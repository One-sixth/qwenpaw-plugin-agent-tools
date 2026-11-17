# -*- coding: utf-8 -*-
"""agent_tools_context_export — 全量导出会话上下文为 Markdown（设计 §3.6）。"""

import logging
from pathlib import Path

try:
    from ..context import (
        assemble_markdown,
        build_timeline,
        count_evicted_rows,
        render_evicted_row,
        render_turn,
    )
    from ..submit import get_workspace
    from ..utils import (
        current_channel,
        get_current_context,
        locate_chat,
        make_error,
        make_success,
    )
    from .common import check_chat_id
except ImportError:  # 兼容 pytest 直接以项目根导入
    from context import (
        assemble_markdown,
        build_timeline,
        count_evicted_rows,
        render_evicted_row,
        render_turn,
    )
    from submit import get_workspace
    from utils import (
        current_channel,
        get_current_context,
        locate_chat,
        make_error,
        make_success,
    )
    from tools.common import check_chat_id

logger = logging.getLogger(__name__)

OUTPUT_PATH_ERROR = (
    "ERROR: output_path 必填（导出 Markdown 路径）。"
    "建议写 <workspace>\\memory\\context_export_<chat_id前8位>.md。"
)


async def agent_tools_context_export(
    chat_id: str | None = None,
    output_path: str | None = None,
    include_think: bool | str | int = False,
    include_tool: bool | str | int = False,
    include_monologue: bool | str | int = False,
    include_compressed: bool | str | int = False,
):
    """把会话上下文导出为 Markdown 块时间线文件，返回路径。

    Args:
        chat_id: 目标会话 id（完整 UUID 或其第一段 8 位 hex 短 id）；不填=当前会话。
        output_path: 导出 Markdown 路径（必填，相对工作区）。
        include_think: 包含思考块。
        include_tool: 包含工具调用与结果块。
        include_monologue: 包含自言自语块。
        include_compressed: 包含被压缩的历史轮。
    """
    output_path = str(output_path or "").strip()
    if not output_path:
        return make_error(OUTPUT_PATH_ERROR)

    if chat_id is None or str(chat_id).strip() == "":
        agent_id, user_id, session_id = get_current_context()
        channel = current_channel()   # 复审 B-M4：非 console 频道读自身目录
        cid = ""
        workspace = get_workspace(agent_id)
        if workspace is None:
            return make_error("导出上下文", "当前 agent 的 workspace 未运行")
    else:
        cid, err, healed = check_chat_id(chat_id)
        if err:
            return make_error("导出上下文", err)
        target = await locate_chat(cid)
        if target is None:
            return make_error(
                "导出上下文",
                f"找不到会话 {cid}",
                "用 agent_tools_list_session 查看已知会话",
            )
        agent_id, session_id, user_id = (
            target.agent_id, target.session_id, target.user_id,
        )
        channel = target.channel
        workspace = get_workspace(agent_id)
        if workspace is None:
            return make_error(
                "导出上下文", f"agent '{agent_id}' 的 workspace 未运行",
            )

    think, tool, mono, comp = (
        _flag(include_think), _flag(include_tool),
        _flag(include_monologue), _flag(include_compressed),
    )
    turns, evicted = await build_timeline(
        workspace, agent_id, session_id, user_id, channel, comp,
    )
    # 计数与渲染彻底分离（R-A #1：此前非导出模式复用 evicted 变量，
    # 渲染守卫 `if evicted:` 放行导致驱逐内容仍被写进导出文件）；
    # 非导出模式用轻量 COUNT（R-A #4：全量读仅为 len 太重）
    if comp:
        evicted_n = len(evicted) if evicted else 0
        evicted_label = f"驱逐历史 {evicted_n} 轮（已含）"
    else:
        counted = count_evicted_rows(workspace, agent_id, session_id)
        if counted is None:
            evicted_label = "驱逐历史 未启用"
        elif counted < 0:
            evicted_label = "驱逐历史 读取失败（详情见宿主日志）"
        elif counted:
            evicted_label = (
                f"驱逐历史 {counted} 轮未导出（include_compressed=true 可含）"
            )
        else:
            evicted_label = "驱逐历史 0 轮"

    sections = [f"# 会话上下文导出：{session_id}",
                f"- agent：{agent_id}｜可见区 {len(turns)} 轮｜"
                f"{evicted_label}"]
    for turn in turns:
        sections.extend(render_turn(turn, think, tool, mono))
    if comp and evicted:  # 双守卫（R-A #1 防回归：非导出模式绝不渲染驱逐）
        for row in evicted:
            sections.extend(render_evicted_row(row, think, tool))
    content = assemble_markdown(sections)

    out = Path(output_path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return make_error("导出上下文", f"写入失败：{exc!r}")
    return make_success(
        f"已导出：{out}（可见区 {len(turns)} 轮"
        f"{f' + 驱逐 {len(evicted)} 轮' if evicted else ''}，"
        f"{len(content)} 字符）"
    )


def _flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("true", "1", "yes", "y")
