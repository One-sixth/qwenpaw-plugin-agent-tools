# -*- coding: utf-8 -*-
"""context 测试：idx 解析 / 函数形式渲染 / 块时间线 / 驱逐行。"""

from context import (
    EvictedRow,
    _format_arg_value,
    describe_range,
    render_evicted_row,
    render_tool_call,
    render_turn,
    resolve_read_indices,
)
from helpers import StubContent, StubMsg
from session_reader import Block, Turn


def ev(n):
    return [EvictedRow(seq=i, idx=i - n, kind="model_turn",
                       role="assistant") for i in range(n)]


def test_resolve_default_recent():
    assert resolve_read_indices(21, ev(10), None, 5) == (16, 5, "-10~20")


def test_resolve_start_zero():
    assert resolve_read_indices(21, ev(10), 0, 5)[:2] == (0, 5)


def test_resolve_negative_start():
    assert resolve_read_indices(21, ev(10), -5, 5)[:2] == (16, 5)


def test_resolve_cross_evicted():
    assert resolve_read_indices(21, ev(10), -25, 5)[:2] == (-4, 5)


def test_resolve_overflow_degrades():
    assert resolve_read_indices(21, ev(10), 25, 5)[:2] == (20, 1)


def test_resolve_no_evicted_clamps():
    assert resolve_read_indices(21, None, -5, 5)[:2] == (16, 5)


def test_resolve_empty():
    # 空会话 avail 显示「无可用轮」（复审 A-L5：旧稿「0~-1」误导）
    assert resolve_read_indices(0, None, None, 5) == (0, 0, "无可用轮")


def test_resolve_visible_empty_reads_evicted_tail():
    assert resolve_read_indices(0, ev(10), None, 5) == (-5, 5, "-10~-1")


def test_resolve_count_cap():
    assert resolve_read_indices(100, None, None, 999)[1] == 50


def test_describe_range():
    assert describe_range(16, 5, "-10~20", True) == (
        "本次读取 idx 16~20（共 5 轮）｜可用范围 -10~20"
        "（负=驱逐历史，需 include_compressed=True；正=当前可见区）"
    )
    assert "未启用驱逐历史" in describe_range(0, 3, "0~20", False)


def test_render_tool_call():
    assert render_tool_call(
        "read_file", '{"file_path": "D:\\\\x.py", "line_count": 20}',
    ) == 'read_file(file_path="D:\\\\x.py", line_count=20)'
    assert render_tool_call(
        "exec",
        '{"cmd": "ls", "bg": true, "n": 3, "x": null, "lst": [1,2]}',
    ) == 'exec(cmd="ls", bg=true, n=3, x=null, lst=[1,2])'
    assert render_tool_call("f", {"a": "hi"}) == 'f(a="hi")'
    assert render_tool_call("f", "not json{{") == 'f(args="not json{{")'
    assert render_tool_call("f", "{}") == "f()"
    assert render_tool_call("f", '{"t": "a\\nb"}') == 'f(t="a\\nb")'


def test_format_arg_value():
    assert _format_arg_value("hi") == '"hi"'
    assert _format_arg_value(42) == "42"
    assert _format_arg_value(True) == "true"
    assert _format_arg_value(None) == "null"
    assert _format_arg_value({"k": 1}) == '{"k":1}'


def _sample_turn():
    return Turn(blocks=[
        Block(kind="user", role="user",
              timestamp="2026-11-16T11:12:45.102080", text="问题"),
        Block(kind="think", role="assistant",
              timestamp="2026-11-16T11:12:47", text="思考"),
        Block(kind="monologue", role="assistant",
              timestamp="2026-11-16T11:12:49", text="自言自语"),
        Block(kind="tool_call", role="assistant",
              timestamp="2026-11-16T11:12:50",
              tool_name="read_file", tool_payload='{"f": "x"}'),
        Block(kind="tool_result", role="assistant",
              timestamp="2026-11-16T11:12:52",
              tool_name="read_file", tool_payload="结果内容"),
        Block(kind="body", role="assistant",
              timestamp="2026-11-16T11:13:01", text="最终回答"),
    ])


def test_render_turn_flags_off():
    secs = render_turn(_sample_turn(), False, False, False)
    assert len(secs) == 2
    assert secs[0].startswith("————————\n[user] 2026-11-16 11:12:45")
    assert "问题" in secs[0]
    assert "[assistant]" in secs[1] and "最终回答" in secs[1]


def test_render_turn_flags_on():
    secs = render_turn(_sample_turn(), True, True, True)
    assert len(secs) == 6
    assert 'read_file(f="x")' in secs[3]
    assert "结果内容" in secs[4]


def test_render_evicted_rows():
    row = EvictedRow(seq=5, idx=-3, kind="model_turn", role="assistant",
                     content="正文", created_at="2026-11-15T10:00:00",
                     blocks=[{"type": "text", "text": "块正文"}])
    secs = render_evicted_row(row, False, False)
    assert len(secs) == 1
    assert "assistant（seq=5，驱逐 idx=-3）" in secs[0]
    assert "块正文" in secs[0]

    row2 = EvictedRow(seq=6, idx=-2, kind="tool_result", role="assistant",
                      content="输出", tool_name="exec",
                      created_at="2026-11-15T10:01:00")
    secs2 = render_evicted_row(row2, False, True)
    assert "tool_result" in secs2[0] and "exec：输出" in secs2[0]

    row3 = EvictedRow(seq=7, idx=-1, kind="context_msg", role="user",
                      content="用户提问", created_at="2026-11-15T09:00:00")
    secs3 = render_evicted_row(row3, False, False)
    assert secs3[0].startswith("————————\n[user（seq=7")
    assert "用户提问" in secs3[0]
