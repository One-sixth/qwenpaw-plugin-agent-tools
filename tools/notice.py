# -*- coding: utf-8 -*-
"""agent_tools_notice — 注册子会话完成通知（设计 §3.4）。

completed/failed/cancelled 三种终态全部触发；仅会话空闲时投递
（process-tools 行为契约同款）。注册时目标已终态 → 不注册，仅返回「目标会话未注册完成通知」。
"""

import logging

try:
    from ..manager import (
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
    from .common import check_chat_id, chunk_text, normalize_chat_id_list
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import (
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
    from tools.common import check_chat_id, chunk_text, normalize_chat_id_list

logger = logging.getLogger(__name__)


async def _notice_one(chat_id, cancel: bool | str | int = False):
    """注册子会话完成通知（每回合 completed/failed/cancelled 均触发）。通知在你停手（回合空闲）时投递，连续调工具期间暂存、停手补发。

    Args:
        chat_id: 子会话 id（完整 UUID，也支持其第一段 8 位 hex 短 id）。
        cancel: True=取消注册并撤回待发通知。
    """
    do_cancel = _to_bool(cancel)
    cid, err, healed = check_chat_id(chat_id)
    if err:
        return make_error(
            "目标会话未找到，取消失败" if do_cancel
            else "目标会话未找到，注册失败"
        )

    manager = get_manager()
    notifier = get_notifier()

    if do_cancel:
        if await locate_chat(cid) is None:
            return make_error("目标会话未找到，取消失败")
        notifier.cancel(cid)
        return make_success("完成通知已取消")

    prior = manager.get_notice(cid)
    if (prior is not None and not prior.cancelled
            and not prior.completion_sent):
        return make_success("完成通知已注册")
    # completion_sent=True 的陈旧记录不再拦截（复审 A-M1 实锤：投递后
    # 记录滞留 24h TTL，旧检查导致「已注册（幂等）」文案但实际已消费，
    # 新回合收不到通知且与「续聊后可重新注册」矛盾）——落到正常注册，
    # register_notice 按 key 覆盖

    # 目标状态检查
    target = await locate_chat(cid)
    if target is None:
        return make_error("目标会话未找到，注册失败")
    workspace = get_workspace(target.agent_id)
    if workspace is None:
        return make_error("目标会话未找到，注册失败")

    book = target.book or healed
    turn = book.latest_turn if book is not None else None
    tracker = workspace.task_tracker
    status = await tracker.get_status(cid)

    # 注册时目标已终态 → 不注册，仅回一句文案
    if turn is not None and turn.is_terminal:
        return make_success("目标会话未注册完成通知")
    if turn is None and status == "idle":
        return make_success("目标会话未注册完成通知")

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
        return make_success("完成通知已注册")
    return make_success("完成通知已注册")


async def agent_tools_notice(chat_id, cancel: bool | str | int = False):
    """注册一个或多个子会话的完成通知（每回合 completed/failed/cancelled 均触发）。
    通知在你停手（回合空闲）时投递，连续调工具期间暂存、停手补发。

    chat_id 可为单个字符串，或多个字符串组成的数组 / JSON 字符串数组。
    多个 id：存在的一个个注册 / 取消，不存在的逐个返回「id 不存在」，不整单失败。
    """
    ids, err = normalize_chat_id_list(chat_id)
    if err:
        return make_error("注册通知", err)
    if len(ids) == 1:
        return await _notice_one(ids[0], cancel)
    do_cancel = _to_bool(cancel)
    blocks, all_err = [], True
    for raw in ids:
        cid_, err_, _healed = check_chat_id(raw)
        full = cid_ if (not err_ and cid_) else raw
        try:
            ch = await _notice_one(raw, cancel)
        except Exception:  # noqa: BLE001
            blocks.append(
                f"chat_id={full}\n"
                + ("目标会话未找到，取消失败" if do_cancel
                   else "目标会话未找到，注册失败")
            )
            continue
        all_err = all_err and bool(getattr(ch, "is_error", False))
        blocks.append(f"chat_id={full}\n{chunk_text(ch)}")
    joined = "\n\n".join(blocks)
    return make_error(joined) if all_err else make_success(joined)


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("true", "1", "yes", "y")
