# -*- coding: utf-8 -*-
"""多会话 wait/notice + chat_id 列表归一（v0.2.5）。"""

import asyncio

import tools.notice as notice_mod
import tools.wait as wait_mod
from helpers import MockTracker, MockWorkspace, make_turn
from manager import NoticeRecord, get_manager
from tools.common import normalize_chat_id_list


def _chunk_text(chunk):
    return chunk.content[0].text


def _mk_book(chat_id, session_id, turn_status=None):
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


# ── normalize_chat_id_list ──

def test_normalize_single_str():
    assert normalize_chat_id_list("abc") == (["abc"], None)


def test_normalize_real_list_dedup():
    ids, err = normalize_chat_id_list(["a", "b", "a", " a "])
    assert err is None and ids == ["a", "b"]


def test_normalize_json_string_list():
    assert normalize_chat_id_list('["a", "b"]') == (["a", "b"], None)


def test_normalize_invalid_type():
    ids, err = normalize_chat_id_list(123)
    assert ids is None and err


def test_normalize_empty_list():
    ids, err = normalize_chat_id_list([])
    assert ids is None and err


def test_normalize_empty_item():
    ids, err = normalize_chat_id_list(["a", "  "])
    assert ids is None and err


def test_normalize_broken_json_falls_back_single():
    # 形如 [...] 但 JSON 非法 → 当作单个字符串
    assert normalize_chat_id_list("[not json") == (["[not json"], None)


# ── wait 多会话 ──

def _patch_wait(monkeypatch, books, workspace):
    monkeypatch.setattr(wait_mod, "get_workspace", lambda aid: workspace)

    async def _fake_loc(cid):
        b = books.get(cid)
        return _loc(cid, b) if b is not None else None

    monkeypatch.setattr(wait_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(wait_mod, "check_chat_id", lambda v: (v, None, None))


def test_wait_multi_all_completed(monkeypatch):
    from tools.wait import agent_tools_wait

    b1 = _mk_book("m-chat-a", "sub-ma", "completed")
    b1.turns[0].result_text = "RA"
    b2 = _mk_book("m-chat-b", "sub-mb", "completed")
    b2.turns[0].result_text = "RB"
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["idle"]))
    _patch_wait(monkeypatch, {"m-chat-a": b1, "m-chat-b": b2}, ws)

    text = _chunk_text(asyncio.run(agent_tools_wait(["m-chat-a", "m-chat-b"])))
    assert "### 会话 m-chat-a" in text and "### 会话 m-chat-b" in text
    assert "RA" in text and "RB" in text


def test_wait_multi_early_fail_no_wait(monkeypatch):
    from tools.wait import agent_tools_wait

    b1 = _mk_book("m-chat-ok", "sub-ok", "completed")
    b1.turns[0].result_text = "SHOULD_NOT_APPEAR"
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["idle"]))
    _patch_wait(monkeypatch, {"m-chat-ok": b1}, ws)

    text = _chunk_text(asyncio.run(agent_tools_wait(["m-chat-ok", "ghost"])))
    assert "ghost" in text and "不存在" in text
    assert "未对任何会话发起等待" in text
    assert "SHOULD_NOT_APPEAR" not in text   # 有效会话也未被等待


def test_wait_multi_json_string(monkeypatch):
    from tools.wait import agent_tools_wait

    b1 = _mk_book("m-js-a", "sub-ja", "completed")
    b1.turns[0].result_text = "JA"
    b2 = _mk_book("m-js-b", "sub-jb", "completed")
    b2.turns[0].result_text = "JB"
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["idle"]))
    _patch_wait(monkeypatch, {"m-js-a": b1, "m-js-b": b2}, ws)

    text = _chunk_text(asyncio.run(agent_tools_wait('["m-js-a", "m-js-b"]')))
    assert "JA" in text and "JB" in text


def test_wait_multi_waits_for_all_timeout(monkeypatch):
    """一完成一运行：全部走完（后者超时）才返回，前者的结果也在。"""
    from tools.wait import agent_tools_wait

    b1 = _mk_book("m-mx-a", "sub-mx-a", "completed")
    b1.turns[0].result_text = "DONE"
    b2 = _mk_book("m-mx-b", "sub-mx-b", "running")
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["running"]))
    _patch_wait(monkeypatch, {"m-mx-a": b1, "m-mx-b": b2}, ws)

    text = _chunk_text(
        asyncio.run(agent_tools_wait(["m-mx-a", "m-mx-b"], timeout=1))
    )
    assert "DONE" in text
    assert "等待超时" in text


def test_wait_single_still_plain(monkeypatch):
    from tools.wait import agent_tools_wait

    b1 = _mk_book("m-single", "sub-sg", "completed")
    b1.turns[0].result_text = "ONLY"
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["idle"]))
    _patch_wait(monkeypatch, {"m-single": b1}, ws)

    text = _chunk_text(asyncio.run(agent_tools_wait("m-single")))
    assert "ONLY" in text and "### 会话" not in text   # 单会话输出格式不变


# ── notice 多会话 ──

def _patch_notice(monkeypatch, books, workspace):
    monkeypatch.setattr(notice_mod, "get_workspace", lambda aid: workspace)

    async def _fake_loc(cid):
        b = books.get(cid)
        return _loc(cid, b) if b is not None else None

    monkeypatch.setattr(notice_mod, "locate_chat", _fake_loc)
    monkeypatch.setattr(notice_mod, "check_chat_id", lambda v: (v, None, None))
    monkeypatch.setattr(notice_mod, "current_channel", lambda: "console")


def test_notice_multi_partial(monkeypatch):
    from tools.notice import agent_tools_notice

    b1 = _mk_book("m-nt-a", "sub-na", "running")
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["running"]))
    _patch_notice(monkeypatch, {"m-nt-a": b1}, ws)

    text = _chunk_text(asyncio.run(agent_tools_notice(["m-nt-a", "ghost"])))
    assert "已注册" in text and "目标会话未找到，注册失败" in text
    assert text.count("m-nt-a") == 1   # uuid 只在 chat_id= 行出现一次，消息内不重复
    assert get_manager().get_notice("m-nt-a") is not None
    assert get_manager().get_notice("ghost") is None


def test_notice_multi_cancel(monkeypatch):
    from tools.notice import agent_tools_notice

    mgr = get_manager()
    for cid, sid in (("m-cx-1", "sub-cx1"), ("m-cx-2", "sub-cx2")):
        mgr.register_notice(NoticeRecord(
            target_chat_id=cid, target_agent_id="a", target_session_id=sid,
            wake_agent_id="pa", wake_user_id="u", wake_session_id="ps",
            wake_channel="console",
        ))
    monkeypatch.setattr(notice_mod, "check_chat_id", lambda v: (v, None, None))

    async def _fake_loc(cid):
        return object()

    monkeypatch.setattr(notice_mod, "locate_chat", _fake_loc)
    text = _chunk_text(
        asyncio.run(agent_tools_notice(["m-cx-1", "m-cx-2"], cancel=True))
    )
    assert "已取消" in text
    assert "移除待发" not in text
    assert mgr.get_notice("m-cx-1").cancelled
    assert mgr.get_notice("m-cx-2").cancelled


def test_normalize_non_str_element():
    ids, err = normalize_chat_id_list([1, 2])
    assert ids is None and err
    ids, err = normalize_chat_id_list('["a", 1]')
    assert ids is None and err


def test_notice_multi_all_invalid_is_error(monkeypatch):
    from tools.notice import agent_tools_notice

    monkeypatch.setattr(notice_mod, "check_chat_id", lambda v: (v, None, None))

    async def _none(cid):
        return None

    monkeypatch.setattr(notice_mod, "locate_chat", _none)
    chunk = asyncio.run(agent_tools_notice(["ghost-a", "ghost-b"]))
    text = _chunk_text(chunk)
    assert "ghost-a" in text and "ghost-b" in text
    assert getattr(chunk, "is_error", False) is True


def test_wait_multi_block_exception_isolated(monkeypatch):
    """某块抛异常：不整单抛错，转为该块错误文本，其他块结果保留。"""
    from tools.wait import agent_tools_wait

    b_ok = _mk_book("m-exc-ok", "sub-exc-ok", "completed")
    b_ok.turns[0].result_text = "GOOD-BODY"
    b_bad = _mk_book("m-exc-bad", "sub-exc-bad", "completed")
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["idle"]))
    _patch_wait(monkeypatch, {"m-exc-ok": b_ok, "m-exc-bad": b_bad}, ws)

    orig = wait_mod._wait_one

    async def fake(cid, timeout=900):
        if cid == "m-exc-bad":
            raise RuntimeError("boom")
        return await orig(cid, timeout)

    monkeypatch.setattr(wait_mod, "_wait_one", fake)
    chunk = asyncio.run(agent_tools_wait(["m-exc-ok", "m-exc-bad"]))
    text = _chunk_text(chunk)
    assert "GOOD-BODY" in text          # 兄弟块结果保留
    assert "处理异常" in text            # 异常块转为文本
    assert getattr(chunk, "is_error", False) is False   # 部分成功 → success


def test_wait_multi_array_of_one_no_header(monkeypatch):
    """单元素数组去重后 len==1 → 走单会话路径，无 ### 块头。"""
    from tools.wait import agent_tools_wait

    b = _mk_book("m-one", "sub-one", "completed")
    b.turns[0].result_text = "ONE"
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["idle"]))
    _patch_wait(monkeypatch, {"m-one": b}, ws)
    text = _chunk_text(asyncio.run(agent_tools_wait(["m-one"])))
    assert "ONE" in text and "### 会话" not in text


def test_wait_multi_duplicate_collapses(monkeypatch):
    """重复 id 去重 → 单会话路径，正文只出现一次。"""
    from tools.wait import agent_tools_wait

    b = _mk_book("m-dup", "sub-dup", "completed")
    b.turns[0].result_text = "DUP-ONLY"
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["idle"]))
    _patch_wait(monkeypatch, {"m-dup": b}, ws)
    text = _chunk_text(asyncio.run(agent_tools_wait(["m-dup", "m-dup"])))
    assert text.count("DUP-ONLY") == 1 and "### 会话" not in text


def test_wait_multi_two_long_bodies_two_files(monkeypatch, tmp_path):
    """两会话各超 9000 → 各自独立截断落盘（两文件、内容各自完整）。"""
    from tools.wait import agent_tools_wait
    import tools.common as common_mod

    body_a = "甲" * 9500
    body_b = "乙" * 9500
    b1 = _mk_book("m-long-a", "sub-la", "completed")
    b1.turns[0].result_text = body_a
    b2 = _mk_book("m-long-b", "sub-lb", "completed")
    b2.turns[0].result_text = body_b
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["idle"]))
    _patch_wait(monkeypatch, {"m-long-a": b1, "m-long-b": b2}, ws)
    monkeypatch.setattr(common_mod, "get_agent_workspace_dir",
                        lambda aid: str(tmp_path))

    text = _chunk_text(asyncio.run(agent_tools_wait(["m-long-a", "m-long-b"])))
    assert text.count("<<<TRUNCATED>>>") >= 4   # 每块 1 切点 + 1 尾部
    files = list((tmp_path / "tool_results").glob("agent-tools-*.txt"))
    assert len(files) == 2
    contents = {f.read_text(encoding="utf-8") for f in files}
    assert body_a in contents and body_b in contents


def test_notice_multi_idempotent(monkeypatch):
    """同一 id 连续两次 notice → 第二次仍「完成通知已注册」。"""
    from tools.notice import agent_tools_notice

    b = _mk_book("m-idem2", "sub-idem2", "running")
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["running"]))
    _patch_notice(monkeypatch, {"m-idem2": b}, ws)
    asyncio.run(agent_tools_notice(["m-idem2"]))
    chunk = asyncio.run(agent_tools_notice(["m-idem2"]))
    assert "完成通知已注册" in _chunk_text(chunk)


def test_notice_cancel_no_queue_count(monkeypatch):
    """取消文案不含『移除待发 N 条』（取消不关心队列）。"""
    from tools.notice import agent_tools_notice
    from manager import NoticeRecord

    mgr = get_manager()
    mgr.register_notice(NoticeRecord(
        target_chat_id="m-cx-n", target_agent_id="a", target_session_id="sub-cxn",
        wake_agent_id="pa", wake_user_id="u", wake_session_id="ps",
        wake_channel="console",
    ))
    monkeypatch.setattr(notice_mod, "check_chat_id", lambda v: (v, None, None))

    async def _fake_loc(cid):
        return object()

    monkeypatch.setattr(notice_mod, "locate_chat", _fake_loc)
    text = _chunk_text(asyncio.run(agent_tools_notice("m-cx-n", cancel=True)))
    assert "已取消" in text
    assert "移除待发" not in text


# ── notice 极简文案（v0.2.5 定稿：5 句 + 多会话 chat_id 行） ──

def test_notice_single_register_exact(monkeypatch):
    from tools.notice import agent_tools_notice

    b = _mk_book("m-ex-reg", "sub-ex-reg", "running")
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["running"]))
    _patch_notice(monkeypatch, {"m-ex-reg": b}, ws)
    assert _chunk_text(asyncio.run(agent_tools_notice("m-ex-reg"))) == "完成通知已注册"


def test_notice_single_cancel_exact(monkeypatch):
    from tools.notice import agent_tools_notice
    from manager import NoticeRecord

    mgr = get_manager()
    mgr.register_notice(NoticeRecord(
        target_chat_id="m-ex-cx", target_agent_id="a",
        target_session_id="sub-ex-cx", wake_agent_id="pa", wake_user_id="u",
        wake_session_id="ps", wake_channel="console",
    ))
    monkeypatch.setattr(notice_mod, "check_chat_id", lambda v: (v, None, None))

    async def _fake_loc(cid):
        return object()

    monkeypatch.setattr(notice_mod, "locate_chat", _fake_loc)
    assert _chunk_text(
        asyncio.run(agent_tools_notice("m-ex-cx", cancel=True))
    ) == "完成通知已取消"


def test_notice_single_notfound_exact(monkeypatch):
    from tools.notice import agent_tools_notice

    monkeypatch.setattr(notice_mod, "check_chat_id", lambda v: (v, None, None))

    async def _none(cid):
        return None

    monkeypatch.setattr(notice_mod, "locate_chat", _none)
    assert _chunk_text(
        asyncio.run(agent_tools_notice("m-ex-nf"))
    ) == "目标会话未找到，注册失败"


def test_notice_single_terminal_exact(monkeypatch):
    from tools.notice import agent_tools_notice

    b = _mk_book("m-ex-tm", "sub-ex-tm", "completed")
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["idle"]))
    _patch_notice(monkeypatch, {"m-ex-tm": b}, ws)
    assert _chunk_text(
        asyncio.run(agent_tools_notice("m-ex-tm"))
    ) == "目标会话未注册完成通知"


def test_notice_multi_header_full_id(monkeypatch):
    """多会话块头用解析后的完整 id，文案另起一行。"""
    from tools.notice import agent_tools_notice
    from utils import make_success

    full1 = "11111111-1111-4111-8111-111111111111"
    full2 = "22222222-2222-4222-8222-222222222222"
    mapping = {"s1": full1, "s2": full2}
    monkeypatch.setattr(notice_mod, "check_chat_id",
                        lambda v: (mapping.get(v, v), None, None))

    async def fake_one(cid, cancel=False):
        return make_success("完成通知已注册")

    monkeypatch.setattr(notice_mod, "_notice_one", fake_one)
    text = _chunk_text(asyncio.run(agent_tools_notice(["s1", "s2"])))
    assert f"chat_id={full1}\n完成通知已注册" in text
    assert f"chat_id={full2}\n完成通知已注册" in text


def test_notice_single_cancel_notfound(monkeypatch):
    """取消不存在的会话 → 目标会话未找到，取消失败。"""
    from tools.notice import agent_tools_notice

    monkeypatch.setattr(notice_mod, "check_chat_id", lambda v: (v, None, None))

    async def _none(cid):
        return None

    monkeypatch.setattr(notice_mod, "locate_chat", _none)
    assert _chunk_text(
        asyncio.run(agent_tools_notice("m-cx-nf", cancel=True))
    ) == "目标会话未找到，取消失败"


def test_notice_multi_cancel_notfound(monkeypatch):
    """多会话取消，全部不存在 → 保持块格式 + is_error。"""
    from tools.notice import agent_tools_notice

    monkeypatch.setattr(notice_mod, "check_chat_id", lambda v: (v, None, None))

    async def _none(cid):
        return None

    monkeypatch.setattr(notice_mod, "locate_chat", _none)
    chunk = asyncio.run(agent_tools_notice(["g1", "g2"], cancel=True))
    text = _chunk_text(chunk)
    assert "chat_id=g1" in text and "chat_id=g2" in text
    assert "目标会话未找到，取消失败" in text
    assert getattr(chunk, "is_error", False) is True


# ── 复审补齐（v0.2.5 MED-4/MED-5）──

def test_wait_multi_invalid_id_form(monkeypatch):
    """形态非法 id → 预校验收集错误，整单不等待。"""
    from tools.wait import agent_tools_wait

    b = _mk_book("m-okid", "sub-okid", "completed")
    b.turns[0].result_text = "OKID"
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["idle"]))
    monkeypatch.setattr(wait_mod, "get_workspace", lambda aid: ws)

    async def _fl(cid):
        return _loc(cid, b) if cid == "m-okid" else None

    monkeypatch.setattr(wait_mod, "locate_chat", _fl)

    def _check(v):
        if v == "bad-id":
            return "", "chat_id 无效：「bad-id」不符合 UUID 形态", None
        return v, None, None

    monkeypatch.setattr(wait_mod, "check_chat_id", _check)
    chunk = asyncio.run(agent_tools_wait(["m-okid", "bad-id"]))
    text = _chunk_text(chunk)
    assert "未对任何会话发起等待" in text
    assert "OKID" not in text
    assert getattr(chunk, "is_error", False) is True


def test_wait_multi_workspace_none(monkeypatch):
    """workspace 未运行 → 预校验失败、整单不等待。"""
    from tools.wait import agent_tools_wait

    books = {
        "m-ws1": _mk_book("m-ws1", "sub-ws1", "completed"),
        "m-ws2": _mk_book("m-ws2", "sub-ws2", "completed"),
    }
    monkeypatch.setattr(wait_mod, "get_workspace", lambda aid: None)

    async def _fl(cid):
        b = books.get(cid)
        return _loc(cid, b) if b is not None else None

    monkeypatch.setattr(wait_mod, "locate_chat", _fl)
    monkeypatch.setattr(wait_mod, "check_chat_id", lambda v: (v, None, None))
    text = _chunk_text(asyncio.run(agent_tools_wait(["m-ws1", "m-ws2"])))
    assert "workspace 未运行" in text
    assert "未对任何会话发起等待" in text


def test_notice_single_invalid_id_form(monkeypatch):
    from tools.notice import agent_tools_notice

    monkeypatch.setattr(
        notice_mod, "check_chat_id",
        lambda v: ("", "chat_id 无效：「x」不符合 UUID 形态", None),
    )
    assert _chunk_text(
        asyncio.run(agent_tools_notice("x"))
    ) == "目标会话未找到，注册失败"
    assert _chunk_text(
        asyncio.run(agent_tools_notice("x", cancel=True))
    ) == "目标会话未找到，取消失败"


def test_notice_single_workspace_none(monkeypatch):
    from tools.notice import agent_tools_notice

    b = _mk_book("m-nws", "sub-nws", "running")

    async def _fl(cid):
        return _loc(cid, b) if cid == "m-nws" else None

    monkeypatch.setattr(notice_mod, "check_chat_id", lambda v: (v, None, None))
    monkeypatch.setattr(notice_mod, "locate_chat", _fl)
    monkeypatch.setattr(notice_mod, "get_workspace", lambda aid: None)
    assert _chunk_text(
        asyncio.run(agent_tools_notice("m-nws"))
    ) == "目标会话未找到，注册失败"


def test_notice_single_idle_no_turn(monkeypatch):
    from tools.notice import agent_tools_notice

    b = _mk_book("m-idle", "sub-idle")   # 无 turns
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["idle"]))
    _patch_notice(monkeypatch, {"m-idle": b}, ws)
    assert _chunk_text(
        asyncio.run(agent_tools_notice("m-idle"))
    ) == "目标会话未注册完成通知"


def test_notice_multi_all_success_is_error_false(monkeypatch):
    from tools.notice import agent_tools_notice

    b1 = _mk_book("m-ok1n", "sub-ok1n", "running")
    b2 = _mk_book("m-ok2n", "sub-ok2n", "running")
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["running"]))
    _patch_notice(monkeypatch, {"m-ok1n": b1, "m-ok2n": b2}, ws)
    chunk = asyncio.run(agent_tools_notice(["m-ok1n", "m-ok2n"]))
    text = _chunk_text(chunk)
    assert text.count("完成通知已注册") == 2
    assert getattr(chunk, "is_error", False) is False


def test_notice_multi_block_exception_isolated(monkeypatch):
    """单块抛异常 → 转该块错误文本，其他块结果保留（不整单抛错）。"""
    from tools.notice import agent_tools_notice

    b1 = _mk_book("m-exc1", "sub-exc1", "running")
    b2 = _mk_book("m-exc2", "sub-exc2", "running")
    ws = MockWorkspace(agent_id="child", task_tracker=MockTracker(["running"]))
    _patch_notice(monkeypatch, {"m-exc1": b1, "m-exc2": b2}, ws)

    orig = notice_mod._notice_one

    async def fake(cid, cancel=False):
        if cid == "m-exc2":
            raise RuntimeError("boom")
        return await orig(cid, cancel)

    monkeypatch.setattr(notice_mod, "_notice_one", fake)
    chunk = asyncio.run(agent_tools_notice(["m-exc1", "m-exc2"]))
    text = _chunk_text(chunk)
    assert "完成通知已注册" in text
    assert "目标会话未找到，注册失败" in text
    assert getattr(chunk, "is_error", False) is False
