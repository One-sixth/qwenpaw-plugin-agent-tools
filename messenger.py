# -*- coding: utf-8 -*-
"""messenger — 非 console 频道的通知信使（agent 回合 + 回复送达 IM）。

对齐 process-tools messenger.py 的 run_wake 链路（决策 18）：channel 预检
→ get_or_create_chat → 忙检 → 会话门闩 → stream_query 跑一轮 → send_event
回频道。差异：**in-process 直调**——notifier flusher 直接 ``await run_wake``，
无 web_api.py、无 HTTP loopback（决策 11 全自洽）。

链路语义（继承 process-tools 0.5.0 设计）：
- 非 console 会话的气泡是死信（console_push_store 无网页消费），
  必须经信使路由把回复送达 IM
- workspace.stream_query 是裸跑（不向 task_tracker 登记）→ 会话级门闩
  互斥信使自身并发（tracker 对信使回合失明）
- 单次回合上限 WAKE_AGENT_TIMEOUT_SECONDS（wait_for 到点取消）
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# agent 回合上限（含模型推理 + 工具调用）
WAKE_AGENT_TIMEOUT_SECONDS = 300

# 会话级门闩：信使回合互斥（process-tools 0.5.0 并发丢失修复同款）。
# gate 以 (agent_id, channel, user_id, session_id) 为键，locked 即 busy
# → 调用方（notifier flusher）以 3s 窗口忙等重试。asyncio 单线程下
# 「locked 检查 → acquire」之间无 await，天然原子。
_SESSION_GATES: dict = {}


def _gate_key(workspace, channel: str, user_id: str, session_id: str):
    agent = getattr(workspace, "agent_id", "") or ""
    return (agent, channel, user_id or "default", session_id)


def reset_gates() -> None:
    """测试用：清空会话门闩（Lock 不跨测试事件循环复用）。"""
    _SESSION_GATES.clear()


async def run_wake(
    workspace,
    *,
    channel: str,
    user_id: str,
    session_id: str,
    text: str,
) -> dict:
    """跑一轮 agent 回合并把回复事件送回频道（in-process，供 notifier 直调）。

    Returns:
        {"ok": True, "chat_id": ...}
        {"ok": False, "busy": True}                          会话忙 → 通知侧重试
        {"ok": False, "error": "频道未配置: xxx"}
        {"ok": False, "error": "agent 回合超时(...)"}
        {"ok": False, "error": "...", "agent_done": True}    回合已启动但中途失败
    """
    channel_manager = getattr(workspace, "channel_manager", None)
    chat_manager = getattr(workspace, "chat_manager", None)
    if channel_manager is None or chat_manager is None:
        return {"ok": False, "error": "workspace 缺少 channel/chat manager"}

    # 1) 频道预检（未配置提前分类报错，不空跑 agent 回合）
    ch = await channel_manager.get_channel(channel)
    if ch is None:
        return {"ok": False, "error": f"频道未配置: {channel}"}

    # 2) 幂等取 chat（三元组全等；真实会话必命中）
    chat = await chat_manager.get_or_create_chat(
        session_id=session_id,
        user_id=user_id,
        channel=channel,
    )

    # 3) 忙检（run_key = chat.id，与 console task_tracker 同键）
    tracker = getattr(workspace, "task_tracker", None)
    if tracker is not None:
        status = await tracker.get_status(chat.id)
        if status == "running":
            return {"ok": False, "busy": True}

    # 3b) 会话门闩（信使自身并发互斥）
    gate = _SESSION_GATES.setdefault(
        _gate_key(workspace, channel, user_id, session_id),
        asyncio.Lock(),
    )
    if gate.locked():
        return {"ok": False, "busy": True}
    await gate.acquire()

    # 4+5) agent 回合 + 事件送频道（cron executor 同款 dict 形态）
    prompt = (
        "【agent 会话通知】以下子 agent 会话状态发生变化，请查看并按需处理"
        "（可用 agent_tools_list_session 查会话、"
        "agent_tools_communicate(chat_id, action='recv') 读进度；"
        "你的回复会直接送达用户，请保持简洁）：\n\n"
        f"{text}"
    )
    req = {
        "channel": channel,
        "user_id": user_id or "default",
        "session_id": session_id,
        "input": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            },
        ],
        # suppress_console_push：非 console 会话不写 console_push_store
        # （死信路径）
        "request_context": {
            "source": "agent_tools_notice",
            "suppress_console_push": True,
        },
    }
    meta = {"suppress_console_push": True}

    agent_done = False

    async def _run() -> None:
        nonlocal agent_done
        async for event in workspace.stream_query(req):
            agent_done = True
            # 基类只发 message+Completed；其余事件静默跳过
            await channel_manager.send_event(
                channel=channel,
                user_id=user_id,
                session_id=session_id,
                event=event,
                meta=meta,
            )

    try:
        try:
            await asyncio.wait_for(
                _run(), timeout=WAKE_AGENT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            note = (
                "agent 回合超时(>" f"{WAKE_AGENT_TIMEOUT_SECONDS}s)"
            )
            if agent_done:
                return {"ok": False, "error": note, "agent_done": True}
            return {"ok": False, "error": note}
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            if agent_done:
                result["agent_done"] = True
            return result
        return {"ok": True, "chat_id": chat.id}
    finally:
        gate.release()
