# -*- coding: utf-8 -*-
"""context_read / context_export 共用逻辑：idx 映射 + 块时间线渲染。

idx 体系（设计 §3.5，时间线连续，负=更早）：
- 可见区轮（宿主归一化 classify_messages）：idx >= 0，0 = 可见区最旧轮
- Scroll 驱逐轮（hist.conversation_history，每行一个 turn）：idx < 0，
  -1 = 紧邻可见区最近的驱逐轮，-E = 最早；映射 s=0..E-1 → idx = s - E
- start=None 默认读可见区最近 count 轮；负 start = 从末轮倒数第 |start| 轮起

Markdown 块时间线（设计 §3.6）：按块粒度平铺，分隔线 + [块类型] 时间戳 +
内容；tool_call 函数形式不截断；tool_result 所见即所得；hint 恒不导出。
"""

import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional

try:
    from .manager import get_manager
    from .utils import format_local_offset
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import get_manager
    from utils import format_local_offset

logger = logging.getLogger(__name__)

# 单次读取轮数上限（参数超限自动降档，作者偏好）
READ_TURN_LIMIT = 50

# 驱逐历史单次读取行上限（绕开 MemorySpace 默认 1000 行 row_cap——
# 长会话驱逐行可能超千，复审 B-M2；仍超则丢弃最旧并记日志）
_EVICTED_ROW_CAP = 100000

# 驱逐行 kind（scroll LogEntry，serialize.py 写入端）
_KIND_MODEL_TURN = "model_turn"
_KIND_CONTEXT_MSG = "context_msg"
_KIND_TOOL_RESULT = "tool_result"


@dataclass
class EvictedRow:
    """一条驱逐历史行（turn 粒度）。"""

    seq: int
    idx: int                  # 负 idx（s - E）
    kind: str
    role: str
    content: str = ""
    created_at: str = ""
    blocks: list = field(default_factory=list)
    tool_name: str = ""
    tool_state: str = ""


# ── 驱逐历史（Scroll history DB 只读） ────────────────────


def history_path(workspace) -> Optional[str]:
    """目标 workspace 的 history DB 路径；agent 未启用 scroll → None。"""
    try:
        from qwenpaw.app.workspace.service_factories import history_service_path

        path = history_service_path(workspace)
        return str(path) if path else None
    except Exception:  # noqa: BLE001
        logger.debug("history_service_path 获取失败", exc_info=True)
        return None


def count_evicted_rows(workspace, agent_id: str, session_id: str
                       ) -> Optional[int]:
    """轻量计数驱逐行（SELECT COUNT，不拉行内容——R-A #4：全量读仅为
    len 太重）；None = 未启用 history DB。

    注意：hist.conversation_history 是全量日志，**包含仍在可见区的轮**
    （2026-11-17 作者拍板：驱逐区与可见区重叠符合预期，无需除重）——
    计数与 fetch_evicted_rows 同口径。
    """
    db_path = history_path(workspace)
    if not db_path:
        return None
    try:
        from qwenpaw.agents.context.scroll.memoryspace import MemorySpace

        ms = MemorySpace(
            history_db_path=db_path,
            session_id=session_id,
            agent_id=agent_id,
            row_cap=_EVICTED_ROW_CAP,
        )
        try:
            rows = ms.sql_query(
                "SELECT COUNT(*) AS n "
                "FROM hist.conversation_history "
                "WHERE session_id = ? AND (agent_id = ? OR agent_id IS NULL)",
                (session_id, agent_id),
            )
        finally:
            ms.close()
        if rows and isinstance(rows[0], dict):
            return int(rows[0].get("n") or 0)
        return 0
    except Exception:  # noqa: BLE001
        logger.warning("驱逐历史计数失败（session=%s）", session_id[:30],
                       exc_info=True)
        return -1  # 哨兵：读取失败（区别 None=未启用；复审 R-A #5）


def fetch_evicted_rows(workspace, agent_id: str, session_id: str
                       ) -> Optional[List[EvictedRow]]:
    """读取该会话的全部驱逐轮（seq 升序）；未启用 → None。

    每次调用新建 MemorySpace（官方工厂同款：read-only ATTACH，读完
    close，不泄漏连接）。

    语义注记（2026-11-17 作者拍板）：hist.conversation_history 是
    **全量日志**，含仍在可见区的轮——查询结果与可见区内容重叠是
    **预期行为**，无需按可见区除重；负索引/导出按本口径展示。
    """
    db_path = history_path(workspace)
    if not db_path:
        return None
    try:
        import json as _json

        from qwenpaw.agents.context.scroll.memoryspace import MemorySpace

        ms = MemorySpace(
            history_db_path=db_path,
            session_id=session_id,
            agent_id=agent_id,
            row_cap=_EVICTED_ROW_CAP,   # 复审 B-M2：绕开默认 1000 行截断
        )
        try:
            rows = ms.sql_query(
                "SELECT seq, kind, role, name, content, headline, blocks, "
                "tool_state, metadata, created_at "
                "FROM hist.conversation_history "
                "WHERE session_id = ? AND (agent_id = ? OR agent_id IS NULL) "
                "ORDER BY seq",
                (session_id, agent_id),
            )
        finally:
            ms.close()
        # 过滤 row_cap 溢出标记行（复审 B-M2：_truncated 是 dict 且会
        # 混进结果尾部，不能当作驱逐轮参与 idx 映射）；命中即说明最旧
        # 行已被宿主截断丢弃——记日志留痕（R1 #4：此前静默少报）
        truncated_n = sum(
            1 for r in rows if isinstance(r, dict) and r.get("_truncated")
        )
        rows = [r for r in rows
                if isinstance(r, dict) and not r.get("_truncated")]
        if truncated_n:
            logger.warning(
                "驱逐历史超出 row_cap，最旧 %d 行已被宿主截断丢弃（可用"
                "范围少报同量）", truncated_n,
            )
        total = len(rows)
        evicted: List[EvictedRow] = []
        for s, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            # blocks 列在 DB 中是 JSON 字符串（写入端 _to_json，
            # 复审 B-M1）——解析失败降级空列表
            blocks = row.get("blocks") or []
            if isinstance(blocks, str):
                try:
                    blocks = _json.loads(blocks)
                except (ValueError, TypeError):
                    blocks = []
            evicted.append(EvictedRow(
                seq=int(row.get("seq") or 0),
                idx=s - total,          # s=0..E-1 → idx=s-E（-1=最新驱逐轮）
                kind=str(row.get("kind") or ""),
                role=str(row.get("role") or ""),
                content=str(row.get("content") or ""),
                created_at=str(row.get("created_at") or ""),
                blocks=blocks if isinstance(blocks, list) else [],
                tool_name=str(row.get("name") or ""),
                tool_state=str(row.get("tool_state") or ""),
            ))
        return evicted
    except Exception:  # noqa: BLE001
        logger.warning("驱逐历史读取失败（session=%s）", session_id[:30],
                       exc_info=True)
        return None


# ── 时间线构建 ────────────────────────────────────────────


async def build_timeline(workspace, agent_id: str, session_id: str,
                         user_id: str, channel: str,
                         include_compressed: bool):
    """构建完整时间线：可见区轮列表 + 驱逐行列表。

    Returns:
        (turns, evicted) — turns 为 session_reader.classify_messages 的
        轮列表；evicted 为升序驱逐行列表（None=未启用/未读取）。
    """
    try:
        from . import session_reader
    except ImportError:  # 兼容 pytest 直接以项目根导入
        import session_reader

    messages = await session_reader.fetch_normalized_messages(
        workspace, session_id, user_id, channel,
    )
    turns = session_reader.classify_messages(messages)
    evicted = None
    if include_compressed:
        evicted = fetch_evicted_rows(workspace, agent_id, session_id)
    return turns, evicted


def resolve_read_indices(turns_total: int, evicted: Optional[List[EvictedRow]],
                         start: Optional[int], count: int):
    """解析 start/count → (起始 idx, 实际读取轮数, 可用范围描述)。

    全时间线：idx ∈ [-E, v_hi]（E=驱逐轮数，v_hi=可见区末轮 idx）。
    - start=None → 可见区最近 count 轮；可见区空 → 驱逐区最近 count 轮
    - start>=0 → 从可见区 idx=start 起（超末尾降到最后可用轮）
    - start<0 → 从末轮倒数第 |start| 轮起（可跨入驱逐区，下界 -E）
    - count 超上限自动降档；负 idx 轮仅 include_compressed=True 可读
      （由调用方保证——include_compressed=False 时 evicted 为 None，
      下界自动钳到 0）
    """
    count = max(1, min(int(count or 5), READ_TURN_LIMIT))
    v_hi = turns_total - 1
    e_total = len(evicted) if evicted else 0
    lo_bound = -e_total if e_total else 0

    if start is None:
        s_idx = max(lo_bound, v_hi - count + 1)
    else:
        start = int(start)
        if start >= 0:
            s_idx = min(max(start, 0), max(v_hi, 0))
        else:
            s_idx = max(v_hi + start + 1, lo_bound)
    length = min(count, max(0, v_hi - s_idx + 1))
    # 空会话（v_hi=-1）显示「无可用轮」而非误导性的「0~-1」（复审 A-L5）
    avail = (
        f"-{e_total}~{v_hi}" if e_total
        else f"0~{v_hi}" if v_hi >= 0
        else "无可用轮"
    )
    return s_idx, length, avail


def describe_range(s_idx: int, length: int, avail: str,
                   compressed_enabled: bool) -> str:
    """结果开头的索引信息行（格式消歧义，设计 §3.5 定稿文案）。"""
    if length <= 0:
        head = f"本次读取 idx {s_idx}（共 0 轮）"
    elif length == 1:
        head = f"本次读取 idx {s_idx}（共 1 轮）"
    else:
        head = f"本次读取 idx {s_idx}~{s_idx + length - 1}（共 {length} 轮）"
    if compressed_enabled:
        return f"{head}｜可用范围 {avail}（负=驱逐历史，需 include_compressed=True；正=当前可见区）"
    return f"{head}｜可用范围 {avail}（未启用驱逐历史，include_compressed=True 可读负索引）"


# ── 块时间线渲染 ──────────────────────────────────────────


def _format_arg_value(value) -> str:
    """函数形式参数值：字符串加引号、数字/布尔/null 原样、list/dict 紧凑 JSON。"""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        escaped = escaped.replace("\n", "\\n").replace("\r", "\\r")
        return f'"{escaped}"'
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def render_tool_call(name: str, arguments) -> str:
    """tool_call 函数形式渲染：name(param=value, ...)，不截断原样输出。

    arguments 兼容 JSON 字符串与 dict 双形态（宿主归一化同口径）。
    """
    unparsed = None
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments)
        except (json.JSONDecodeError, ValueError):
            args, unparsed = None, arguments
    else:
        args = arguments
    if isinstance(args, dict) and args:
        inner = ", ".join(
            f"{k}={_format_arg_value(v)}" for k, v in args.items()
        )
        return f"{name}({inner})"
    if unparsed:
        return f"{name}(args={_format_arg_value(unparsed)})"
    return f"{name}()"


def _block_line(kind: str, timestamp: str, content: str) -> str:
    """一块的时间线段：分隔线 + [类型] 时间戳 + 内容。"""
    ts = format_local_offset(timestamp) if timestamp else "（无时间戳）"
    return f"————————\n[{kind}] {ts}\n{content}"


def render_turn(turn, include_think: bool, include_tool: bool,
                include_monologue: bool) -> List[str]:
    """可见区单轮 → 块时间线段列表（user 与正文恒可见）。"""
    sections: List[str] = []
    for block in turn.blocks:
        kind = block.kind
        if kind == "user":
            sections.append(_block_line("user", block.timestamp, block.text))
        elif kind == "body":
            sections.append(_block_line("assistant", block.timestamp, block.text))
        elif kind == "monologue":
            if include_monologue:
                sections.append(_block_line("monologue", block.timestamp, block.text))
        elif kind == "think":
            if include_think:
                sections.append(_block_line("think", block.timestamp, block.text))
        elif kind == "tool_call":
            if include_tool:
                sections.append(_block_line(
                    "tool_call", block.timestamp,
                    render_tool_call(block.tool_name, block.tool_payload),
                ))
        elif kind == "tool_result":
            if include_tool:
                sections.append(_block_line(
                    "tool_result", block.timestamp, block.tool_payload,
                ))
        elif kind == "media":
            sections.append(_block_line("media", block.timestamp, block.text))
    return sections


def render_evicted_row(row: EvictedRow, include_think: bool,
                       include_tool: bool) -> List[str]:
    """驱逐行 → 块时间线段列表（kind 行级还原，标注负 idx）。

    - kind=tool_result → [tool_result]
    - kind=model_turn → blocks 展开（think/text/tool_call），无 blocks
      则 [assistant] + content
    - kind=context_msg → [user]（role=user）或 [assistant]
    """
    label = f"（seq={row.seq}，驱逐 idx={row.idx}）"
    sections: List[str] = []
    if row.kind == _KIND_TOOL_RESULT:
        sections.append(_block_line(
            f"tool_result{label}", row.created_at,
            f"{row.tool_name}：{row.content}" if row.tool_name else row.content,
        ))
        return sections
    if row.kind == _KIND_MODEL_TURN and row.blocks:
        has_block = False
        for block in row.blocks or []:
            if not isinstance(block, dict):
                continue
            btype = str(block.get("type") or "")
            if btype == "thinking" and include_think:
                has_block = True
                sections.append(_block_line(
                    f"think{label}", row.created_at,
                    str(block.get("thinking") or ""),
                ))
            elif btype == "tool_call" and include_tool:
                has_block = True
                sections.append(_block_line(
                    f"tool_call{label}", row.created_at,
                    render_tool_call(
                        str(block.get("name") or ""),
                        block.get("input"),
                    ),
                ))
            elif btype == "text":
                has_block = True
                sections.append(_block_line(
                    f"assistant{label}", row.created_at,
                    str(block.get("text") or ""),
                ))
        if not has_block:
            sections.append(_block_line(
                f"assistant{label}", row.created_at, row.content,
            ))
        return sections
    # context_msg / 无块 model_turn：行级直接渲染
    kind = "user" if (row.kind == _KIND_CONTEXT_MSG and row.role == "user") \
        else "assistant"
    sections.append(_block_line(
        f"{kind}{label}", row.created_at, row.content,
    ))
    return sections


def assemble_markdown(sections: List[str]) -> str:
    """段列表 → 完整 Markdown 文档（块之间空行分隔）。"""
    return "\n\n".join(s for s in sections if s)
