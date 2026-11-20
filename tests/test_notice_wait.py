# -*- coding: utf-8 -*-
"""wait / notice 测试：终态返回 / stale / 超时文案 / 注册与取消。"""

import asyncio

import tools.notice as notice_mod
import tools.wait as wait_mod
from helpers import (
    MockTracker,
    MockWorkspace,
    StubContent,
    StubMsg,
    make_turn,
    now,
)

from manager import SpawnRecord, TurnRecord, get_manager


def _chunk_text(chunk):
    return chunk.content[0].text


def _mk_book(chat_id, turn_status=None, session_id="sub-w0000001"):
    mgr = get_manager()
    turns = [make_turn(status=turn_status)] if turn_status else []
    return mgr.inject_spawn_record(
        chat_id, agent_id="child", session_id=session_id, turns=turns,
    )


def _loc(chat_id, book):
    return type("Loc", (), {
        "agent_id": book.agent_id, "session_id": book.session_id,
        "user_id": book.user_id, "channel": book.channel,
        "chat_id": chat_id, "book": book,
    })()


def test_wait_completed_returns_body(monkeypatch):
    from tools.wait import agent_tools_wait

    book = _mk_book("chat-w", turn_status="completed")
    book.turns[0].result_text = "任务结果正文"
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["idle"]))
    monkeypatch.setattr(wait_mod, "get_workspace", lambda aid: workspace)
    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)
    monkeypatch.setattr(wait_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(wait_mod, "check_chat_id", lambda v: (v, None, None))
    chunk = asyncio.run(agent_tools_wait("chat-w"))
    text = _chunk_text(chunk)
    assert "completed" in text and "任务结果正文" in text


def test_wait_no_book_idle(monkeypatch):
    from tools.wait import agent_tools_wait

    book = _mk_book("chat-nb")   # 无 turns
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["idle"]))
    monkeypatch.setattr(wait_mod, "get_workspace", lambda aid: workspace)
    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)
    monkeypatch.setattr(wait_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(wait_mod, "check_chat_id", lambda v: (v, None, None))
    chunk = asyncio.run(agent_tools_wait("chat-nb"))
    assert "无运行中回合" in _chunk_text(chunk)


def test_wait_timeout(monkeypatch):
    from tools.wait import agent_tools_wait

    book = _mk_book("chat-to", turn_status="running")
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["running"]))
    monkeypatch.setattr(wait_mod, "get_workspace", lambda aid: workspace)
    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)
    monkeypatch.setattr(wait_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(wait_mod, "check_chat_id", lambda v: (v, None, None))
    chunk = asyncio.run(agent_tools_wait("chat-to", timeout=1))
    text = _chunk_text(chunk)
    assert "等待超时" in text and "仍运行中" in text
    assert "未停止、不受影响" in text
    # 超时绝不停止目标：无 request_stop 调用
    assert workspace.task_tracker.stopped == []


def test_wait_stale_branch(monkeypatch):
    """簿记 running 但 tracker idle → stale 分支从会话文件读正文。"""
    from tools.wait import agent_tools_wait
    import session_reader

    book = _mk_book("chat-st", turn_status="running")
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["idle"]))
    monkeypatch.setattr(wait_mod, "get_workspace", lambda aid: workspace)
    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)
    monkeypatch.setattr(wait_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(wait_mod, "check_chat_id", lambda v: (v, None, None))

    async def fake_fetch(w, sid, uid, ch):
        return [
            StubMsg("user", "message", [StubContent(text="问")]),
            StubMsg("assistant", "message", [StubContent(text="兜底正文")]),
        ]

    monkeypatch.setattr(session_reader, "fetch_normalized_messages", fake_fetch)
    chunk = asyncio.run(agent_tools_wait("chat-st", timeout=2))
    text = _chunk_text(chunk)
    assert "不一致" in text and "兜底正文" in text


def test_notice_register_success(monkeypatch):
    from tools.notice import agent_tools_notice
    import session_reader

    book = _mk_book("chat-nt", turn_status="running")
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["running"]))
    monkeypatch.setattr(notice_mod, "get_workspace", lambda aid: workspace)
    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)
    monkeypatch.setattr(notice_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(notice_mod, "check_chat_id", lambda v: (v, None, book))
    monkeypatch.setattr(notice_mod, "current_channel", lambda: "console")

    async def fake_fetch(w, sid, uid, ch):
        return []

    monkeypatch.setattr(session_reader, "fetch_normalized_messages", fake_fetch)

    chunk = asyncio.run(agent_tools_notice("chat-nt"))
    text = _chunk_text(chunk)
    assert "已注册" in text
    mgr = get_manager()
    record = mgr.get_notice("chat-nt")
    assert record is not None
    assert record.wake_agent_id == "default"
    assert record.wake_channel == "console"
    assert not record.cancelled and not record.completion_sent


def test_notice_already_terminal(monkeypatch):
    from tools.notice import agent_tools_notice

    book = _mk_book("chat-done", turn_status="completed")
    book.turns[0].result_text = "已有结果"
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["idle"]))
    monkeypatch.setattr(notice_mod, "get_workspace", lambda aid: workspace)
    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)
    monkeypatch.setattr(notice_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(notice_mod, "check_chat_id", lambda v: (v, None, book))
    chunk = asyncio.run(agent_tools_notice("chat-done"))
    text = _chunk_text(chunk)
    assert "目标会话未注册完成通知" in text
    assert get_manager().get_notice("chat-done") is None


def test_notice_cancel(monkeypatch):
    from tools.notice import agent_tools_notice
    from manager import NoticeRecord

    mgr = get_manager()
    mgr.register_notice(NoticeRecord(
        target_chat_id="chat-cx", target_agent_id="a",
        target_session_id="sub-cx", wake_agent_id="pa", wake_user_id="u",
        wake_session_id="ps", wake_channel="console",
    ))
    monkeypatch.setattr(notice_mod, "check_chat_id",
                        lambda v: (v, None, None))

    async def _fake_loc(cid):
        return object()

    monkeypatch.setattr(notice_mod, "locate_chat", _fake_loc)
    chunk = asyncio.run(agent_tools_notice("chat-cx", cancel=True))
    assert "已取消" in _chunk_text(chunk)
    assert mgr.get_notice("chat-cx").cancelled


def test_notice_reregister_idempotent(monkeypatch):
    from tools.notice import agent_tools_notice
    from manager import NoticeRecord

    mgr = get_manager()
    mgr.register_notice(NoticeRecord(
        target_chat_id="chat-idem", target_agent_id="a",
        target_session_id="sub-idem", wake_agent_id="pa", wake_user_id="u",
        wake_session_id="ps", wake_channel="console",
    ))
    monkeypatch.setattr(notice_mod, "check_chat_id",
                        lambda v: (v, None, None))
    chunk = asyncio.run(agent_tools_notice("chat-idem"))
    assert "完成通知已注册" in _chunk_text(chunk)


def test_notice_untracked_watch(monkeypatch):
    """无簿记 + running → 注册成功（监视协程兜底路径）。"""
    from tools.notice import agent_tools_notice
    import session_reader

    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["running"]))
    monkeypatch.setattr(notice_mod, "get_workspace", lambda aid: workspace)
    monkeypatch.setattr(notice_mod, "check_chat_id", lambda v: (v, None, None))

    async def fake_locate(cid):
        return type("Loc", (), {
            "agent_id": "child", "session_id": "sub-u0000001",
            "user_id": "u", "channel": "console", "chat_id": cid,
            "book": None,
        })()

    monkeypatch.setattr(notice_mod, "locate_chat", fake_locate)
    monkeypatch.setattr(notice_mod, "current_channel", lambda: "console")

    async def fake_fetch(w, sid, uid, ch):
        return []

    monkeypatch.setattr(session_reader, "fetch_normalized_messages", fake_fetch)
    chunk = asyncio.run(agent_tools_notice("chat-u"))
    assert "已注册" in _chunk_text(chunk)
    assert get_manager().get_notice("chat-u") is not None


# ── 截断全文落盘（v0.2.4，QwenPaw 大 tool_result 同款机制） ──


def test_wait_truncated_saves_full_body(monkeypatch, tmp_path):
    """completed 正文超 WAIT_BODY_LIMIT(9000) → 全文落盘调用方
    workspace tool_results，尾部含文件路径 + 续读行号。"""
    from tools.wait import agent_tools_wait
    import tools.common as common_mod

    body = "甲" * 16003
    book = _mk_book("chat-tr", turn_status="completed")
    book.turns[0].result_text = body
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["idle"]))
    monkeypatch.setattr(wait_mod, "get_workspace", lambda aid: workspace)
    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)
    monkeypatch.setattr(wait_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(wait_mod, "check_chat_id", lambda v: (v, None, None))
    monkeypatch.setattr(common_mod, "get_agent_workspace_dir",
                        lambda aid: str(tmp_path))
    chunk = asyncio.run(agent_tools_wait("chat-tr"))
    text = _chunk_text(chunk)
    assert "<<<TRUNCATED>>>" in text
    assert "tool_results" in text
    assert "从第 1 行开始续读" in text   # 16003 字符单行 → 切点在第 1 行
    files = list((tmp_path / "tool_results").glob("agent-tools-*.txt"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == body


def test_notice_truncated_saves_full_body(monkeypatch, tmp_path):
    """notice 正文超 NOTIFY_BODY_LIMIT(4000) → 全文落盘唤醒会话
    workspace tool_results。"""
    import notifier as notifier_mod
    from manager import NoticeRecord

    body = "乙" * 20000
    record = NoticeRecord(
        target_chat_id="chat-nt", target_agent_id="child",
        target_session_id="sub-nt", wake_agent_id="pa", wake_user_id="u",
        wake_session_id="ps", wake_channel="console",
    )
    monkeypatch.setattr(notifier_mod, "get_agent_workspace_dir",
                        lambda aid: str(tmp_path))
    text = notifier_mod.build_notice_text(record, "completed", body)
    assert "<<<TRUNCATED>>>" in text
    assert "从第 1 行开始续读" in text
    files = list((tmp_path / "tool_results").glob("agent-tools-*.txt"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == body


def test_recv_truncated_saves_full_body(monkeypatch, tmp_path):
    """recv 正文 >9000 → 全文落盘调用方 workspace tool_results，
    尾部含文件路径 + 续读行号。"""
    import tools.communicate as comm_mod

    body = "丙" * 9003
    async def _fake_read(ws, tgt):
        return body, []
    monkeypatch.setattr(comm_mod, "read_latest_turn_sections", _fake_read)
    monkeypatch.setattr(comm_mod, "get_agent_workspace_dir",
                        lambda aid: str(tmp_path))
    target = type("T", (), {"chat_id": "chat-rc"})()
    chunk = asyncio.run(comm_mod._do_recv(object(), target))
    text = _chunk_text(chunk)
    assert "<<<TRUNCATED>>>" in text
    assert "tool_results" in text
    # 全文 3 行（标题 / 分节头 / 正文），切点落在正文行
    assert "从第 3 行开始续读" in text
    files = list((tmp_path / "tool_results").glob("agent-tools-*.txt"))
    assert len(files) == 1
    saved = files[0].read_text(encoding="utf-8")
    assert "—— 最终输出 ——" in saved and body in saved


def test_recv_short_body_untouched(monkeypatch):
    """recv 正文 ≤9000 → 原样返回，无截断标记、不落盘。"""
    import tools.communicate as comm_mod

    async def _fake_read(ws, tgt):
        return "短正文", []
    monkeypatch.setattr(comm_mod, "read_latest_turn_sections", _fake_read)
    target = type("T", (), {"chat_id": "chat-rc2"})()
    chunk = asyncio.run(comm_mod._do_recv(object(), target))
    text = _chunk_text(chunk)
    assert "短正文" in text and "<<<TRUNCATED>>>" not in text
