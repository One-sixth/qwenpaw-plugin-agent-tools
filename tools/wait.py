# -*- coding: utf-8 -*-
"""agent_tools_wait — 前台等待子会话回合结束并返回最终正文（设计 §3.3）。

wait 语义总纲（决策 2）：前台等待超时 = 只放弃等待，绝不停止目标会话。
"""

import asyncio
import logging

try:
    from ..manager import (
        STATUS_CANCELLED,
        STATUS_COMPLETED,
        STATUS_FAILED,
        STATUS_RUNNING,
        TERMINAL_STATUSES,
        get_manager,
    )
    from ..session_reader import read_latest_turn_body
    from ..submit import get_workspace
    from ..utils import locate_chat, make_error, make_success
    from .common import (check_chat_id, chunk_text, describe_terminal,
                         normalize_chat_id_list, truncate_wait_body)
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import (
        STATUS_CANCELLED,
        STATUS_COMPLETED,
        STATUS_FAILED,
        STATUS_RUNNING,
        TERMINAL_STATUSES,
        get_manager,
    )
    from session_reader import read_latest_turn_body
    from submit import get_workspace
    from utils import locate_chat, make_error, make_success
    from tools.common import (check_chat_id, chunk_text, describe_terminal,
                              normalize_chat_id_list, truncate_wait_body)

logger = logging.getLogger(__name__)


def poll_interval(elapsed: float) -> float:
    """智能轮询节奏（wait_agent_task 同款）：0-10s/1s、10-30s/3s、
    30-60s/5s、60s+/10s。"""
    if elapsed < 10:
        return 1.0
    if elapsed < 30:
        return 3.0
    if elapsed < 60:
        return 5.0
    return 10.0


async def wait_for_terminal(workspace, target, turn, timeout: float,
                            chat_id: str = "") -> "str | None":
    """等待 target（SpawnRecord 或 LocTarget 形态）最新回合终态。

    Args:
        workspace: 目标 workspace（tracker 读取）
        target: 带 session_id/user_id/channel 属性的定位对象
        turn: 跟踪的 TurnRecord（可为 None——无簿记观察模式）
        timeout: 等待上限（秒）；超时不停止目标会话
        chat_id: 供日志/文案

    Returns:
        终态文本（completed 带正文 / failed / cancelled / stale）；
        超时返回 None。
    """
    manager = get_manager()
    tracker = workspace.task_tracker
    loop = asyncio.get_event_loop()
    start = loop.time()
    deadline = start + max(1.0, float(timeout))
    saw_running = False

    while True:
        manager.sweep_expired()
        status = await tracker.get_status(chat_id or target.session_id)

        if turn is not None:
            # 簿记路径：跟随最新 TurnRecord
            if turn.is_terminal:
                return describe_terminal(turn, target, tracker)
            if status == "idle":
                # stale 分支：簿记 running 但 tracker idle（消费者异常
                # 退出未写终态）→ 视为终态从 session 文件读（复审 M4）
                try:
                    body = await read_latest_turn_body(workspace, target)
                except Exception:  # noqa: BLE001
                    body = ""
                suffix = (
                    f"\n—— 结果正文 ——\n{truncate_wait_body(body)}" if body
                    else "\n（会话文件中无正文）"
                )
                return (
                    "状态：completed（簿记与运行状态不一致，"
                    f"正文从会话文件读取）{suffix}"
                )
        else:
            # 无簿记观察模式：迁移门——先观察到 running 再转 idle 才算完成
            if status == "running":
                saw_running = True
            elif saw_running and status == "idle":
                try:
                    body = await read_latest_turn_body(workspace, target)
                except Exception:  # noqa: BLE001
                    body = ""
                return (
                    "状态：completed（无簿记，经迁移门观察确认）\n"
                    f"—— 结果正文 ——\n"
                    f"{truncate_wait_body(body) if body else '（会话文件中无正文）'}"
                )

        if loop.time() >= deadline:
            return None
        await asyncio.sleep(poll_interval(loop.time() - start))


async def _wait_one(chat_id, timeout: int | float | str = 900):
    """前台等待子会话回合结束并返回最终正文（超时不停止目标会话）。

    Args:
        chat_id: 子会话 id（完整 UUID，也支持其第一段 8 位 hex 短 id）。
        timeout: 等待上限（秒），默认 900；超时可续等。
    """
    cid, err, healed = check_chat_id(chat_id)
    if err:
        return make_error("等待会话", err)
    target = await locate_chat(cid)
    if target is None:
        return make_error(
            "等待会话",
            f"找不到会话 {cid}",
            "用 agent_tools_list_session 查看已知会话",
        )

    manager = get_manager()
    workspace = _workspace_of(target)
    if workspace is None:
        return make_error(
            "等待会话", f"agent '{target.agent_id}' 的 workspace 未运行",
        )

    book = target.book or healed
    turn = book.latest_turn if book is not None else None
    tracker = workspace.task_tracker
    status = await tracker.get_status(cid)

    if turn is not None and turn.is_terminal:
        return make_success(describe_terminal(turn, target, tracker))
    if turn is None and status == "idle":
        # 无簿记且起始即 idle：重启后对从未运行的会话防误报（审查 M8）
        return make_success(
            "无运行中回合：该会话当前空闲且无本插件簿记"
            "（宿主重启后簿记清空）。可用 agent_tools_context_read 读上下文、"
            "communicate(chat_id, action='recv') 读最新输出。"
        )

    try:
        wait_seconds = float(timeout)
    except (TypeError, ValueError):
        wait_seconds = 900.0
    body = await wait_for_terminal(
        workspace, target, turn, wait_seconds, chat_id=cid,
    )
    if body is not None:
        return make_success(body)
    # 超时（wait 语义：绝不停止目标会话；句式 §3.9⑤ 与 spawn 前台共用）
    return make_success(
        f"等待超时：会话 {cid} 仍运行中"
        f"（已等 {int(wait_seconds)} 秒，未停止、不受影响）。\n"
        "可 wait(chat_id, timeout=1800) 续等、notice(chat_id) 挂通知、"
        "recv 读进度、stop 停止。"
    )


async def agent_tools_wait(chat_id, timeout: int | float | str = 900):
    """前台等待一个或多个子会话回合结束并返回最终正文（超时不停止目标会话）。

    chat_id 可为单个字符串，或多个字符串组成的数组 / JSON 字符串数组。
    多个 id：**全部完成后才返回**；任一会话 id 不存在 → 提前失败，不对任何
    会话发起等待。
    """
    ids, err = normalize_chat_id_list(chat_id)
    if err:
        return make_error("等待会话", err)
    if len(ids) == 1:
        return await _wait_one(ids[0], timeout)

    # 多会话：先整体预校验，任一无效 → 提前失败、全都不等待
    bad = []
    for raw in ids:
        cid_, e, _healed = check_chat_id(raw)
        if e:
            bad.append(e)
            continue
        tgt = await locate_chat(cid_)
        if tgt is None:
            bad.append(f"chat_id '{raw}' 不存在，请检查拼写")
            continue
        if _workspace_of(tgt) is None:
            bad.append(f"chat_id '{raw}' 对应 agent 的 workspace 未运行")
    if bad:
        lines = "\n".join(f"  - {m}" for m in bad)
        return make_error(
            "等待会话",
            f"以下 chat_id 不存在或无效：\n{lines}\n（未对任何会话发起等待）",
        )

    # 全部有效 → 并发等待，全部完成（或各自超时）后返回；单块异常不影响其他块
    results = await asyncio.gather(
        *[_wait_one(raw, timeout) for raw in ids], return_exceptions=True,
    )
    blocks, all_err = [], True
    for raw, ch in zip(ids, results):
        if isinstance(ch, BaseException):
            blocks.append(f"### 会话 {raw}\n等待会话失败：处理异常 {ch!r}")
            continue
        all_err = all_err and bool(getattr(ch, "is_error", False))
        blocks.append(f"### 会话 {raw}\n{chunk_text(ch)}")
    joined = "\n\n".join(blocks)
    return make_error(joined) if all_err else make_success(joined)


def _workspace_of(target):
    return get_workspace(target.agent_id)
