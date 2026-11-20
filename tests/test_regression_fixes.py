# -*- coding: utf-8 -*-
"""复审修复回归测试（2026-11-17 R1/R2 四路复审 + 作者拍板项）。

- notice 主链路通电回归（R1 HIGH #1：get_notifier 未导入 NameError 被吞）
- FIFO 竞态排队（作者拍板：队列非空 + idle → 也排队，禁直提超车）
- blocks JSON 字符串解析（R1 #4 修复：str→json.loads，坏 JSON→[]）
- locate_chat 解包回归（R1 LOW：get_current_context 取错位）
- _truncated 截断日志（R1 LOW：静默少报 → warning 留痕）
"""

import asyncio
import json
import logging

import pytest

import context as context_mod
import tools.communicate as comm_mod
import utils as utils_mod
from helpers import MockTracker, MockWorkspace

from manager import PendingSend, SpawnRecord, TurnRecord, get_manager
from notifier import get_notifier
from submit import SubmitResult, consume_and_track


def _chunk_text(chunk):
    return chunk.content[0].text


def _mk_book(chat_id, turn_status=None, session_id="sub-r0000001"):
    mgr = get_manager()
    turns = [TurnRecord(status=turn_status)] if turn_status else []
    return mgr.inject_spawn_record(
        chat_id, agent_id="child", session_id=session_id, turns=turns,
    )


def _loc(chat_id, book):
    return type("Loc", (), {
        "agent_id": book.agent_id, "session_id": book.session_id,
        "user_id": book.user_id, "channel": book.channel,
        "chat_id": chat_id, "book": book,
    })()


def record_book(book):
    """consume_and_track 需要 SpawnRecord（book 即 SpawnRecord）。"""
    return book


# ── HIGH 回归：notice 主触发链路通电 ────────────────────────


def test_notice_wired_to_consumer_terminal(monkeypatch):
    """注册 notice → 消费者写终态 → completion_sent=True。

    修复前 submit.py 缺 get_notifier 导入，NameError 被宽 except 吞掉，
    本用例断言 completion_sent 必红（R1 HIGH #1 的防复发闸）。
    """
    import tools.notice as notice_mod

    book = _mk_book("chat-hi", turn_status="running")
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["idle"]))
    monkeypatch.setattr(notice_mod, "get_workspace", lambda aid: workspace)

    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)

    monkeypatch.setattr(notice_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(notice_mod, "check_chat_id",
                        lambda v: (v, None, book))
    monkeypatch.setattr(notice_mod, "current_channel", lambda: "console")

    from tools.notice import agent_tools_notice
    chunk = asyncio.run(agent_tools_notice("chat-hi"))
    assert "已注册" in _chunk_text(chunk)

    # 消费者跑终态（预填队列 + sentinel → completed）
    turn = book.turns[0]
    record, _ = book, turn
    result = SubmitResult(
        "chat-hi", True, asyncio.Queue(),
        {"producer_cancelled": False, "producer_error": None},
    )
    result.queue.put_nowait(None)
    asyncio.run(consume_and_track(workspace, record, turn, result))

    assert turn.status == "completed"
    notice_record = get_manager().get_notice("chat-hi")
    assert notice_record is not None
    assert notice_record.completion_sent is True, (
        "notice 主触发链路断裂：消费者写终态后未投递通知"
    )


def test_consumer_failed_terminal_also_notifies(monkeypatch):
    """failed 终态同样触发 notice（决策 6：三种终态全部触发）。"""
    import tools.notice as notice_mod

    book = _mk_book("chat-hf", turn_status="running")
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["idle"]))
    monkeypatch.setattr(notice_mod, "get_workspace", lambda aid: workspace)

    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)

    monkeypatch.setattr(notice_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(notice_mod, "check_chat_id",
                        lambda v: (v, None, book))
    monkeypatch.setattr(notice_mod, "current_channel", lambda: "console")

    from tools.notice import agent_tools_notice
    asyncio.run(agent_tools_notice("chat-hf"))

    turn = book.turns[0]
    result = SubmitResult(
        "chat-hf", True, asyncio.Queue(),
        {"producer_cancelled": False, "producer_error": RuntimeError("boom")},
    )
    result.queue.put_nowait(None)
    asyncio.run(consume_and_track(workspace, record_book(book), turn, result))

    assert turn.status == "failed"
    notice_record = get_manager().get_notice("chat-hf")
    assert notice_record is not None
    assert notice_record.completion_sent is True


# ── FIFO 竞态：队列非空 + idle → 排队（禁直提超车） ──────────


def test_send_queued_when_queue_nonempty_and_idle(monkeypatch):
    """busy→idle 过渡窗口（flusher 未取走队首）+ idle：
    新 send 必须排队而非直提（作者拍板，T1 实测 3→2 超车回归闸）。"""
    from tools.communicate import agent_tools_communicate

    book = _mk_book("chat-rq")
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["idle"]))
    mgr = get_manager()

    # 预置：队列已有 1 条待投递消息（flusher 还没取走）
    queue = mgr.get_or_create_queue("chat-rq")
    queue.items.append(PendingSend(text="第二条（排队中）", parent=None))

    monkeypatch.setattr(comm_mod, "get_workspace", lambda aid: workspace)

    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)

    monkeypatch.setattr(comm_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(comm_mod, "check_chat_id",
                        lambda v: (v, None, book))

    async def _no_misuse(session_id, target_agent_id=None):
        return None

    from tools.common import check_misuse
    monkeypatch.setattr(comm_mod, "check_misuse", _no_misuse)
    monkeypatch.setattr(comm_mod, "get_current_context",
                        lambda: ("default", "u-1", "sess-1"))

    chunk = asyncio.run(
        agent_tools_communicate("chat-rq", action="send", text="第三条"),
    )
    text = _chunk_text(chunk)
    assert "已排队" in text, text
    assert "排第 2 位" in text, text
    # 队列追加成功且 FIFO 保序
    assert len(queue.items) == 2
    assert queue.items[0].text == "第二条（排队中）"
    assert "第三条" in queue.items[1].text


def test_send_direct_when_idle_and_no_queue(monkeypatch):
    """idle 且无队列 → 直提（原语义不变）。"""
    from tools.communicate import agent_tools_communicate

    book = _mk_book("chat-idle")
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["idle"]))
    monkeypatch.setattr(comm_mod, "get_workspace", lambda aid: workspace)

    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)

    monkeypatch.setattr(comm_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(comm_mod, "check_chat_id",
                        lambda v: (v, None, book))

    async def _no_misuse(session_id, target_agent_id=None):
        return None

    monkeypatch.setattr(comm_mod, "check_misuse", _no_misuse)
    monkeypatch.setattr(comm_mod, "get_current_context",
                        lambda: ("default", "u-1", "sess-2"))

    submit_calls = []

    async def _fake_submit(ws, sid, uid, ch, text, **kw):
        submit_calls.append(text)
        return SubmitResult(
            "chat-idle", True, asyncio.Queue(),
            {"producer_cancelled": False, "producer_error": None},
        )

    monkeypatch.setattr(comm_mod, "_submit_turn", _fake_submit)

    chunk = asyncio.run(
        agent_tools_communicate("chat-idle", action="send", text="直提消息"),
    )
    text = _chunk_text(chunk)
    assert "已提交" in text, text
    # 直提路径确实调用 _submit_turn 一次（未排队）
    assert len(submit_calls) == 1
    assert "直提消息" in submit_calls[0]
    # 无排队结构残留
    assert get_manager().get_queue("chat-idle") is None


# ── blocks JSON 字符串解析（R1 #4 回归） ─────────────────────


class _FakeMS:
    """MemorySpace 替身：预置 rows（含 JSON 串/坏 JSON/_truncated 行）。"""

    captured_row_cap = None

    def __init__(self, **kwargs):
        _FakeMS.captured_row_cap = kwargs.get("row_cap")

    def sql_query(self, _sql, _params):
        return [
            {   # 正常：blocks 是 JSON 字符串（宿主写入端 _to_json 形态）
                "seq": 10, "kind": "model_turn", "role": "assistant",
                "content": "", "headline": "", "tool_state": "",
                "metadata": "", "created_at": "2026-11-17T10:00:00",
                "name": None,
                "blocks": json.dumps(
                    [{"type": "text", "text": "驱逐轮正文"}]
                ),
            },
            {   # 坏 JSON → blocks 降级 []
                "seq": 11, "kind": "model_turn", "role": "assistant",
                "content": "", "headline": "", "tool_state": "",
                "metadata": "", "created_at": "2026-11-17T10:01:00",
                "name": None, "blocks": "{not-valid-json",
            },
            {   # row_cap 溢出标记行 → 过滤
                "seq": 12, "_truncated": True,
            },
        ]

    def close(self):
        pass


def test_fetch_evicted_rows_parses_blocks_json_string(monkeypatch):
    """blocks 列 JSON 字符串 → 解析成 list；坏 JSON → []；_truncated 过滤。"""
    monkeypatch.setattr(context_mod, "history_path", lambda w: "fake.db")
    monkeypatch.setattr(
        "qwenpaw.agents.context.scroll.memoryspace.MemorySpace", _FakeMS,
    )
    rows = context_mod.fetch_evicted_rows(
        workspace := object(), "agent-x", "sess-x",
    )
    assert rows is not None and len(rows) == 2
    assert rows[0].blocks == [{"type": "text", "text": "驱逐轮正文"}]
    assert rows[1].blocks == []
    # 负 idx 映射：2 行 → idx -2/-1
    assert rows[0].idx == -2 and rows[1].idx == -1
    # row_cap 用 10 万（复审 B-M2 在位）
    assert _FakeMS.captured_row_cap == context_mod._EVICTED_ROW_CAP


def test_fetch_evicted_rows_truncated_logged(monkeypatch, caplog):
    """_truncated 行命中 → warning 日志留痕（R1 LOW：静默少报修复）。"""
    monkeypatch.setattr(context_mod, "history_path", lambda w: "fake.db")
    monkeypatch.setattr(
        "qwenpaw.agents.context.scroll.memoryspace.MemorySpace", _FakeMS,
    )
    with caplog.at_level(logging.WARNING, logger=context_mod.logger.name):
        context_mod.fetch_evicted_rows(object(), "agent-x", "sess-y")
    assert any("截断" in rec.message for rec in caplog.records)


# ── locate_chat 解包回归（R1 LOW） ──────────────────────────


def test_locate_chat_resolver_receives_agent_id(monkeypatch):
    """无簿记兜底路径：find_chat_matches 第三参收 agent_id 而非 session_id
    （R1 LOW：get_current_context 解包错位回归闸）。"""
    captured = {}

    async def _fake_find(config, chat_id, agent_id):
        captured["agent_id"] = agent_id

        class _Spec:
            session_id, user_id, channel = "sess-r", "u-r", "console"

        class _Res:
            chat = _Spec()
            agent_id = "resolved-agent"

        return [_Res()]

    import qwenpaw.app.chats.resolver as resolver_mod
    import qwenpaw.config.utils as config_utils

    monkeypatch.setattr(resolver_mod, "find_chat_matches", _fake_find)
    monkeypatch.setattr(config_utils, "load_config", lambda: object())
    monkeypatch.setattr(utils_mod, "get_current_context",
                        lambda: ("agent-x", "user-x", "sess-should-not-pass"))

    loc = asyncio.run(utils_mod.locate_chat("chat-no-book"))
    assert loc is not None
    assert captured["agent_id"] == "agent-x", (
        "find_chat_matches 收到 session_id 而非 agent_id（解包错位复发）"
    )
    assert loc.agent_id == "resolved-agent"


# ── 消费者异常分支触发 notice（2026-11-17 作者拍板） ─────────


class _ExplodingTracker(MockTracker):
    """stream_from_queue 一读就抛（模拟宿主流异常的罕见场景）。"""

    async def stream_from_queue(self, queue, run_key, *, cancel_run=False):
        raise RuntimeError("stream exploded")
        yield  # pragma: no cover  （使函数成为 async generator）


def test_consumer_exception_triggers_notice():
    """消费者读流异常 → turn=failed 且 notice 触发（三种终态全触发）。"""
    import session_reader as sr_mod

    workspace = MockWorkspace(agent_id="child",
                              task_tracker=_ExplodingTracker(["idle"]))
    record = _mk_book("chat-ex", turn_status="running")
    turn = record.turns[0]
    result = SubmitResult(
        "chat-ex", True, asyncio.Queue(),
        {"producer_cancelled": False, "producer_error": None},
    )

    mgr = get_manager()
    from manager import NoticeRecord

    notice = NoticeRecord(
        target_chat_id="chat-ex",
        target_agent_id="child",
        target_session_id="sub-r0000001",
        wake_agent_id="default", wake_user_id="u",
        wake_session_id="sess-parent", wake_channel="console",
    )
    mgr.register_notice(notice)

    async def _fake_read(ws, obj):
        return ""

    monkey_orig = sr_mod.read_latest_turn_body
    sr_mod.read_latest_turn_body = _fake_read
    try:
        asyncio.run(consume_and_track(workspace, record, turn, result))
    finally:
        sr_mod.read_latest_turn_body = monkey_orig

    assert turn.status == "failed"
    assert "consumer error" in (turn.error or "")
    assert notice.completion_sent is True, (
        "异常分支写终态后未触发 notice（缺口复发）"
    )


# ── flusher 连续失败超限放弃（2026-11-17 作者拍板） ──────────


def _patch_flusher_env(monkeypatch, submit_behavior):
    """flusher 测试环境：快轮询 + resolver/上下文/提交 mock。

    submit_behavior(idx) -> SubmitResult：第 idx 次（0 基）提交的返回，
    抛异常即提交失败。
    """
    monkeypatch.setattr(comm_mod, "FLUSHER_POLL_SECONDS", 0.01)

    monkeypatch.setattr(
        comm_mod, "get_workspace",
        lambda aid: MockWorkspace(
            agent_id="child", task_tracker=MockTracker(["idle"]),
        ),
    )

    async def _fake_loc(cid, _b=None):
        return None

    monkeypatch.setattr(comm_mod, "locate_chat", _fake_loc)

    async def _no_misuse(session_id, target_agent_id=None):
        return None

    monkeypatch.setattr(comm_mod, "check_misuse", _no_misuse)
    monkeypatch.setattr(comm_mod, "get_current_context",
                        lambda: ("default", "u", "sess-parent"))
    monkeypatch.setattr(
        "qwenpaw.config.utils.load_config", lambda: object(),
    )

    async def _fake_find(config, chat_id, agent_id):
        return [object()]

    monkeypatch.setattr(
        "qwenpaw.app.chats.resolver.find_chat_matches", _fake_find,
    )

    calls = {"n": 0}

    async def _fake_submit(ws, sid, uid, ch, text, **kw):
        idx = calls["n"]
        calls["n"] += 1
        return await submit_behavior(idx)

    monkeypatch.setattr(comm_mod, "_submit_turn", _fake_submit)

    bubbles = []

    async def _fake_append(session_id, text, *, sticky=False):
        bubbles.append((session_id, text, sticky))

    monkeypatch.setattr(
        "qwenpaw.app.console_push_store.append", _fake_append,
    )
    return calls, bubbles


def _mk_flusher_target(chat_id="chat-fl"):
    return type("T", (), {
        "agent_id": "child", "session_id": "sub-r0000001",
        "user_id": "u", "channel": "console",
        "chat_id": chat_id, "book": None,
    })()


def test_flusher_gives_up_after_10_failures(monkeypatch):
    """连续 10 次「空闲→投递→失败→放回」→ 放弃 + drop_queue + 气泡。"""

    async def _always_fail(idx):
        raise RuntimeError("host api broken")

    calls, bubbles = _patch_flusher_env(monkeypatch, _always_fail)
    mgr = get_manager()
    target = _mk_flusher_target()
    mgr.get_or_create_queue("chat-fl").items.append(
        PendingSend(text="滞留消息", parent=("default", "sess-parent")),
    )

    book = _mk_book("chat-fl")

    async def _body():
        await asyncio.wait_for(
            comm_mod._flusher(object(), target), timeout=10,
        )

    asyncio.run(_body())

    assert calls["n"] == comm_mod.FLUSHER_MAX_CONSECUTIVE_FAILURES
    assert mgr.get_queue("chat-fl") is None  # 放弃后队列清理
    assert len(bubbles) == 1
    session_id, text, sticky = bubbles[0]
    assert session_id == "sess-parent"  # 气泡发往排队发起者会话
    assert "投递失败" in text and "开发者" in text
    assert sticky is True


def test_flusher_success_resets_failure_count(monkeypatch):
    """失败 9 次后第 10 次成功 → 计数清零，不放弃继续服务。"""
    from submit import SubmitResult as SR

    async def _fail_then_ok(idx):
        if idx < 9:
            raise RuntimeError("flaky")
        return SR("chat-fl2", True, asyncio.Queue(), {
            "producer_cancelled": False, "producer_error": None,
        })

    calls, bubbles = _patch_flusher_env(monkeypatch, _fail_then_ok)
    mgr = get_manager()
    target = _mk_flusher_target("chat-fl2")
    _mk_book("chat-fl2")
    mgr.get_or_create_queue("chat-fl2").items.append(
        PendingSend(text="坚韧消息", parent=("default", "sess-parent")),
    )

    async def _body():
        await asyncio.wait_for(
            comm_mod._flusher(object(), target), timeout=10,
        )

    asyncio.run(_body())

    assert calls["n"] == 10
    assert mgr.get_queue("chat-fl2") is None  # 成功投出后队列自然清空退出
    assert bubbles == []  # 未放弃，无气泡


def test_flusher_busy_not_counted(monkeypatch):
    """会话一直忙（running）≠ 失败：不计数、不放弃，队列保留。"""
    from submit import SubmitResult as SR

    calls, bubbles = _patch_flusher_env(
        monkeypatch,
        lambda idx: SR("chat-fl3", True, asyncio.Queue(), {
            "producer_cancelled": False, "producer_error": None,
        }),
    )
    monkeypatch.setattr(
        comm_mod, "get_workspace", lambda aid: MockWorkspace(
            agent_id="child", task_tracker=MockTracker(["running"]),
        ),
    )
    mgr = get_manager()
    target = _mk_flusher_target("chat-fl3")
    mgr.get_or_create_queue("chat-fl3").items.append(
        PendingSend(text="耐心消息", parent=("default", "sess-parent")),
    )

    async def _body():
        task = asyncio.create_task(comm_mod._flusher(object(), target))
        await asyncio.sleep(0.15)  # 快轮询下足够跑多轮 running 分支
        assert calls["n"] == 0  # 从未到达提交（一直 running）
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_body())

    assert bubbles == []
    queue = mgr.get_queue("chat-fl3")
    assert queue is not None and len(queue.items) == 1  # 消息保留未丢


# ── 三路复审修复防复发闸（2026-11-17 R-A/R-B/R-C） ───────────


def test_send_purges_stale_cancel_flag_queue(monkeypatch):
    """stop 残留 cancel_flag 死队列 + idle → send 清死结构排进新活队列
    （R-A #2/R-B #1 防复发：旧代码复用死队列，消息被 stale flag 吞）。"""
    from tools.communicate import agent_tools_communicate

    book = _mk_book("chat-stale")
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["idle"]))
    mgr = get_manager()
    # 构造 stop 残留：死结构（cancel_flag=True，items 空——stop 已清）
    stale = mgr.get_or_create_queue("chat-stale")
    stale.cancel_flag = True

    monkeypatch.setattr(comm_mod, "get_workspace", lambda aid: workspace)

    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)

    monkeypatch.setattr(comm_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(comm_mod, "check_chat_id",
                        lambda v: (v, None, book))

    async def _no_misuse(session_id, target_agent_id=None):
        return None

    monkeypatch.setattr(comm_mod, "check_misuse", _no_misuse)
    monkeypatch.setattr(comm_mod, "get_current_context",
                        lambda: ("default", "u", "sess-stale"))

    chunk = asyncio.run(
        agent_tools_communicate("chat-stale", action="send", text="新消息"),
    )
    text = _chunk_text(chunk)
    assert "已提交" in text, text  # idle + 死结构已清 → 直提
    q = mgr.get_queue("chat-stale")
    # 死结构必须被清掉（drop 后直提不重建队列）或为新建活结构
    assert q is None or q.cancel_flag is False, (
        "死结构残留：cancel_flag 仍为 True"
    )


def _patch_export(monkeypatch, evicted_count, msgs):
    """export 测试环境：返回 (写入路径捕获列表)。"""
    import context as ctx_mod
    import tools.context_export as export_mod
    import session_reader as sr_mod
    from helpers import StubContent, StubMsg

    book = _mk_book("chat-exp", turn_status="completed")
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["idle"]))
    monkeypatch.setattr(export_mod, "get_workspace", lambda aid: workspace)

    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)

    monkeypatch.setattr(export_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(export_mod, "check_chat_id",
                        lambda v: (v, None, book))
    monkeypatch.setattr(export_mod, "current_channel", lambda: "console")

    async def fake_fetch(w, sid, uid, ch):
        return [
            StubMsg("user", "message", [StubContent(text="问")]),
            StubMsg("assistant", "message", [StubContent(text="答")]),
        ]

    monkeypatch.setattr(sr_mod, "fetch_normalized_messages", fake_fetch)

    if evicted_count is None:
        monkeypatch.setattr(ctx_mod, "history_path", lambda w: None)
    else:
        monkeypatch.setattr(
            export_mod, "count_evicted_rows",
            lambda w, a, s: evicted_count,
        )
    return book


def test_export_off_mode_never_renders_evicted(monkeypatch):
    """include_compressed=False → 文件不含驱逐内容（R-A #1 防复发：
    旧代码变量复用把驱逐全量渲染进导出文件）。"""
    from tools.context_export import agent_tools_context_export

    _patch_export(monkeypatch, evicted_count=4, msgs=None)
    import os as _os, tempfile as _tf
    out = _os.path.join(_tf.gettempdir(), "qwenpaw_at_tmp_export_off.md")
    chunk = asyncio.run(agent_tools_context_export(
        chat_id="chat-exp", output_path=out,
    ))
    assert "已导出" in _chunk_text(chunk), _chunk_text(chunk)
    content = open(out, encoding="utf-8").read()
    assert "驱逐历史 4 轮未导出" in content, content[:300]
    assert "驱逐 idx=" not in content, (
        "非导出模式仍渲染了驱逐内容（变量复用回归）"
    )
    assert "[user]" in content and "[assistant]" in content
    # 清理
    import os

    os.remove(out)


def test_export_off_mode_count_sentinel_error(monkeypatch):
    """计数哨兵 -1（读取失败）→ 文案「读取失败」（R-A #5 防复发）。"""
    from tools.context_export import agent_tools_context_export

    _patch_export(monkeypatch, evicted_count=-1, msgs=None)
    import os as _os, tempfile as _tf
    out = _os.path.join(_tf.gettempdir(), "qwenpaw_at_tmp_export_err.md")
    chunk = asyncio.run(agent_tools_context_export(
        chat_id="chat-exp", output_path=out,
    ))
    assert "已导出" in _chunk_text(chunk), _chunk_text(chunk)
    content1 = open(out, encoding="utf-8").read()
    assert "驱逐历史 读取失败" in content1, content1[:300]
    import os

    os.remove(out)


def test_export_off_mode_count_none_disabled(monkeypatch):
    """计数 None（未启用 history DB）→ 文案「未启用」（R-A #5 防复发）。"""
    from tools.context_export import agent_tools_context_export

    _patch_export(monkeypatch, evicted_count=None, msgs=None)
    import os as _os, tempfile as _tf
    out2 = _os.path.join(_tf.gettempdir(), "qwenpaw_at_tmp_export_none.md")
    chunk2 = asyncio.run(agent_tools_context_export(
        chat_id="chat-exp", output_path=out2,
    ))
    assert "已导出" in _chunk_text(chunk2), _chunk_text(chunk2)
    content2 = open(out2, encoding="utf-8").read()
    assert "驱逐历史 未启用" in content2, content2[:300]
    import os

    os.remove(out2)


def test_consumer_task_cancel_never_writes_terminal():
    """消费者 task 被取消 → 不写终态（cancelling() 守卫防假 completed，
    R-A #3 防复发：宿主吞 CancelledError 使异常分支不可达）。"""
    import asyncio

    from manager import get_manager as _gm
    from notifier import NoticeRecord

    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["idle"]))
    record = _mk_book("chat-cc", turn_status="running")
    turn = record.turns[0]
    result = SubmitResult(
        "chat-cc", True, asyncio.Queue(),
        {"producer_cancelled": False, "producer_error": None},
    )
    # 队列不填 sentinel——消费者阻塞在 queue.get()，取消时被吞

    mgr = _gm()
    notice = NoticeRecord(
        target_chat_id="chat-cc",
        target_agent_id="child",
        target_session_id="sub-r0000001",
        wake_agent_id="default", wake_user_id="u",
        wake_session_id="sess-cc", wake_channel="console",
    )
    mgr.register_notice(notice)

    async def _body():
        task = asyncio.create_task(
            consume_and_track(workspace, record, turn, result),
        )
        await asyncio.sleep(0.05)  # 让消费者跑到阻塞点
        assert turn.status == "running"  # 尚未写终态
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_body())

    assert turn.status == "running", (
        f"被取消的消费者写了终态：{turn.status}（假 completed 风险）"
    )
    assert notice.completion_sent is False


# ── 第二轮复审核实修复（2026-11-17 R-A/R-B 二轮 + 动态实验实锤） ──


def test_stop_window_send_delivered(monkeypatch):
    """B-② 修复：stop 的 await 窗口内排队的消息，stop 完成后 flusher
    重启投递（修复前孤立滞留——动态实验实锤「已排队」承诺落空）。"""
    from tools.communicate import agent_tools_communicate

    book = _mk_book("chat-stw", turn_status="running")
    workspace = MockWorkspace(
        agent_id="child",
        task_tracker=MockTracker(["running", "idle", "idle"]),
    )
    monkeypatch.setattr(comm_mod, "get_workspace", lambda aid: workspace)

    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)

    monkeypatch.setattr(comm_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(comm_mod, "check_chat_id",
                        lambda v: (v, None, book))
    monkeypatch.setattr(comm_mod, "get_current_context",
                        lambda: ("default", "u", "sess-stw"))

    async def _no_misuse(session_id, target_agent_id=None):
        return None

    monkeypatch.setattr(comm_mod, "check_misuse", _no_misuse)

    async def _fake_stop(ws, sid, cid):
        # 模拟 stop 的 await 窗口：期间新 send 到达并排队
        chunk = await agent_tools_communicate(
            "chat-stw", action="send", text="窗口内消息",
        )
        assert "已排队" in _chunk_text(chunk)
        return {"cancelled_tools": 0, "stopped": True}

    monkeypatch.setattr(comm_mod, "stop_session", _fake_stop)
    monkeypatch.setattr(comm_mod, "FLUSHER_POLL_SECONDS", 0.01)
    # flusher 的 resolver 校验：qwenpaw 可导入会真跑（cwd 无配置 →
    # 判「会话已删除」丢队列）——patch 为恒命中
    import qwenpaw.app.chats.resolver as _res

    async def _fake_matches(cfg, ref, aid):
        return [object()]

    monkeypatch.setattr(_res, "find_chat_matches", _fake_matches)

    async def _scene():
        # stop 前置段已产生 flag 队列（clear_queue 语义）
        q = get_manager().get_or_create_queue("chat-stw")
        q.cancel_flag = True
        stopped = asyncio.Event()

        async def _old_flusher():
            await stopped.wait()

        old = asyncio.create_task(_old_flusher())
        q.flusher_task = old
        old.cancel()
        await asyncio.sleep(0)          # cancel 落地（task 仍 done）

        chunk = await comm_mod._do_stop(workspace, _loc("chat-stw", book))
        assert "已停止" in _chunk_text(chunk), _chunk_text(chunk)
        await asyncio.sleep(0.15)       # 等 flusher 投完窗口内消息
        return len(book.turns)

    turns_after = asyncio.run(_scene())
    assert turns_after >= 2, (
        f"stop 窗口内排队的消息未被投递（簿记回合数 {turns_after}）"
    )


def test_remove_active_turn_guard(monkeypatch):
    """B-③ 修复：消费者 body-read 让出窗口内 flusher 注册 T2 覆盖同键
    条目 → T1 finally 守卫移除不误删 T2（修复前动态实验实锤 E2 被删）。"""
    book = _mk_book("chat-gd", turn_status="running")
    turn1 = book.turns[0]
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["idle"]))
    mgr = get_manager()
    key = ("child", book.session_id)
    mgr.register_active(key[0], key[1], "chat-gd", parent=None, turn=turn1)

    gate = asyncio.Event()
    import session_reader as sr_mod

    async def _slow_read(ws, obj):
        await gate.wait()
        return "正文"

    monkeypatch.setattr(sr_mod, "read_latest_turn_body", _slow_read)

    result = SubmitResult(
        "chat-gd", True, asyncio.Queue(),
        {"producer_cancelled": False, "producer_error": None},
    )
    result.queue.put_nowait(None)

    async def _body():
        task = asyncio.create_task(
            consume_and_track(workspace, book, turn1, result),
        )
        await asyncio.sleep(0.05)
        # flusher 窗口内注册 T2（同键覆盖，绑 turn2）
        turn2 = TurnRecord()
        mgr.register_active(key[0], key[1], "chat-gd",
                            parent=("p", "s"), turn=turn2)
        gate.set()
        await task
        entry = mgr.active_sessions.get(key)
        assert entry is not None and entry.turn is turn2, (
            "T2 的活跃条目被 T1 消费者误删（拦截失效复发）"
        )

    asyncio.run(_body())


def test_notice_reregister_after_delivery(monkeypatch):
    """A-M1 修复：投递后的陈旧记录不再拦截再注册——续聊后重新注册
    成功，新回合终态再次触发通知。"""
    import tools.notice as notice_mod
    from manager import NoticeRecord

    mgr = get_manager()
    book = _mk_book("chat-re", turn_status="running")
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(["running", "idle"]))
    r1 = mgr.register_notice(NoticeRecord(
        target_chat_id="chat-re", target_agent_id="child",
        target_session_id=book.session_id, wake_agent_id="pa",
        wake_user_id="u", wake_session_id="ps", wake_channel="console",
    ))
    # 回合 1 完成投递（on_turn_finished 内部 create_task 需要运行中循环）
    async def _deliver1():
        assert get_notifier().on_turn_finished("chat-re", "completed", "结果1")

    asyncio.run(_deliver1())
    assert r1.completion_sent is True

    monkeypatch.setattr(notice_mod, "get_workspace", lambda aid: workspace)

    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)

    monkeypatch.setattr(notice_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(notice_mod, "check_chat_id",
                        lambda v: (v, None, book))
    monkeypatch.setattr(notice_mod, "current_channel", lambda: "console")

    async def fake_fetch(w, sid, uid, ch):
        return []

    import session_reader as sr_mod
    monkeypatch.setattr(sr_mod, "fetch_normalized_messages", fake_fetch)

    # 续聊：回合 2 running，用户再次注册——不应被陈旧记录拦截
    book.turns.append(TurnRecord(status="running"))
    chunk = asyncio.run(notice_mod.agent_tools_notice("chat-re"))
    text = _chunk_text(chunk)
    assert "重复注册幂等" not in text, (
        "投递后的陈旧记录仍拦截再注册（A-M1 复发）"
    )
    r2 = mgr.get_notice("chat-re")
    assert r2 is not None and r2 is not r1 and not r2.completion_sent
    # 回合 2 完成 → 通知再次触发

    async def _deliver2():
        assert get_notifier().on_turn_finished("chat-re", "completed", "结果2")

    asyncio.run(_deliver2())


def test_preempted_send_queued(monkeypatch):
    """A-M2 修复：直提被抢占 → 入队复用 flusher 必达（旧稿「未送达，
    稍后重试」依赖 LLM 重试，与 flusher 放回队首不对称）。"""

    import tools.communicate as cm
    from tools.communicate import agent_tools_communicate

    book = _mk_book("chat-pq", turn_status=None)
    book.turns.clear()

    class PreemptTracker(MockTracker):
        def __init__(self):
            super().__init__(["idle", "idle"])
            self.preempted = False

        async def attach_or_start(self, run_key, payload, stream_fn,
                                  owner=None, on_finished=None,
                                  channel_id=None):
            if not self.preempted:
                self.preempted = True
                q = asyncio.Queue()
                q.put_nowait(None)
                self.attached.append((run_key, False))
                return q, False
            return await super().attach_or_start(
                run_key, payload, stream_fn, owner=owner,
                on_finished=on_finished, channel_id=channel_id,
            )

    workspace = MockWorkspace(agent_id="child",
                              task_tracker=PreemptTracker())
    mgr = get_manager()
    monkeypatch.setattr(cm, "get_workspace", lambda aid: workspace)

    async def _fake_loc(cid, _b=book):
        return _loc(cid, _b)

    monkeypatch.setattr(cm, "locate_chat", _fake_loc)
    monkeypatch.setattr(cm, "check_chat_id", lambda v: (v, None, book))
    monkeypatch.setattr(cm, "get_current_context",
                        lambda: ("default", "u", "sess-pq"))

    async def _no_misuse(session_id, target_agent_id=None):
        return None

    monkeypatch.setattr(cm, "check_misuse", _no_misuse)

    async def _fake_parent(ws, aid, sid):
        return None

    monkeypatch.setattr(cm, "resolve_parent_chat_id", _fake_parent)
    monkeypatch.setattr(cm, "FLUSHER_POLL_SECONDS", 0.01)
    # flusher 的 resolver 校验恒命中（同 test_stop_window_send_delivered）
    import qwenpaw.app.chats.resolver as _res

    async def _fake_matches(cfg, ref, aid):
        return [object()]

    monkeypatch.setattr(_res, "find_chat_matches", _fake_matches)

    async def _scene():
        chunk = await agent_tools_communicate(
            "chat-pq", action="send", text="被抢占消息",
        )
        await asyncio.sleep(0.15)  # 同一循环内等 flusher 投递
        return chunk

    chunk = asyncio.run(_scene())
    text = _chunk_text(chunk)
    assert "已排队" in text and "恰好转忙" in text, text
    assert "未送达" not in text, "旧文案回流（消息交付又依赖 LLM 重试）"
    assert len(book.turns) >= 1, (
        "被抢占入队的消息未被 flusher 投递"
    )


# ── v0.1.10 防误用祖先链 gap 修复（通知回合反向 send 测试发现） ──


def test_misuse_main_session_ancestor_blocked(monkeypatch):
    """祖先链 gap 修复回归：qa 回合中 send 主会话（target 不在插件簿记）
    → 显式 target_agent_id 命中 parent 链 → 拦截。修复前簿记反查空串
    恒放行（真机实测：第三方 agent 反向 send 主会话漏拦）。"""
    import utils as utils_mod
    from tools.common import MISUSE_ERROR, check_misuse

    monkeypatch.setattr(utils_mod, "get_current_context",
                        lambda: ("child", "u", "sub-m0000001"))
    mgr = get_manager()
    mgr.register_active("child", "sub-m0000001", "chat-x",
                        parent=("default", "main-1"))

    # 显式 target_agent_id：主会话在祖先链 → 拦截
    result = asyncio.run(check_misuse("main-1", target_agent_id="default"))
    assert result == MISUSE_ERROR

    # 对照：None fallback 簿记反查 → 主会话不在簿记 → 空串 → 放行（旧行为）
    assert asyncio.run(check_misuse("main-1")) is None


def test_misuse_notice_round_passes(monkeypatch):
    """作者拍板例外：notice 触发回合不注册活跃表（notifier._deliver 走
    _submit_turn fire-and-forget）→ is_active 不命中 → 天然放行——
    通知回合已不是问答会话，子 agent 可自由 send 交流。"""
    import utils as utils_mod
    from tools.common import check_misuse

    monkeypatch.setattr(utils_mod, "get_current_context",
                        lambda: ("demo-agent", "u", "sub-notice001"))
    get_manager()  # 活跃表为空（无条目）
    assert asyncio.run(check_misuse("main-1",
                                    target_agent_id="default")) is None


def test_misuse_explicit_agent_off_chain(monkeypatch):
    """显式 agent_id 非祖先（如子 agent send 兄弟会话）→ 放行。"""
    import utils as utils_mod
    from tools.common import check_misuse

    monkeypatch.setattr(utils_mod, "get_current_context",
                        lambda: ("child", "u", "sub-m0000001"))
    get_manager().register_active("child", "sub-m0000001", "chat-x",
                                  parent=("default", "main-1"))
    assert asyncio.run(check_misuse("sib-1", target_agent_id="child")) is None


def test_export_relative_path_resolves_base_dir(monkeypatch, tmp_path):
    """相对路径挂工具基准目录（v0.2.2 修复：旧版 Path(output_path)
    挂宿主进程 cwd，文件落 miniconda Scripts/「消失」）；
    成功消息回显绝对路径。"""
    from tools.context_export import agent_tools_context_export

    _patch_export(monkeypatch, evicted_count=0, msgs=None)
    monkeypatch.setattr(utils_mod, "get_workspace_dir",
                        lambda: str(tmp_path))

    rel = "memory/tmp_export_rel.md"
    chunk = asyncio.run(agent_tools_context_export(
        chat_id="chat-exp", output_path=rel,
    ))
    text = _chunk_text(chunk)
    expected = tmp_path / "memory" / "tmp_export_rel.md"
    assert expected.exists(), f"相对路径未挂基准目录落盘；消息={text}"
    assert str(expected) in text, f"成功消息未回显绝对路径：{text}"
    assert "绝对路径" in text, text


# ── notice 气泡只在最终投递时发（2026-11-18 作者报 bug：busy 重试反复刷屏） ──


def test_notice_bubble_only_on_final_delivery(monkeypatch):
    """console 唤醒 busy 重试期间不发气泡；唤醒成功才发一条聚合气泡。

    修复前 _deliver 先无条件 append 气泡再 _submit_turn——busy 重试
    每 3s 弹一次气泡，直到父会话空闲才停（作者实测复现）。
    """
    import submit as submit_mod
    from notifier import get_notifier

    calls = {"n": 0}

    async def busy_then_ok(ws, sid, uid, ch, text, **kw):
        calls["n"] += 1
        if calls["n"] < 2:
            return submit_mod.SubmitResult("chat-wake", False, None, None)
        return submit_mod.SubmitResult(
            "chat-wake", True, asyncio.Queue(),
            {"producer_cancelled": False, "producer_error": None},
        )

    monkeypatch.setattr(submit_mod, "_submit_turn", busy_then_ok)
    workspace = MockWorkspace(agent_id="parent")
    monkeypatch.setattr(submit_mod, "get_workspace", lambda aid: workspace)

    bubbles = []

    async def _fake_append(session_id, text, *, sticky=False):
        bubbles.append((session_id, text, sticky))

    monkeypatch.setattr(
        "qwenpaw.app.console_push_store.append", _fake_append,
    )

    key = ("parent", "u", "sess-bubble", "console")

    async def _body():
        ok = await get_notifier()._deliver(
            key, object(), [type("P", (), {"text": "通知A", "is_alive": lambda s: True})()],
        )
        assert ok is False, "busy 应返回 False 触发 flusher 忙等"
        ok2 = await get_notifier()._deliver(
            key, object(), [type("P", (), {"text": "通知A", "is_alive": lambda s: True})()],
        )
        assert ok2 is True, "成功应返回 True"
        return bubbles

    bubbles = asyncio.run(_body())
    assert calls["n"] == 2, f"busy 重试共提交 2 次，实际 {calls['n']}"
    assert len(bubbles) == 1, (
        f"busy 重试期间不应发气泡，最终成功才发 1 条；实际 {len(bubbles)} 条"
    )
    assert "通知A" in bubbles[0][1]


def test_notice_bubble_on_submit_failure(monkeypatch):
    """唤醒提交真失败（异常）时仍尽力发气泡（通知文本至少可见），
    且不重试（返回 True 批次自然消失）。"""
    import submit as submit_mod
    from notifier import get_notifier

    calls = {"n": 0}

    async def _always_raise(ws, sid, uid, ch, text, **kw):
        calls["n"] += 1
        raise RuntimeError("host api broken")

    monkeypatch.setattr(submit_mod, "_submit_turn", _always_raise)
    workspace = MockWorkspace(agent_id="parent")
    monkeypatch.setattr(submit_mod, "get_workspace", lambda aid: workspace)

    bubbles = []

    async def _fake_append(session_id, text, *, sticky=False):
        bubbles.append((session_id, text, sticky))

    monkeypatch.setattr(
        "qwenpaw.app.console_push_store.append", _fake_append,
    )

    key = ("parent", "u", "sess-fail", "console")

    async def _body():
        return await get_notifier()._deliver(
            key, object(), [type("P", (), {"text": "通知B", "is_alive": lambda s: True})()],
        )

    ok = asyncio.run(_body())
    assert ok is True, "真失败不重试（返回 True 批次消失）"
    assert calls["n"] == 1, "真失败只提交一次"
    assert len(bubbles) == 1, "真失败仍发一条气泡保证通知文本可见"
    assert "通知B" in bubbles[0][1]
