# -*- coding: utf-8 -*-
"""manager 簿记测试：注入 / 祖先链 / 队列 / TTL / LRU。"""

from datetime import datetime, timedelta

from helpers import make_turn, now

from manager import (
    MAX_SPAWN_DEPTH,
    AgentToolsManager,
    PendingSend,
    get_manager,
)


def _rec(chat_id, session_id="sub-x", **kw):
    from manager import SpawnRecord

    return SpawnRecord(
        agent_id=kw.get("agent_id", "child"), session_id=session_id,
        user_id="u", channel="console", chat_id=chat_id, **{
            k: v for k, v in kw.items() if k != "agent_id"
        },
    )


def test_inject_and_lookup():
    mgr = get_manager()
    r = mgr.inject_spawn_record("chat-1", session_id="sub-aaaa1111")
    assert mgr.get_record("chat-1") is r
    assert mgr.get_record_by_session("sub-aaaa1111") is r
    assert mgr.get_record_by_session("sub-none") is None


def test_append_turn_no_book_raises():
    import pytest

    mgr = get_manager()
    with pytest.raises(KeyError):
        mgr.append_turn("no-such", make_turn())


def test_ancestor_chain():
    mgr = get_manager()
    mgr.register_active("a", "grand", "c1", parent=None)
    mgr.register_active("a", "mid", "c2", parent=("a", "grand"))
    mgr.register_active("a", "cur", "c3", parent=("a", "mid"))
    assert mgr.is_on_ancestor_chain("a", "cur", "a", "mid")
    assert mgr.is_on_ancestor_chain("a", "cur", "a", "grand")
    assert not mgr.is_on_ancestor_chain("a", "cur", "a", "other")
    assert not mgr.is_on_ancestor_chain("a", "mid", "a", "cur")
    # 环不死循环
    mgr.register_active("b", "x", "c4", parent=("b", "y"))
    mgr.register_active("b", "y", "c5", parent=("b", "x"))
    assert not mgr.is_on_ancestor_chain("b", "x", "b", "z")


def test_queue_clear_and_drop():
    mgr = get_manager()
    q = mgr.get_or_create_queue("chat-q")
    q.items.append(PendingSend(text="hi", parent=None))
    assert mgr.clear_queue("chat-q") == 1
    assert q.cancel_flag and not q.items
    mgr.drop_queue("chat-q")
    assert mgr.get_queue("chat-q") is None


def test_sweep_expired():
    mgr = get_manager()
    old = make_turn(status="completed", finished=now() - timedelta(hours=25))
    mgr.inject_spawn_record("chat-old", session_id="sub-old00001", turns=[old])
    fresh = make_turn(status="completed", finished=now())
    mgr.inject_spawn_record("chat-new", session_id="sub-new00001", turns=[fresh])
    expired, evicted = mgr.sweep_expired(ttl_hours=24, max_records=500)
    assert expired == 1 and evicted == 0
    assert mgr.get_record("chat-old") is None
    assert mgr.get_record("chat-new") is not None


def test_lru_eviction():
    mgr = get_manager()
    for i in range(505):
        t = make_turn(status="completed",
                      finished=now() - timedelta(minutes=i))
        mgr.inject_spawn_record(f"chat-{i:04d}", session_id=f"sub-{i:08d}",
                                turns=[t])
    expired, evicted = mgr.sweep_expired(ttl_hours=24, max_records=500)
    assert evicted == 5 and len(mgr.spawn_records) == 500


def test_status_follows_latest_turn():
    mgr = get_manager()
    r = mgr.inject_spawn_record("chat-s", turns=[make_turn(status="running")])
    assert r.status == "running"
    r.turns.append(make_turn(status="completed", finished=now()))
    assert r.status == "completed"


def test_notice_registry():
    from manager import NoticeRecord

    mgr = get_manager()
    rec = mgr.register_notice(NoticeRecord(
        target_chat_id="chat-n", target_agent_id="a",
        target_session_id="sub-n", wake_agent_id="pa",
        wake_user_id="u", wake_session_id="s-ps", wake_channel="console",
    ))
    assert mgr.get_notice("chat-n") is rec
    assert mgr.cancel_notice("chat-n") is rec
    assert rec.cancelled
    # TTL 清理：已取消 + 超龄 → 删
    rec.registered_at = now() - timedelta(hours=25)
    mgr.sweep_expired(ttl_hours=24)
    assert mgr.get_notice("chat-n") is None


def test_depth_limit_constant():
    assert MAX_SPAWN_DEPTH == 5


# ── B3：spawn_managed 托管（v0.2.1） ────────────────────


def test_spawn_managed_falls_back_without_resources():
    """B3：resources 未注入（测试/直调）→ 回落裸 create_task（旧行为）。"""
    import asyncio

    async def _body():
        async def _noop():
            await asyncio.sleep(0)

        mgr = get_manager()
        assert mgr.resources is None
        task = mgr.spawn_managed(_noop(), "t")
        assert isinstance(task, asyncio.Task)
        task.cancel()

    asyncio.run(_body())


def test_spawn_managed_uses_injected_resources():
    """B3：resources 注入 → 走 ResourceScope.task 托管（卸载自动清理）。"""
    import asyncio

    created = {}

    class _FakeScope:
        def task(self, coro, description):
            created["description"] = description
            return asyncio.create_task(coro)

    async def _body():
        async def _noop():
            await asyncio.sleep(0)

        mgr = get_manager()
        mgr.resources = _FakeScope()
        try:
            task = mgr.spawn_managed(_noop(), "notice_flusher")
            assert created["description"] == "notice_flusher"
            assert isinstance(task, asyncio.Task)
            task.cancel()
        finally:
            mgr.resources = None  # conftest 亦兜底归位

    asyncio.run(_body())


def test_spawn_managed_propagates_scope_error():
    """B3 契约锁定（复审 C 发现的根源契约）：scope.task 抛异常（模拟
    StaleContext/ResourceError）时 spawn_managed 原样传播，且
    flusher_task 不被部分赋值（异常发生在赋值前，无半初始化状态）。"""
    import asyncio

    class _BrokenScope:
        def task(self, coro, description):
            coro.close()  # 宿主 resources.task 的 _check 失败行为（防泄漏）
            raise RuntimeError("StaleContext: scope is closing")

    async def _body():
        import pytest

        async def _noop():
            await asyncio.sleep(0)

        mgr = get_manager()
        mgr.resources = _BrokenScope()
        try:
            with pytest.raises(RuntimeError, match="scope is closing"):
                mgr.spawn_managed(_noop(), "notice_flusher")
        finally:
            mgr.resources = None  # conftest 亦兜底归位
        # 异常传播后无残留任务引用（赋值未发生）
        assert mgr.background_tasks == set()

    asyncio.run(_body())
