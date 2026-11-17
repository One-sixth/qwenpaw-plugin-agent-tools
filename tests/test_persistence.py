# -*- coding: utf-8 -*-
"""persistence 测试：spawn 身份写入 chat.meta + chats.json 恢复回填（v0.1.8）。"""

import asyncio
import json
from types import SimpleNamespace

from helpers import make_record

from manager import get_manager
from persistence import (
    META_KEY,
    build_spawn_meta,
    persist_spawn_identity,
    restore_ledger_from_chats,
)
from utils import resolve_chat_id_input

CHAT = "ab12cd34-5678-4abc-8def-112233445566"


def _record(parent=("default", "1794832098524-hq4cvko")):
    return make_record(CHAT, agent_id="demo-agent", session_id="sub-1a2b3c4d",
                       user_id="u", channel="console", template="demo-agent",
                       depth=1, parent=parent)


# ── build_spawn_meta ──


def test_build_spawn_meta_fields():
    meta = build_spawn_meta(_record(), parent_chat_id="55dd955c-0000")
    assert meta["template"] == "demo-agent"
    assert meta["depth"] == 1
    assert meta["parent_agent_id"] == "default"
    assert meta["parent_session_id"] == "1794832098524-hq4cvko"
    assert meta["parent_chat_id"] == "55dd955c-0000"
    assert "spawned_at" in meta
    # 无 parent：parent 三字段为 None
    meta2 = build_spawn_meta(_record(parent=None), None)
    assert meta2["parent_agent_id"] is None
    assert meta2["parent_session_id"] is None
    assert meta2["parent_chat_id"] is None


# ── persist_spawn_identity（mock chat_manager，真事务模式） ──


class _FakeRepo:
    def __init__(self, chats):
        self.chats = {c.id: c for c in chats}

    async def get_chat(self, cid):
        return self.chats.get(cid)

    async def upsert_chat(self, spec):
        self.chats[spec.id] = spec


def _fake_chat_manager(chats):
    from qwenpaw.app.chats.models import ChatSpec

    cm = SimpleNamespace()
    cm._lock = asyncio.Lock()
    cm._repo = _FakeRepo(chats)
    return cm


def _spec(cid=CHAT, meta=None):
    from qwenpaw.app.chats.models import ChatSpec

    return ChatSpec(id=cid, session_id="sub-1a2b3c4d", user_id="u",
                    channel="console", meta=meta or {})


def test_persist_merges_and_keeps_sibling_keys():
    """写入 agent_tools 命名空间 + 宿主兄弟键（runtime_context）保留。"""
    spec = _spec(meta={"runtime_context": {"model": {"k": "v"}}})
    cm = _fake_chat_manager([spec])
    ws = SimpleNamespace(chat_manager=cm)

    ok = asyncio.run(persist_spawn_identity(ws, _record(), "55dd955c-0000"))
    assert ok is True
    saved = cm._repo.chats[CHAT]
    assert saved.meta[META_KEY]["template"] == "demo-agent"
    assert saved.meta["runtime_context"] == {"model": {"k": "v"}}  # 兄弟键不动
    assert spec.meta.get(META_KEY) is None  # 原 spec 不被原地改（model_copy）


def test_persist_missing_chat_returns_false():
    cm = _fake_chat_manager([])
    ws = SimpleNamespace(chat_manager=cm)
    assert asyncio.run(persist_spawn_identity(ws, _record())) is False


def test_persist_no_chat_manager():
    ws = SimpleNamespace()  # 无 chat_manager 属性
    assert asyncio.run(persist_spawn_identity(ws, _record())) is False


# ── restore_ledger_from_chats ──


def _write_chats_json(path, chats):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "chats": chats}, ensure_ascii=False),
        encoding="utf-8",
    )


def _fake_profiles(tmp_path):
    ws1 = tmp_path / "ws_demo-agent"
    ws2 = tmp_path / "ws_default"
    _write_chats_json(ws1 / "chats.json", [
        {   # 插件 spawn 会话（带身份 meta）
            "id": CHAT, "session_id": "sub-1a2b3c4d", "user_id": "u",
            "channel": "console",
            "meta": {META_KEY: {
                "template": "demo-agent", "depth": 2,
                "parent_agent_id": "default",
                "parent_session_id": "1794832098524-hq4cvko",
                "parent_chat_id": "55dd955c-0000",
                "spawned_at": "2026-11-17T22:38:21",
            }},
        },
        {   # 主会话（无 meta）→ 不回填
            "id": "ffffffff-0000-4000-8000-000000000002",
            "session_id": "1794832098524-hq4cvko", "user_id": "u",
            "channel": "console", "meta": {},
        },
    ])
    _write_chats_json(ws2 / "chats.json", [])  # 空 chats.json
    return {
        "demo-agent": SimpleNamespace(enabled=True, workspace_dir=str(ws1)),
        "default": SimpleNamespace(enabled=True, workspace_dir=str(ws2)),
        "off": SimpleNamespace(enabled=False, workspace_dir=str(tmp_path / "off")),
    }


def test_restore_backfills_and_resolves(tmp_path, monkeypatch):
    """恢复回填：restored=True / 身份字段齐 / 主会话跳过；resolve 短 id 命中。"""
    import qwenpaw.config.utils as cfg_utils

    profiles = _fake_profiles(tmp_path)
    monkeypatch.setattr(cfg_utils, "load_config",
                        lambda: SimpleNamespace(agents=SimpleNamespace(
                            profiles=profiles)))
    mgr = get_manager()
    assert mgr.all_records() == []  # 前置：簿记空（模拟热装/重启后）

    restored = restore_ledger_from_chats(mgr)
    assert restored == 1
    rec = mgr.get_record(CHAT)
    assert rec is not None and rec.restored is True
    assert rec.agent_id == "demo-agent" and rec.template == "demo-agent"
    assert rec.session_id == "sub-1a2b3c4d" and rec.depth == 2
    assert rec.parent == ("default", "1794832098524-hq4cvko")
    # spawned_at 还原 created_at（list_session 显示真实 spawn 时间）
    from datetime import datetime as _dt
    assert rec.created_at == _dt(2026, 11, 17, 22, 38, 21)
    assert mgr.get_record("ffffffff-0000-4000-8000-000000000002") is None

    # 恢复后短 id 全链路命中（走 resolve_chat_id_input，known_ids 含回填）
    cid, err, healed = resolve_chat_id_input("Ab12Cd34")
    assert cid == CHAT and err is None and healed is rec


def test_restore_skips_existing_records(tmp_path, monkeypatch):
    """内存已有（正常注册过/已恢复）→ 跳过不覆盖。"""
    import qwenpaw.config.utils as cfg_utils

    profiles = _fake_profiles(tmp_path)
    monkeypatch.setattr(cfg_utils, "load_config",
                        lambda: SimpleNamespace(agents=SimpleNamespace(
                            profiles=profiles)))
    mgr = get_manager()
    mgr.inject_spawn_record(CHAT, agent_id="demo-agent",
                            session_id="sub-1a2b3c4d",
                            user_id="u", channel="console")
    assert restore_ledger_from_chats(mgr) == 0
    rec = mgr.get_record(CHAT)
    assert rec.restored is False  # 原记录保留


def test_resolve_miss_triggers_restore(tmp_path, monkeypatch):
    """resolve 短 id 无匹配 → 自动触发恢复 → 重解析命中（全链路）。"""
    import qwenpaw.config.utils as cfg_utils

    profiles = _fake_profiles(tmp_path)
    monkeypatch.setattr(cfg_utils, "load_config",
                        lambda: SimpleNamespace(agents=SimpleNamespace(
                            profiles=profiles)))
    mgr = get_manager()
    assert mgr.all_records() == []

    cid, err, healed = resolve_chat_id_input("ab12cd34")
    assert cid == CHAT and err is None
    assert healed is not None and healed.restored is True
    # 回填即缓存：二次 resolve 走内存，不再扫（restore 幂等，结果一致）
    cid2, err2, _ = resolve_chat_id_input("ab12cd34")
    assert cid2 == CHAT and err2 is None


def test_restore_without_qwenpaw_config(tmp_path, monkeypatch):
    """load_config 不可用 → 返回 0 不炸（恢复跳过）。"""
    import qwenpaw.config.utils as cfg_utils

    def _boom():
        raise RuntimeError("no config")

    monkeypatch.setattr(cfg_utils, "load_config", _boom)
    assert restore_ledger_from_chats(get_manager()) == 0


def test_restore_missing_chats_json(tmp_path, monkeypatch):
    """workspace 目录无 chats.json → 跳过该 agent 不炸。"""
    import qwenpaw.config.utils as cfg_utils

    profiles = {"demo-agent": SimpleNamespace(
        enabled=True, workspace_dir=str(tmp_path / "no_such_dir"))}
    monkeypatch.setattr(cfg_utils, "load_config",
                        lambda: SimpleNamespace(agents=SimpleNamespace(
                            profiles=profiles)))
    assert restore_ledger_from_chats(get_manager()) == 0


def test_tool_docstrings_mention_short_id():
    """schema 回归锁定：收 chat_id 的 5 个工具参数描述 + spawn 返回提示
    必须提及短 id 支持（2026-11-17 作者拍板——无上下文调用方的可发现性，
    防未来改动丢失提示）。"""
    from tools.communicate import agent_tools_communicate
    from tools.context_export import agent_tools_context_export
    from tools.context_read import agent_tools_context_read
    from tools.notice import agent_tools_notice
    from tools.spawn import agent_tools_spawn, _WAIT_HINT
    from tools.wait import agent_tools_wait

    for fn in (agent_tools_wait, agent_tools_communicate,
               agent_tools_notice):
        assert "8 位 hex 短 id" in fn.__doc__, f"{fn.__name__} 缺短 id 提示"
    for fn in (agent_tools_context_read, agent_tools_context_export):
        assert "8 位 hex 短 id" in fn.__doc__, f"{fn.__name__} 缺短 id 提示"
    assert "8 位 hex 简写" in _WAIT_HINT
    # spawn 成功文案实际包含 _WAIT_HINT（背景路径拼接）
    assert "CHAT_ID" in _WAIT_HINT
