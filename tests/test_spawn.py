# -*- coding: utf-8 -*-
"""spawn 工具测试：template 校验 / 深度限制 / 簿记与活跃表 / 前台超时。"""

import asyncio

import tools.spawn as spawn_mod
from helpers import MockTracker, MockWorkspace, make_turn, now

from manager import SpawnRecord, TurnRecord, get_manager
from submit import SubmitResult


class _Chunk:
    def __init__(self, text, is_error=False):
        self.text = text
        self.is_error = is_error

    @property
    def content(self):
        return [type("_B", (), {"text": self.text})()]


def _patch(monkeypatch, submit_result=None, statuses=("idle",),
           queue_mode="immediate"):
    """统一 patch：profile/name/workspace/_submit_turn。

    queue_mode: immediate=消费者立即收尾（预填 sentinel）；
                hang=消费者阻塞在空队列（前台超时测试用）。
    """
    monkeypatch.setattr(spawn_mod, "_get_profile", lambda tid: object())
    monkeypatch.setattr(spawn_mod, "_agent_name", lambda aid: "demo-agent")
    workspace = MockWorkspace(agent_id="child",
                              task_tracker=MockTracker(list(statuses)))
    monkeypatch.setattr(spawn_mod, "get_workspace", lambda aid: workspace)
    monkeypatch.setattr(spawn_mod, "get_workspace_manager", lambda: None)

    submitted = {}

    async def fake_submit(ws, sid, uid, ch, text, **kw):
        submitted["text"] = text
        submitted["kw"] = kw
        if submit_result is not None:
            return submit_result
        queue = asyncio.Queue()
        if queue_mode == "immediate":
            queue.put_nowait(None)   # 消费者立即收尾
        return SubmitResult("chat-new", True, queue, {
            "producer_cancelled": False, "producer_error": None,
        })

    monkeypatch.setattr(spawn_mod, "_submit_turn", fake_submit)
    # 消费者用到的 read_latest_turn_body → session_reader mock
    import session_reader

    async def fake_fetch(w, sid, uid, ch):
        return []

    monkeypatch.setattr(session_reader, "fetch_normalized_messages", fake_fetch)
    return submitted


def _chunk_text(chunk):
    return chunk.content[0].text


def test_spawn_empty_task():
    from tools.spawn import agent_tools_spawn

    chunk = asyncio.run(agent_tools_spawn(task=""))
    assert "task 不能为空" in _chunk_text(chunk)


def test_spawn_invalid_template(monkeypatch):
    from tools.spawn import agent_tools_spawn

    monkeypatch.setattr(spawn_mod, "_get_profile", lambda tid: None)
    chunk = asyncio.run(agent_tools_spawn(task="hi", template="ghost"))
    assert "不存在或未启用" in _chunk_text(chunk)
    assert "list_agent" in _chunk_text(chunk)


def test_spawn_depth_limit(monkeypatch):
    from tools.spawn import agent_tools_spawn

    _patch(monkeypatch)
    # 发起者 depth=5（在簿记）→ 子 depth=6 > 5 → 拒绝
    # （测试环境无 qwenpaw contextvar，get_current_context 回落 default）
    mgr = get_manager()
    mgr.inject_spawn_record(
        "chat-parent", agent_id="default", session_id="default",
        template="default", depth=5, turns=[make_turn(status="running")],
    )
    chunk = asyncio.run(agent_tools_spawn(task="hi"))
    assert "链深上限" in _chunk_text(chunk)


def test_spawn_success_bookkeeping(monkeypatch):
    from tools.spawn import agent_tools_spawn

    submitted = _patch(monkeypatch)
    chunk = asyncio.run(agent_tools_spawn(task="干活去", template=""))
    text = _chunk_text(chunk)
    assert "[CHAT_ID: chat-new]" in text
    assert "agent_tools_wait(chat_id)" in text

    # 身份前缀已注入
    assert "Agent「demo-agent」" in submitted["text"]
    assert "干活去" in submitted["text"]
    # request_context 传入（测试环境 qwenpaw 可导入 → 官方构造，
    # contextvar 空 → parent_session_id 为空串回落）
    rc = submitted["kw"].get("request_context") or {}
    assert rc.get("_spawn_subagent") is True
    assert rc.get("parent_session_id") in ("", "default")

    # 簿记 + 活跃表（template="" → 当前 agent 身份，回落上下文 default）
    mgr = get_manager()
    record = mgr.get_record("chat-new")
    assert record is not None
    assert record.agent_id == "default" and record.template == "default"
    assert record.depth == 1
    assert record.parent == ("default", "default")
    assert len(record.turns) == 1
    # 回合状态：running（消费者未跑）或 completed（测试 asyncio.run 收尾
    # 让消费者跑完——本机实测语义）；绝不残留其他状态
    assert record.turns[0].status in ("running", "completed"), (
        record.turns[0].status,
    )
    # 活跃表与回合状态联动：running=已登记；completed=消费者已收尾移除
    if record.turns[0].status == "running":
        assert mgr.is_active(record.agent_id, record.session_id)
    # session_id 形态 sub-{8hex}
    assert record.session_id.startswith("sub-") and len(record.session_id) == 12


def test_spawn_foreground_timeout(monkeypatch):
    from tools.spawn import agent_tools_spawn

    _patch(monkeypatch, statuses=("running",), queue_mode="hang")
    chunk = asyncio.run(agent_tools_spawn(task="hi", background=False,
                                          timeout=1))
    text = _chunk_text(chunk)
    # 3.9⑤ 句式（决策 16：完整 chat_id 只出现一次——不带 [CHAT_ID] 头）
    assert "等待超时" in text and "仍运行中" in text
    assert "未停止、不受影响" in text
    assert text.count("chat-new") == 1, text


def test_spawn_foreground_complete(monkeypatch):
    from tools.spawn import agent_tools_spawn

    # 前台：提交后立即 idle（消费者终态未写）→ stale 分支读 session 文件
    _patch(monkeypatch, statuses=("idle",))
    chunk = asyncio.run(agent_tools_spawn(task="hi", background=False,
                                          timeout=5))
    text = _chunk_text(chunk)
    # 前台完成返回 wait_for_terminal 终态文本（无 CHAT_ID 头）
    assert "completed" in text, text
