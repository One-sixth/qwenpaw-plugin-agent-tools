# -*- coding: utf-8 -*-
"""agent_tools_notice — 注册子会话完成通知（设计 §3.4）。

completed/failed/cancelled 三种终态全部触发；仅会话空闲时投递
（process-tools 行为契约同款）。注册时目标已终态 → 不注册直接返回终态。
"""

import logging

try:
    from ..manager import (
        STATUS_RUNNING,
        TERMINAL_STATUSES,
        NoticeRecord,
        get_manager,
    )
    from ..notifier import get_notifier
    from ..submit import get_workspace
    from ..utils import (
        current_channel,
        get_current_context,
        locate_chat,
        make_error,
        make_success,
    )
    from .common import check_chat_id, describe_terminal
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import (
        STATUS_RUNNING,
        TERMINAL_STATUSES,
        NoticeRecord,
        get_manager,
    )
    from notifier import get_notifier
    from submit import get_workspace
    from utils import (
        current_channel,
        get_current_context,
        locate_chat,
        make_error,
        make_success,
    )
    from tools.common import check_chat_id, describe_terminal

logger = logging.getLogger(__name__)


async def agent_tools_notice(chat_id: str, cancel: bool | str | int = False):
    """注册子会话完成通知（每回合 completed/failed/cancelled 均触发）。通知在你停手（回合空闲）时投递，连续调工具期间暂存、停手补发。

    Args:
        chat_id: 子会话 id（完整 UUID，也支持其第一段 8 位 hex 短 id）。
        cancel: True=取消注册并撤回待发通知。
    """
    cid, err, healed = check_chat_id(chat_id)
    if err:
        return make_error("注册通知", err)

    manager = get_manager()
    notifier = get_notifier()

    if _to_bool(cancel):
        removed = notifier.cancel(cid)
        return make_success(
            f"已取消：会话 {cid} 的通知注册已撤回"
            f"（移除待发 {removed} 条）。"
        )

    prior = manager.get_notice(cid)
    if (prior is not None and not prior.cancelled
            and not prior.completion_sent):
        return make_success(f"已注册：会话 {cid} 此前已有通知注册（重复注册幂等）。")
    # completion_sent=True 的陈旧记录不再拦截（复审 A-M1 实锤：投递后
    # 记录滞留 24h TTL，旧检查导致「已注册（幂等）」文案但实际已消费，
    # 新回合收不到通知且与「续聊后可重新注册」矛盾）——落到正常注册，
    # register_notice 按 key 覆盖

    # 目标状态检查
    target = await locate_chat(cid)
    if target is None:
        return make_error(
            "注册通知",
            f"找不到会话 {cid}",
            "用 agent_tools_list_session 查看已知会话",
        )
    workspace = get_workspace(target.agent_id)
    if workspace is None:
        return make_error(
            "注册通知", f"agent '{target.agent_id}' 的 workspace 未运行",
        )

    book = target.book or healed
    turn = book.latest_turn if book is not None else None
    tracker = workspace.task_tracker
    status = await tracker.get_status(cid)

    # 注册时目标已终态 → 不注册，直接返回终态 + 正文（引导 wait/recv）
    if turn is not None and turn.is_terminal:
        return make_success(
            "无需注册：目标回合已结束。\n"
            + describe_terminal(turn, target, tracker)
            + "\n（直接用上面的结果即可；续聊后可重新注册）"
        )
    if turn is None and status == "idle":
        return make_success(
            "无需注册：该会话当前空闲且无运行中回合"
            "（重启后对从未运行的会话不发假完成通知）。"
            "可 communicate(chat_id, action='send') 发起新一轮后再注册。"
        )

    # ── 注册（wake_* 身份注册时从当前上下文捕获） ──
    manager.sweep_expired()   # 惰性清理（复审 A-L10：注册入口覆盖）
    wake_agent, wake_user, wake_session = get_current_context()
    record = NoticeRecord(
        target_chat_id=cid,
        target_agent_id=target.agent_id,
        target_session_id=target.session_id,
        wake_agent_id=wake_agent,
        wake_user_id=wake_user,
        wake_session_id=wake_session,
        wake_channel=current_channel(),
        template_label=(book.template if book is not None else ""),
    )
    manager.register_notice(record)

    # 簿记缺失（重启/热重载）→ 启动监视协程兜底（迁移门在协程内；
    # 登记到簿记 + record.watch_task，cancel/卸载钩子可取消——复审 C-H1/C-M1）
    if book is None or turn is None:
        import asyncio

        task = asyncio.create_task(notifier.watch_untracked(record))
        manager.register_task(task)
        record.watch_task = task
        logger.info("通知注册（监视协程兜底）：chat=%s", cid[:8])
        return make_success(
            f"已注册：会话 {cid} 完成时通知（completed/failed/cancelled 均"
            "触发；本会话无簿记，以运行状态监视兜底）。仅在你停手时投递。"
        )
    return make_success(
        f"已注册：会话 {cid} 完成时通知（completed/failed/cancelled 均"
        "触发；结果正文随通知送达）。仅在你停手时投递。"
    )


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("true", "1", "yes", "y")
