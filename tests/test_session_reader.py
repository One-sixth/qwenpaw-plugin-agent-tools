# -*- coding: utf-8 -*-
"""session_reader 测试：轮分类 / 正文判定 / 多段拼接。"""

from helpers import StubContent, StubMsg

from session_reader import classify_messages


def _full_msgs():
    return [
        StubMsg("user", "message", [StubContent(text="问题A")],
                "2026-11-16T11:00:00"),
        StubMsg("assistant", "reasoning", [StubContent(text="思考1")],
                "2026-11-16T11:00:02"),
        StubMsg("assistant", "plugin_call",
                [StubContent(data={"name": "read_file",
                                   "arguments": '{"file_path": "x.py"}'})],
                "2026-11-16T11:00:03"),
        StubMsg("assistant", "plugin_call_output",
                [StubContent(data={"name": "read_file",
                                   "output": "文件内容..."})],
                "2026-11-16T11:00:05"),
        StubMsg("assistant", "message", [StubContent(text="中途自言自语")],
                "2026-11-16T11:00:06"),
        StubMsg("assistant", "message", [StubContent(text="最终回答A")],
                "2026-11-16T11:00:08"),
        StubMsg("user", "message", [StubContent(text="问题B")],
                "2026-11-16T11:01:00"),
        StubMsg("assistant", "message", [StubContent(text="回答B")],
                "2026-11-16T11:01:05"),
        StubMsg("assistant", "reasoning", [StubContent(text="正在思考...")],
                "2026-11-16T11:02:00"),
    ]


def test_classify_two_turns():
    turns = classify_messages(_full_msgs())
    assert len(turns) == 2
    t1 = turns[0]
    kinds = [b.kind for b in t1.blocks]
    assert kinds == ["user", "think", "tool_call", "tool_result",
                     "monologue", "body"], kinds
    assert t1.body_text == "最终回答A"
    assert t1.blocks[0].text == "问题A"
    assert t1.blocks[0].timestamp == "2026-11-16T11:00:00"
    assert t1.blocks[2].tool_name == "read_file"
    assert "x.py" in t1.blocks[2].tool_payload
    assert t1.blocks[3].tool_payload == "文件内容..."


def test_turn2_and_tail_merge():
    turns = classify_messages(_full_msgs())
    t2 = turns[1]
    kinds = [b.kind for b in t2.blocks]
    assert kinds == ["user", "body", "think"], kinds
    assert t2.body_text == "回答B"


def test_multi_text_all_body():
    msgs = [
        StubMsg("user", "message", [StubContent(text="问")]),
        StubMsg("assistant", "message",
                [StubContent(text="段1"), StubContent(text="段2")]),
    ]
    t = classify_messages(msgs)[0]
    assert t.body_text == "段1\n段2"


def test_system_skipped_empty_body():
    msgs = [
        StubMsg("system", "message", [StubContent(text="系统")]),
        StubMsg("user", "message", [StubContent(text="问")]),
        StubMsg("assistant", "message", []),
    ]
    t = classify_messages(msgs)
    assert len(t) == 1 and t[0].body_text == ""


def test_empty_input():
    assert classify_messages([]) == []
    assert classify_messages(None) == []
