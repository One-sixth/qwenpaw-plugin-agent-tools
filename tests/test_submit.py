# -*- coding: utf-8 -*-
"""submit 层测试：注册字段派生 / SSE 错误信号 / 消费者终态状态机。"""

import asyncio

import pytest

import session_reader
from helpers import MockTracker, MockWorkspace

from manager import SpawnRecord, TurnRecord, get_manager
from submit import (
    SubmitResult,
    consume_and_track,
    derive_registration_fields,
    extract_error_signal,
)


def test_derive_registration_fields_spawn():
    rc = {"_spawn_subagent": True, "parent_session_id": "p-1",
          "root_session_id": "r-1"}
    fields = derive_registration_fields(rc)
    assert fields == {"source": "subagent", "parent_session_id": "p-1",
                      "root_session_id": "r-1"}


def test_derive_registration_fields_none_or_plain():
    assert derive_registration_fields(None) == {}
    assert derive_registration_fields({"other": 1}) == {}
    # 空字符串 parent/root → None（宿主端点同款）
    fields = derive_registration_fields({"_spawn_subagent": True})
    assert fields["parent_session_id"] is None
    assert fields["root_session_id"] is None


def test_extract_error_signal():
    assert extract_error_signal('data: {"type": "rate_limited", "error": "x"}') == (
        "rate_limited: x"
    )
    assert extract_error_signal('data: {"error": "boom"}') == "error: boom"
    assert extract_error_signal('data: {"type": "message"}') == ""
    assert extract_error_signal("event: x") == ""
    assert extract_error_signal("data: [DONE]") == ""
    assert extract_error_signal("data: not-json") == ""


def _make_record(chat_id="chat-c", session_id="sub-c0000001"):
    mgr = get_manager()
    turn = TurnRecord()
    record = SpawnRecord(
        agent_id="child", session_id=session_id, user_id="u",
        channel="console", chat_id=chat_id, turns=[turn],
    )
    mgr.register_spawn(record)
    mgr.register_active("child", session_id, chat_id, parent=None, turn=turn)
    return record, turn


def test_consume_completed():
    async def body():
        tracker = MockTracker()
        tracker.consumed = ['data: {"type": "message"}']
        tracker.next_queue = None
        workspace = MockWorkspace(agent_id="child", task_tracker=tracker)
        record, turn = _make_record()

        async def fake_fetch(w, sid, uid, ch):
            return [
                __import__("helpers").StubMsg(
                    "user", "message",
                    [__import__("helpers").StubContent(text="问")],
                ),
                __import__("helpers").StubMsg(
                    "assistant", "message",
                    [__import__("helpers").StubContent(text="答案")],
                ),
            ]

        orig = session_reader.fetch_normalized_messages
        session_reader.fetch_normalized_messages = fake_fetch
        try:
            result = SubmitResult("chat-c", True, asyncio.Queue(), {
                "producer_cancelled": False, "producer_error": None,
            })
            # 队列预填事件 + sentinel
            result.queue.put_nowait('data: {"type": "message"}')
            result.queue.put_nowait(None)
            await consume_and_track(workspace, record, turn, result)
        finally:
            session_reader.fetch_normalized_messages = orig

        assert turn.status == "completed", turn.status
        assert turn.result_text == "答案"
        assert turn.finished_at is not None
        # 活跃表已移除
        assert not get_manager().is_active("child", "sub-c0000001")

    asyncio.run(body())


def test_consume_producer_cancelled():
    async def body():
        tracker = MockTracker()
        workspace = MockWorkspace(agent_id="child", task_tracker=tracker)
        record, turn = _make_record("chat-x", "sub-x0000001")
        result = SubmitResult("chat-x", True, asyncio.Queue(), {
            "producer_cancelled": True, "producer_error": None,
        })
        result.queue.put_nowait(None)
        await consume_and_track(workspace, record, turn, result)
        assert turn.status == "cancelled"
        assert not get_manager().is_active("child", "sub-x0000001")

    asyncio.run(body())


def test_consume_error_signal_failed():
    async def body():
        tracker = MockTracker()
        workspace = MockWorkspace(agent_id="child", task_tracker=tracker)
        record, turn = _make_record("chat-f", "sub-f0000001")
        result = SubmitResult("chat-f", True, asyncio.Queue(), {
            "producer_cancelled": False, "producer_error": None,
        })
        result.queue.put_nowait('data: {"type": "rate_limited", "error": "额度"}')
        result.queue.put_nowait(None)
        await consume_and_track(workspace, record, turn, result)
        assert turn.status == "failed"
        assert "rate_limited" in turn.error

    asyncio.run(body())


def test_consume_producer_error_failed():
    async def body():
        tracker = MockTracker()
        workspace = MockWorkspace(agent_id="child", task_tracker=tracker)
        record, turn = _make_record("chat-e", "sub-e0000001")
        result = SubmitResult("chat-e", True, asyncio.Queue(), {
            "producer_cancelled": False, "producer_error": RuntimeError("boom"),
        })
        result.queue.put_nowait(None)
        await consume_and_track(workspace, record, turn, result)
        assert turn.status == "failed"
        assert "boom" in turn.error

    asyncio.run(body())
