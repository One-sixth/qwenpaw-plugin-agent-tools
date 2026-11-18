# -*- coding: utf-8 -*-
"""Agent Tools 公共工具函数。

提供会话上下文定位、SDK ToolResult 结果封装、chat_id 形态校验与一步自愈、
身份前缀构造、本地时间+偏移格式化等工具共享的底层能力。
"""

import logging
import re
from contextvars import ContextVar
from datetime import datetime
from typing import Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────

# chat_id：UUID4 形态（宿主 get_or_create_chat 生成，全局唯一持久不变）
CHAT_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# session_id 三种形态（宽松匹配，仅用于「看起来像 session_id」判定）：
#   主会话  {毫秒ts}-{8字符}
#   spawn 子会话  sub-{8位hex}
#   跨 agent  {from}:to:{to}:{ts}:{uuid8}（实测样例
#     default:to:demo-agent:1793214532425:1794972e —— ts 与 uuid8 冒号分隔）
SESSION_ID_RES = (
    re.compile(r"^\d{12,16}-[0-9a-zA-Z]{4,12}$"),
    re.compile(r"^sub-[0-9a-fA-F]{6,12}$"),
    re.compile(r"^.+:to:.+:\d{12,16}:[0-9a-fA-F]{4,12}$"),
)


# ── 会话上下文定位 ────────────────────────────────────────

# SDK ToolContext 注入点：plugin._wrap 每次工具调用 set/reset；
# get_current_context 优先读它（agent_id=workspace_id、session_id 与内核
# contextvar 同源，qwenpaw.plugins 公共面）。user_id 无公共对应物，
# 注入路径下仍从内核 API 补齐。
_CALL_CTX: ContextVar = ContextVar("agent_tools_call_ctx", default=None)


def get_current_context() -> Tuple[str, str, str]:
    """获取当前工具调用的 (agent_id, user_id, session_id) 上下文。

    优先读 SDK ToolContext 注入（_CALL_CTX）；无注入（TestHost / 直调
    工具函数）时读 QwenPaw 内核 contextvar（由 ContextVarsSetupHook 每次
    请求注入）。取不到时回落为 "default"。身份前缀、notice 唤醒身份、
    防误用判定、context_read 当前会话定位全部依赖本函数。
    """
    ctx = _CALL_CTX.get()
    if ctx is not None:
        agent_id = str(ctx.workspace_id or "default")
        session_id = str(ctx.session_id or "default")
        user_id = "default"
        try:
            from qwenpaw.app.agent_context import get_current_user_id

            user_id = str(get_current_user_id() or "default")
        except Exception:  # noqa: BLE001
            pass
        return agent_id, user_id, session_id
    try:
        from qwenpaw.app.agent_context import (
            get_current_agent_id,
            get_current_session_id,
            get_current_user_id,
        )

        agent_id = get_current_agent_id() or "default"
        session_id = get_current_session_id() or "default"
        user_id = get_current_user_id() or "default"
        return str(agent_id), str(user_id), str(session_id)
    except Exception:  # noqa: BLE001
        return "default", "default", "default"


def current_channel() -> str:
    """读当前工具调用上下文的频道名（contextvar），取不到回落 "console"。

    notice 唤醒身份注册时捕获用；非 console 频道走信使路由。
    """
    try:
        from qwenpaw.app.agent_context import get_current_channel

        return str(get_current_channel() or "console")
    except Exception:  # noqa: BLE001
        return "console"


# ── 工具结果封装（SDK ToolResult） ────────────────────────

_ToolResult = None


def _import_toolresult() -> None:
    """懒加载 SDK ToolResult（qwenpaw 2.3.0+ plugins.tools 公共面）。

    旧路径 ToolChunk 已废弃——SDK invoke wrapper 只接受 ToolResult；
    迁移后统一从这里出结果（字段兼容：content[0].text 同名可访问）。
    """
    global _ToolResult
    if _ToolResult is not None:
        return
    from qwenpaw.plugins.tools import ToolResult

    _ToolResult = ToolResult


def make_success(text: str) -> "ToolResult":
    """构建成功结果 ToolResult。

    Args:
        text: 显示文本内容
    """
    _import_toolresult()
    return _ToolResult.text(text)


def format_error(action: str, reason: str = "", suggestion: str = "") -> str:
    """三段式错误：操作失败 + 原因 + 建议。"""
    parts = [f"{action}失败" + (f"：{reason}" if reason else "")]
    if suggestion:
        parts.append(suggestion)
    return "\n".join(p for p in parts if p)


def make_error(text: str, reason: str = "", suggestion: str = "") -> "ToolResult":
    """构建错误结果 ToolResult。支持两种调用方式：
    - make_error("错误文本")
    - make_error("操作名", "原因", "建议")  # 自动调用 format_error 拼接

    Args:
        text: 错误文本，或 format_error 的 action 参数
        reason: 当提供时，与 text 一起传给 format_error
        suggestion: 当提供时，传给 format_error
    """
    _import_toolresult()
    if reason:
        text = format_error(text, reason, suggestion)
    return _ToolResult.text(text, is_error=True)


# ── chat_id 形态校验 + 一步自愈 ───────────────────────────


def looks_like_session_id(value: str) -> bool:
    """判断传入值是否为 session_id 形态（主会话 / sub 子会话 / 跨 agent）。"""
    return any(p.fullmatch(value) for p in SESSION_ID_RES)


SHORT_ID_RE = re.compile(r"^[0-9a-fA-F]{8}$")


def resolve_chat_reference(
    value: str,
    known_ids: Optional[Iterable[str]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """校验 chat_id 形态（2026-11-17 作者拍板：只支持 chat_id UUID，
    不再支持 session_id 一步自愈——自愈路径依赖插件簿记，官方会话/
    主会话不在簿记恒拒绝，形态语义混乱；统一只收 UUID 主键）。

    所有收 chat_id 的工具入口统一走本函数（设计 §3）：
    - 完整 UUID → 原样返回
    - 短 id（**严格为长 id 第一段**，恰好 8 位 hex）→ 在 known_ids
      （簿记已知 chat_id）内前缀匹配：命中 1 条 → 返回完整 id；
      命中 ≥2 条 → 显式歧义报错（git commit hash 同款，禁止静默
      取第一个——32 bit 空间体量大后存在撞对可能）；0 条 → 无匹配
    - session_id 形态 / 其他 → 报错；session_id 形态给专门提示帮助
      调用方自纠

    Args:
        value: 调用方传入的 chat_id 参数原始值
        known_ids: 簿记已知 chat_id 全集（check_chat_id 传入）；None
                   时短 id 无匹配（纯形态校验场景）

    Returns:
        (chat_id_or_None, error_or_None)
    """
    value = str(value).strip()
    if not value:
        return None, "chat_id 不能为空"
    if CHAT_ID_RE.fullmatch(value):
        return value.lower(), None
    hint = "可用 agent_tools_list_session 查看已知会话（行内为完整 chat_id）"
    if SHORT_ID_RE.fullmatch(value):
        short = value.lower()
        matches = sorted(
            cid for cid in (known_ids or ())
            if str(cid).lower().startswith(short)
        )
        if len(matches) == 1:
            return matches[0], None
        if len(matches) >= 2:
            preview = "、".join(m[:13] + "…" for m in matches[:3])
            more = f" 等 {len(matches)} 个" if len(matches) > 3 else ""
            return None, (
                f"短 id「{value}」存在多个重复（{preview}{more}），"
                "请给完整的长 id（UUID）"
            )
        return None, (
            f"短 id「{value}」无匹配会话（短 id = chat_id 第一段 8 位"
            f"hex）；{hint}"
        )
    if looks_like_session_id(value):
        return None, (
            f"chat_id 无效：「{value}」是 session_id 形态，本工具只接受 "
            f"chat_id（UUID，见 spawn 返回的 [CHAT_ID] 行；也支持其"
            f"第一段 8 位 hex 作为短 id）；{hint}"
        )
    return None, f"chat_id 无效：「{value}」不符合 UUID 形态；{hint}"


# ── 身份前缀（设计 §4.5 定稿文案，2026-11-17 作者拍板改版） ──


async def resolve_parent_chat_id(
    workspace, agent_id: str, session_id: str,
) -> Optional[str]:
    """反查发起者会话的宿主 chat_id（UUID），供身份前缀回访指引。

    走宿主 resolver（chats 表按 session_id 匹配）。发起者必在 chats
    表——主会话 / 官方 task session / 插件子会话三类全记录（2026-11-17
    作者拍板 + 数据实锤 269 条），正常路径恒成功；反查失败即宿主异常
    → WARNING 日志留痕 + None（前缀退短文案，消息仍送达——不因回访
    地址缺失阻塞消息，消息不丢优先）。
    """
    if not session_id:
        return None
    try:
        from qwenpaw.config.utils import load_config
        from qwenpaw.app.chats.resolver import find_chat_matches

        matches = await find_chat_matches(load_config(), session_id, agent_id)
    except Exception:  # noqa: BLE001  导入/配置异常 → 日志留痕，不阻塞消息
        logger.warning(
            "发起者 chat_id 反查异常，前缀退短文案（agent=%s，"
            "session=%s）", agent_id, session_id, exc_info=True,
        )
        return None
    try:
        for m in matches:
            if m.chat and m.chat.id:
                return str(m.chat.id)
    except Exception:  # noqa: BLE001  匹配结果形态异常 → 同上防御
        logger.warning(
            "发起者 chat_id 反查结果解析异常（agent=%s，session=%s）",
            agent_id, session_id, exc_info=True,
        )
        return None
    logger.warning(
        "resolver 无匹配：发起者 session 不在 chats 表，属异常状态"
        "（agent=%s，session=%s）", agent_id, session_id,
    )
    return None


def build_identity_prefix(
    agent_name: str, agent_id: str, chat_id: Optional[str] = None,
) -> str:
    """构造 spawn / communicate send 共用的中文身份前缀。

    chat_id = 发起者会话的宿主 chat_id（UUID，resolve_parent_chat_id
    反查）——给出时附回访指引（UUID 全文只出现一次，第二次用「这个
    chat_id」就近指代）；为 None 时降级为无回访指引的短文案。
    """
    head = (
        f"[系统提示：本回合请求来自 Agent「{agent_name}」"
        f"（agent_id: {agent_id}"
    )
    if chat_id:
        head += f"，chat_id: {chat_id}"
    head += "）。"
    body = "你的回复对方会读取到，本回合无需再向对方发消息"
    if chat_id:
        body += (
            "；之后要主动联系对方，就用这个 chat_id 调 "
            "agent_tools_communicate 发送"
        )
    return head + body + "]"


# ── 时间格式化（决策 12：本地时间+偏移） ──────────────────


def _fmt_offset(td) -> str:
    """timedelta 时区偏移 → +HH:MM / -HH:MM。"""
    total = int(td.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    return f"{sign}{hours:02d}:{minutes:02d}"


def format_local_offset(value) -> str:
    """把 naive 本地时间（datetime 或 ISO 字符串）格式化为 本地时间+偏移。

    输出形态：2026-11-16 11:12:45.102080+08:00（微秒非零时保留 6 位）。
    源数据为宿主 session 文件的 created_at（naive 本地时间，ISO-T 微秒）；
    偏移取当前系统时区（宿主与插件同机同时区）。带 tzinfo 的输入防御性
    转本地 naive 再拼偏移。解析失败时原样返回字符串。
    """
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if not isinstance(value, datetime):
        return str(value)
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    offset = _fmt_offset(datetime.now().astimezone().utcoffset())
    text = value.strftime("%Y-%m-%d %H:%M:%S")
    if value.microsecond:
        text += f".{value.microsecond:06d}"
    return f"{text}{offset}"


# ── chat_id → 会话定位（簿记优先，resolver 兜底） ─────────


def resolve_chat_id_input(value, ) -> "tuple[str, str, str | None]":
    """chat_id 参数入口校验（只认 UUID）→ (chat_id, error, healed_record)。

    短 id 无匹配时触发 meta 恢复（v0.1.8）：扫各 agent workspace 的
    chats.json 回填身份簿记后重解析——重启/热装清簿记后短 id 依然可用。
    回填即缓存：恢复过的记录进内存簿记，同 miss 不重复扫。
    """
    try:
        from .manager import get_manager
    except ImportError:  # 兼容 pytest 直接以项目根导入
        from manager import get_manager

    mgr = get_manager()
    chat_id, err = resolve_chat_reference(
        str(value or ""),
        [r.chat_id for r in mgr.all_records()],
    )
    if err and "无匹配" in err:  # 仅短 id 无匹配值得恢复（歧义/形态错不需要）
        try:
            from .persistence import restore_ledger_from_chats
        except ImportError:  # 兼容 pytest 直接以项目根导入
            from persistence import restore_ledger_from_chats
        if restore_ledger_from_chats(mgr) > 0:
            chat_id, err = resolve_chat_reference(
                str(value or ""),
                [r.chat_id for r in mgr.all_records()],
            )
    if err:
        return "", err, None
    healed = mgr.get_record(chat_id)
    return chat_id, None, healed


async def locate_chat(chat_id: str):
    """chat_id → 定位对象（LocTarget）。

    簿记 SpawnRecord 优先（agent/session/user/channel 四元组齐全）；
    无簿记 → find_chat_matches 全局 resolver 兜底（重启/热重载后簿记
    丢失场景，设计 §2.2/§7）。找不到 → None。
    """
    from dataclasses import dataclass as _dc

    @_dc
    class _Loc:
        agent_id: str
        session_id: str
        user_id: str
        channel: str
        chat_id: str
        book: object = None   # SpawnRecord | None

    try:
        from .manager import get_manager
    except ImportError:  # 兼容 pytest 直接以项目根导入
        from manager import get_manager
    manager = get_manager()

    book = manager.get_record(chat_id)
    if book is not None:
        return _Loc(
            agent_id=book.agent_id, session_id=book.session_id,
            user_id=book.user_id, channel=book.channel,
            chat_id=chat_id, book=book,
        )

    try:
        from qwenpaw.config.utils import load_config
        from qwenpaw.app.chats.resolver import find_chat_matches
    except Exception:  # noqa: BLE001
        logger.warning("resolver 导入失败", exc_info=True)
        return None

    current_agent, _, _ = get_current_context()
    try:
        matches = await find_chat_matches(load_config(), chat_id, current_agent)
    except Exception:  # noqa: BLE001
        logger.warning("find_chat_matches 失败（chat=%s）", chat_id[:8],
                       exc_info=True)
        return None
    if len(matches) != 1:
        return None  # 0=未找到；>1=歧义（对齐 /resolve 端点 409 语义）
    resolution = matches[0]
    spec = resolution.chat
    return _Loc(
        agent_id=resolution.agent_id,
        session_id=spec.session_id,
        user_id=spec.user_id,
        channel=spec.channel,
        chat_id=chat_id,
        book=None,
    )


# ── 小工具 ────────────────────────────────────────────────


def truncate_text(text: str, limit: int, marker: str = "…") -> str:
    """超长截断（字符数），附省略标记。"""
    if len(text) <= limit:
        return text
    return text[:limit] + marker
