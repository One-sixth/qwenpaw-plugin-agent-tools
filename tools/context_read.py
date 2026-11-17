# -*- coding: utf-8 -*-
"""agent_tools_context_read — 读取会话上下文（设计 §3.5）。"""

import logging

try:
    from ..context import (
        assemble_markdown,
        build_timeline,
        describe_range,
        render_evicted_row,
        render_turn,
        resolve_read_indices,
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
        describe_range,
        render_evicted_row,
        render_turn,
        resolve_read_indices,
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


async def agent_tools_context_read(
    chat_id: str | None = None,
    start: int | str | None = None,
    count: int | str = 5,
    include_think: bool | str | int = False,
    include_tool: bool | str | int = False,
    include_monologue: bool | str | int = False,
    include_compressed: bool | str | int = False,
):
    """读取会话上下文（默认最近数轮），支持正负索引翻页与块过滤开关。

    Args:
        chat_id: 目标会话 id（完整 UUID 或其第一段 8 位 hex 短 id）；不填=当前会话。
        start: 起始轮索引：正=可见区轮（0=最旧），负=压缩历史（-1 最新，越负越旧）；不填=最近 count 轮。
        count: 读取轮数（默认 5，上限 50 自动降档）。
        include_think: 包含思考块。
        include_tool: 包含工具调用与结果块。
        include_monologue: 包含自言自语块。
        include_compressed: 包含被压缩的历史轮。
    """
    if chat_id is None or str(chat_id).strip() == "":
        # 当前会话（当前上下文获取器）
        agent_id, user_id, session_id = get_current_context()
        target = None
        cid = ""
        book = None
        workspace = get_workspace(agent_id)
        if workspace is None:
            return make_error("读取上下文", "当前 agent 的 workspace 未运行")
    else:
        cid, err, healed = check_chat_id(chat_id)
        if err:
            return make_error("读取上下文", err)
        target = await locate_chat(cid)
        if target is None:
            return make_error(
                "读取上下文",
                f"找不到会话 {cid}",
                "用 agent_tools_list_session 查看已知会话",
            )
        agent_id, session_id, user_id = (
            target.agent_id, target.session_id, target.user_id,
        )
        channel = target.channel
        book = target.book or healed
        workspace = get_workspace(agent_id)
        if workspace is None:
            return make_error(
                "读取上下文", f"agent '{agent_id}' 的 workspace 未运行",
            )
    if target is None:
        channel = current_channel()   # 复审 B-M4：非 console 频道会话读自身目录

    turns, evicted = await build_timeline(
        workspace, agent_id, session_id, user_id, channel,
        include_compressed=_flag(include_compressed),
    )

    try:
        count_val = int(count)
    except (TypeError, ValueError):
        count_val = 5
    try:
        start_val = None if start is None else int(start)
    except (TypeError, ValueError):
        start_val = None

    s_idx, length, avail = resolve_read_indices(
        len(turns), evicted, start_val, count_val,
    )
    header = describe_range(
        s_idx, length, avail, evicted is not None,
    )

    sections = [header]
    e_total = len(evicted) if evicted else 0
    for i in range(length):
        idx = s_idx + i
        if idx < 0:
            row = evicted[idx + e_total]
            sections.extend(render_evicted_row(
                row, _flag(include_think), _flag(include_tool),
            ))
        else:
            turn = turns[idx]
            sections.extend(render_turn(
                turn,
                _flag(include_think), _flag(include_tool),
                _flag(include_monologue),
            ))
    body = assemble_markdown(sections)
    return make_success(body if body.strip() else header + "\n（该范围为空）")


def _flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("true", "1", "yes", "y")
