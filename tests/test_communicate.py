# -*- coding: utf-8 -*-
"""communicate 测试：send 排队/提交 / recv / stop / 防误用拦截。"""

import asyncio

import tools.communicate as comm_mod
import tools.common as common_mod
from helpers import MockTracker, MockWorkspace, StubContent, StubMsg, make_turn

from manager import get_manager
from submit import SubmitResult


def _chunk_text(chunk):
    return chunk.content[0].text


def _patch(monkeypatch, statuses=("idle",), submit_queue_mode="immediate"):
    import session_reader

    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(list(statuses)))
    monkeypatch.setattr(comm_mod, "get_workspace", lambda aid: workspace)
    monkeypatch.setattr(comm_mod, "_agent_name", lambda aid: "demo-agent")
    monkeypatch.setattr(common_mod, "MISUSE_ERROR",
                        common_mod.MISUSE_ERROR)  # no-op，保持引用

    async def fake_locate(cid):
        from manager import SpawnRecord

        book = SpawnRecord(
            agent_id="child", session_id="sub-t0000001", user_id="u",
            channel="console", chat_id=cid, depth=1,
            parent=None, spawn_request_context={"k": "v"}, turns=[],
        )
        get_manager().spawn_records[cid] = book
        return type("Loc", (), {
            "agent_id": "child", "session_id": "sub-t0000001",
            "user_id": "u", "channel": "console", "chat_id": cid,
            "book": book,
        })()

    async def fake_misuse(sid, target_agent_id=None):
        return None

    monkeypatch.setattr(comm_mod, "locate_chat", fake_locate)
    monkeypatch.setattr(comm_mod, "check_misuse", fake_misuse)
    monkeypatch.setattr(comm_mod, "check_chat_id", lambda v: (v, None, None))

    submitted = {}

    async def fake_submit(ws, sid, uid, ch, text, **kw):
        submitted["text"] = text
        submitted["kw"] = kw
        queue = asyncio.Queue()
        if submit_queue_mode == "immediate":
            queue.put_nowait(None)
        return SubmitResult("chat-x", True, queue, {
            "producer_cancelled": False, "producer_error": None,
        })

    monkeypatch.setattr(comm_mod, "_submit_turn", fake_submit)

    async def fake_fetch(w, sid, uid, ch):
        return [
            StubMsg("user", "message", [StubContent(text="问")]),
            StubMsg("assistant", "message",
                    [StubContent(text="中途说话")]),
            StubMsg("assistant", "message", [StubContent(text="最终答")]),
        ]

    monkeypatch.setattr(session_reader, "fetch_normalized_messages", fake_fetch)
    return submitted


def test_send_empty_text(monkeypatch):
    from tools.communicate import agent_tools_communicate

    _patch(monkeypatch)
    chunk = asyncio.run(agent_tools_communicate("chat-x", "send", ""))
    assert "send 需要 text 参数" in _chunk_text(chunk)


def test_send_idle_submits_with_prefix(monkeypatch):
    from tools.communicate import agent_tools_communicate

    submitted = _patch(monkeypatch, statuses=("idle",))
    chunk = asyncio.run(agent_tools_communicate("chat-x", "send", "干活"))
    text = _chunk_text(chunk)
    assert "已提交" in text and "chat-x" in text
    assert "Agent「demo-agent」" in submitted["text"] and "干活" in submitted["text"]
    # 续聊审批路由原样携带（簿记 spawn_request_context）
    assert submitted["kw"]["request_context"] == {"k": "v"}


def test_send_busy_queues(monkeypatch):
    from tools.communicate import agent_tools_communicate

    _patch(monkeypatch, statuses=("running",), submit_queue_mode="hang")
    chunk = asyncio.run(agent_tools_communicate("chat-x", "send", "排队消息"))
    text = _chunk_text(chunk)
    assert "已排队" in text and "排第 1 位" in text
    # 队列结构存在且含条目
    mgr = get_manager()
    queue = mgr.get_queue("chat-x")
    assert queue is not None and len(queue.items) == 1
    # flusher task 已挂
    assert queue.flusher_task is not None
    queue.cancel_flag = True   # 收尾：防 flusher 继续轮询
    queue.flusher_task.cancel()


def test_send_busy_full_queue(monkeypatch):
    from tools.communicate import agent_tools_communicate
    from manager import PendingSend

    _patch(monkeypatch, statuses=("running",))
    mgr = get_manager()
    queue = mgr.get_or_create_queue("chat-x")
    for i in range(10):
        queue.items.append(PendingSend(text=f"m{i}", parent=None))
    chunk = asyncio.run(agent_tools_communicate("chat-x", "send", "超"))
    assert "排队已满" in _chunk_text(chunk)


def test_recv_body_and_monologue(monkeypatch):
    from tools.communicate import agent_tools_communicate

    _patch(monkeypatch)
    chunk = asyncio.run(agent_tools_communicate("chat-x", "recv"))
    text = _chunk_text(chunk)
    assert "自言自语" in text and "中途说话" in text
    assert "最终输出" in text and "最终答" in text
    assert "thinking" not in text  # 思考/工具不读


def test_stop_sequence(monkeypatch):
    from tools.communicate import agent_tools_communicate

    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["idle"]))
    monkeypatch.setattr(comm_mod, "get_workspace", lambda aid: workspace)
    monkeypatch.setattr(comm_mod, "check_chat_id", lambda v: (v, None, None))

    async def fake_locate(cid):
        return type("Loc", (), {
            "agent_id": "child", "session_id": "sub-t0000001",
            "user_id": "u", "channel": "console", "chat_id": cid,
            "book": None,
        })()

    monkeypatch.setattr(comm_mod, "locate_chat", fake_locate)

    stops = []

    async def fake_stop(ws, sid, cid):
        stops.append((sid, cid))
        return {"cancelled_tools": 2, "stopped": True}

    monkeypatch.setattr(comm_mod, "stop_session", fake_stop)
    chunk = asyncio.run(agent_tools_communicate("chat-x", "stop"))
    text = _chunk_text(chunk)
    assert "已停止" in text and "工具取消 2 项" in text
    assert stops == [("sub-t0000001", "chat-x")]


def test_misuse_intercept(monkeypatch):
    """祖先链拦截：当前会话在被调起回合中且目标在其祖先链上 → 3.9⑦。

    测试环境当前会话固定回落 (default, default)——让它扮演被父会话
    （child/sub-t0000001 即目标 chat-x）调起的子会话。
    """
    from tools.communicate import agent_tools_communicate

    _patch(monkeypatch)
    mgr = get_manager()
    mgr.register_active("default", "default", "chat-cur",
                        parent=("child", "sub-t0000001"))

    async def real_misuse(sid, target_agent_id=None):
        from tools.common import check_misuse as cm
        return await cm(sid, target_agent_id=target_agent_id)

    monkeypatch.setattr(comm_mod, "check_misuse", real_misuse)
    chunk = asyncio.run(agent_tools_communicate("chat-x", "send", "回父会话"))
    assert "只需在正文正常输出即可" in _chunk_text(chunk)


def test_invalid_action(monkeypatch):
    from tools.communicate import agent_tools_communicate

    chunk = asyncio.run(agent_tools_communicate("chat-x", "bad"))
    assert "action" in _chunk_text(chunk) and "send" in _chunk_text(chunk)
