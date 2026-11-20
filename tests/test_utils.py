# -*- coding: utf-8 -*-
"""utils 测试：chat_id 校验自愈 / session 形态 / 时间格式化 / 身份前缀。"""

from datetime import datetime

from helpers import make_turn

from manager import SpawnRecord, get_manager
from utils import (
    build_identity_prefix,
    format_local_offset,
    looks_like_session_id,
    resolve_chat_id_input,
    resolve_chat_reference,
)

CHAT = "ab12cd34-5678-4abc-8def-112233445566"


def _lookup(session_id):
    if session_id == "sub-1a2b3c4d":
        return SpawnRecord(agent_id="child", session_id=session_id,
                           user_id="u", channel="console", chat_id=CHAT)
    return None


def test_resolve_uuid_passthrough():
    cid, err = resolve_chat_reference(CHAT.upper())
    assert cid == CHAT and err is None


def test_resolve_session_form_rejected():
    """session_id 形态（含簿记内有记录的 sub-）一律拒绝（2026-11-17
    作者拍板：只支持 chat_id，自愈路径移除）。"""
    from manager import get_manager as _gm

    _gm().reset_manager()
    _gm().inject_spawn_record(
        CHAT, agent_id="child", session_id="sub-1a2b3c4d",
        user_id="u", channel="console",
    )
    # 即便簿记内有该 session 的记录，session_id 输入也拒绝
    cid, err = resolve_chat_reference("sub-1a2b3c4d")
    assert cid is None and "session_id 形态" in err

    cid2, err2 = resolve_chat_reference("1794832098524-hq4cvko")
    assert cid2 is None and "session_id 形态" in err2

    cid3, err3 = resolve_chat_reference(
        "default:to:demo-agent:1793214532425:1794972e",
    )
    assert cid3 is None and "session_id 形态" in err3


def test_resolve_invalid():
    cid, err = resolve_chat_reference("garbage!!")
    assert cid is None and err and "UUID" in err


def test_resolve_short_id():
    """短 id = 长 id 第一段（严格 8 位 hex）：命中 1 → 解析；≥2 → 歧义
    显式拒绝（禁止静默取第一个）；0 → 无匹配（2026-11-17 作者拍板）。"""
    mgr = get_manager()
    mgr.reset_manager()
    c1 = "ab12cd34-5678-4abc-8def-112233445566"
    c2 = "ffeeddcc-0000-4000-8000-000000000001"
    c3 = "ab12cd34-a000-4abc-8def-112233445588"   # 与 c1 第一段相同（撞对）
    mgr.inject_spawn_record(c1, agent_id="child", session_id="sub-s000001",
                            user_id="u", channel="console")
    mgr.inject_spawn_record(c2, agent_id="child", session_id="sub-s000002",
                            user_id="u", channel="console")

    ids = [r.chat_id for r in mgr.all_records()]
    # 命中 1 条（大小写混合输入）
    cid, err = resolve_chat_reference("Ab12Cd34", ids)
    assert cid == c1 and err is None
    # 命中 0 条
    cid, err = resolve_chat_reference("deadbeef", ids)
    assert cid is None and "无匹配" in err
    # 严格 8 位：7 位 / 9 位都拒绝
    cid, err = resolve_chat_reference("ab12cd3", ids)
    assert cid is None and "UUID" in err
    cid, err = resolve_chat_reference("ab12cd345", ids)
    assert cid is None and "UUID" in err
    # 完整 UUID 仍优先走全量形态
    cid, err = resolve_chat_reference(c1, ids)
    assert cid == c1 and err is None

    # 制造撞对：c3 与 c1 第一段相同 → 短 id 歧义显式拒绝
    mgr.inject_spawn_record(c3, agent_id="child", session_id="sub-s000003",
                            user_id="u", channel="console")
    ids2 = [r.chat_id for r in mgr.all_records()]
    cid, err = resolve_chat_reference("ab12cd34", ids2)
    assert cid is None and "多个重复" in err and "完整的长 id" in err
    mgr.reset_manager()


def test_looks_like_three_forms():
    assert looks_like_session_id("1794832098524-hq4cvko")
    assert looks_like_session_id("sub-1a2b3c4d")
    assert looks_like_session_id(
        "default:to:demo-agent:1793214532425:1794972e")
    assert not looks_like_session_id(CHAT)


def test_format_local_offset():
    s = format_local_offset(datetime(2026, 11, 16, 11, 12, 45, 102080))
    assert s.startswith("2026-11-16 11:12:45.102080+") and len(s) > 30
    s2 = format_local_offset("2026-08-14T21:18:27.370181")
    assert s2.startswith("2026-08-14 21:18:27.370181+")
    s3 = format_local_offset(datetime(2026, 1, 1, 8, 0, 0))
    assert "." not in s3
    # 解析失败原样返回
    assert format_local_offset("garbage") == "garbage"


def test_identity_prefix():
    # 有 chat_id：完整版——UUID 全文只出现一次，回访指引就近指代
    cid = "32b6b25e-f641-4e92-a1e3-f0e5423348df"
    p1 = build_identity_prefix("demo-agent", "default", cid)
    assert "Agent「demo-agent」" in p1
    assert "agent_id: default" in p1
    assert f"chat_id: {cid}" in p1
    assert p1.count(cid) == 1
    assert "对方会读取到" in p1
    assert "无需再向对方发消息" in p1
    assert "这个 chat_id" in p1
    assert "agent_tools_communicate" in p1
    # 无 chat_id：降级版——无回访指引，其余保留
    p2 = build_identity_prefix("demo-agent", "default", None)
    assert "Agent「demo-agent」" in p2 and "agent_id: default" in p2
    assert "chat_id" not in p2
    assert "对方会读取到" in p2 and "无需再向对方发消息" in p2
    assert "agent_tools_communicate" not in p2


def test_resolve_parent_chat_id_degrades(monkeypatch):
    """resolver 异常 → 异常防御路径返回 None，不抛异常不阻塞消息。"""
    import asyncio

    import qwenpaw.app.chats.resolver as _resolver_mod
    from utils import resolve_parent_chat_id

    async def _boom(cfg, ref, aid):
        raise RuntimeError("resolver 挂了")

    monkeypatch.setattr(_resolver_mod, "find_chat_matches", _boom)

    async def _run():
        return await resolve_parent_chat_id(
            object(), "default", "1794832098524-hq4cvko",
        )

    assert asyncio.run(_run()) is None


def test_toolchunk_helpers():
    from utils import format_error, make_error, make_success

    chunk = make_success("ok")
    assert chunk.content[0].text == "ok"
    chunk2 = make_error("查看", "没了", "先 list")
    text = chunk2.content[0].text
    assert "查看失败：没了" in text and "先 list" in text
    assert format_error("a", "b") == "a失败：b"


def test_resolve_chat_id_input_integration():
    """resolve_chat_id_input 集成链路（utils→manager 单例，入口包装自动
    取簿记 known_ids）——v0.1.7 hotfix 回归锁定：all_records 漏括号把
    method 当 property 迭代（'method' object is not iterable），纯函数
    测试覆盖不到此接线，专用集成用例堵盲区。"""
    mgr = get_manager()
    mgr.reset_manager()
    c1 = "ab12cd34-5678-4abc-8def-112233445566"
    mgr.inject_spawn_record(c1, agent_id="child", session_id="sub-i000001",
                            user_id="u", channel="console")
    # 完整 UUID：全链路 + healed 簿记记录回传
    cid, err, healed = resolve_chat_id_input(c1.upper())
    assert cid == c1 and err is None and healed is not None
    # 短 id：known_ids 由包装函数自动从簿记取得
    cid, err, _ = resolve_chat_id_input("Ab12Cd34")
    assert cid == c1 and err is None
    # 歧义：簿记内两条同前缀 → 显式拒绝
    c3 = "ab12cd34-a000-4abc-8def-112233445588"
    mgr.inject_spawn_record(c3, agent_id="child", session_id="sub-i000003",
                            user_id="u", channel="console")
    cid, err, _ = resolve_chat_id_input("ab12cd34")
    assert not cid and "多个重复" in err
    # session_id 形态：全链路拒绝（spawn 子会话形态 sub-{6~12位hex}）
    cid, err, _ = resolve_chat_id_input("sub-1a2b3c4d")
    assert not cid and "session_id 形态" in err
    mgr.reset_manager()


# ── B2：ToolContext 注入（v0.2.1） ──────────────────────


class _FakeToolCtx:
    """最小 ToolContext 替身：只带 get_current_context 消费的字段。"""

    def __init__(self, workspace_id="demo-agent", session_id="1795000000000-ab12cd"):
        self.workspace_id = workspace_id
        self.session_id = session_id


def test_get_current_context_prefers_injected_toolcontext(monkeypatch):
    """B2：_CALL_CTX 注入优先——agent_id=workspace_id / session_id 走
    ToolContext 公共面（与内核 contextvar 同源），user_id 无公共对应物
    仍从内核 API 补齐。"""
    import utils as utils_mod

    monkeypatch.setattr(
        "qwenpaw.app.agent_context.get_current_user_id", lambda: "u-42",
    )
    token = utils_mod._CALL_CTX.set(_FakeToolCtx())
    try:
        got = utils_mod.get_current_context()
    finally:
        utils_mod._CALL_CTX.reset(token)
    assert got == ("demo-agent", "u-42", "1795000000000-ab12cd")


def test_get_current_context_injected_empty_normalizes_default(monkeypatch):
    """B2：注入对象字段为空 → 归一 default（与内核路径 or "default" 一致）。"""
    import utils as utils_mod

    monkeypatch.setattr(
        "qwenpaw.app.agent_context.get_current_user_id", lambda: None,
    )
    token = utils_mod._CALL_CTX.set(_FakeToolCtx(workspace_id="", session_id=None))
    try:
        got = utils_mod.get_current_context()
    finally:
        utils_mod._CALL_CTX.reset(token)
    assert got == ("default", "default", "default")


def test_get_current_context_without_injection_returns_str_tuple():
    """B2：无注入（直调工具函数 / 纯 mock 测试）→ 原内核路径不变。"""
    import utils as utils_mod

    agent_id, user_id, session_id = utils_mod.get_current_context()
    assert all(isinstance(x, str) for x in (agent_id, user_id, session_id))



# ── resolve_workspace_path / get_workspace_dir（v0.2.2 导出路径修复） ──


def test_resolve_workspace_path_relative_uses_base(monkeypatch, tmp_path):
    """相对路径挂基准目录（get_tool_base_dir 三级回落 wrapper）。"""
    import utils as u

    monkeypatch.setattr(u, "get_workspace_dir", lambda: str(tmp_path))
    got = u.resolve_workspace_path("memory/export.md")
    assert got == str(tmp_path / "memory" / "export.md")


def test_resolve_workspace_path_absolute_untouched(monkeypatch):
    """绝对路径照用，不挂基准目录（normpath 归一即可）。"""
    import os

    import utils as u

    monkeypatch.setattr(u, "get_workspace_dir", lambda: r"D:\base")
    abs_path = os.path.join(os.getcwd(), "somewhere", "out.md")
    assert u.resolve_workspace_path(abs_path) == os.path.normpath(abs_path)


# ── 截断全文落盘（v0.2.4，QwenPaw 大 tool_result 同款机制） ──


def test_truncate_tail_with_file_pointer():
    """落盘成功 → 尾部含文件路径 + 总行数 + 从第X行开始续读（含被截断行）。"""
    import utils as u

    tail = u.truncate_tail(16000, r"C:\ws\tool_results\agent-tools-abc.txt",
                           42, 200)
    assert tail.startswith("<<<TRUNCATED>>>")
    assert "agent-tools-abc.txt" in tail
    assert "共 200 行" in tail
    assert "从第 42 行开始续读" in tail
    assert "包含被截断的那行" in tail


def test_truncate_tail_degrades_without_file():
    """落盘失败（file_path=None）→ 降级为纯截断提示。"""
    import utils as u

    assert u.truncate_tail(16000, None, None, None) == \
        "<<<TRUNCATED>>>正文已截断（上限 16000 字符）"


def test_get_tool_results_dir_reads_agent_json(tmp_path):
    """agent.json 有 tool_results_cache → 用配置值（与宿主对齐）。"""
    import json

    import utils as u

    (tmp_path / "agent.json").write_text(json.dumps({
        "running": {"light_context_config": {
            "tool_result_pruning_config": {"tool_results_cache": "my_cache"}}},
    }), encoding="utf-8")
    assert u.get_tool_results_dir(str(tmp_path)) == str(tmp_path / "my_cache")


def test_get_tool_results_dir_fallback_default(tmp_path):
    """无 agent.json / 字段缺失 → 回落默认 tool_results。"""
    import utils as u

    assert u.get_tool_results_dir(str(tmp_path)) == \
        str(tmp_path / "tool_results")
    (tmp_path / "agent.json").write_text("{}", encoding="utf-8")
    assert u.get_tool_results_dir(str(tmp_path)) == \
        str(tmp_path / "tool_results")


def test_save_truncated_body_writes_file(tmp_path):
    """全文写入 workspace tool_results（宿主 save_text_output），内容一致。"""
    import utils as u

    body = "line1\nline2\n" + "x" * 200
    path = u.save_truncated_body(body, str(tmp_path))
    assert path is not None
    files = list((tmp_path / "tool_results").glob("agent-tools-*.txt"))
    assert len(files) == 1 and str(files[0]) == path
    assert files[0].read_text(encoding="utf-8") == body
    assert u.save_truncated_body("", str(tmp_path)) is None
    assert u.save_truncated_body(body, "") is None
