# -*- coding: utf-8 -*-
"""list_agent 测试：标头 + 行格式 + disabled/★ 标注。"""

import sys
from types import SimpleNamespace

import tools.list_agent as la


def test_list_agent_header_and_rows(monkeypatch):
    profiles = {
        "default": SimpleNamespace(enabled=True,
                                   workspace_dir=r"C:\ws\default"),
        "demo-agent": SimpleNamespace(enabled=True, workspace_dir=r"C:\ws\lh"),
        "qa": SimpleNamespace(enabled=False, workspace_dir=r"C:\ws\qa"),
    }
    fake_utils = SimpleNamespace(load_config=lambda: SimpleNamespace(
        agents=SimpleNamespace(profiles=profiles),
    ))
    monkeypatch.setitem(sys.modules, "qwenpaw.config.utils", fake_utils)
    monkeypatch.setattr(la, "get_current_context",
                        lambda: ("default", "u", "s"))

    # agent.json 读不到 → 空字段降级（不崩）
    chunk = la.agent_tools_list_agent()
    text = chunk.content[0].text

    # 标头（作者拍板：列意义说明）
    assert text.startswith("[id ｜ name ｜ enabled ｜ description]")
    assert "spawn 的 template 传 id 字段" in text
    # 行格式
    assert "default ★" in text            # 当前 agent 标注
    assert "demo-agent" in text and "（无名）" in text   # agent.json 缺失降级
    assert "qa" in text and "disabled，不可作 template" in text
    # 行序与标头分离
    lines = text.splitlines()
    assert len(lines) == 4 and lines[0].startswith("[id")


def test_list_agent_empty(monkeypatch):
    fake_utils = SimpleNamespace(load_config=lambda: SimpleNamespace(
        agents=SimpleNamespace(profiles={}),
    ))
    monkeypatch.setitem(sys.modules, "qwenpaw.config.utils", fake_utils)
    chunk = la.agent_tools_list_agent()
    assert "当前没有已配置的 agent" in chunk.content[0].text
