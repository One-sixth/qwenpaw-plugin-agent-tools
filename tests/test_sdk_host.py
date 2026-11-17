# -*- coding: utf-8 -*-
"""SDK PluginTestHost 集成测试（v0.2.0 迁移验收）。

真实加载链路验证：register → on_activate → 8 工具注册 → call_tool →
错误语义 → schema 校验 → reload → 卸载清理。

⚠️ TestHost 进程独占 + 重绑 qwenpaw 全局单例（PluginRegistry 等），
close 时恢复——若本文件与其他测试出现顺序性污染，把本文件移到最后
执行或加 skip 标记。

spawn/send 全链路（workspace_manager 真实 attach_or_start）不在本文件
覆盖——TestHost 的 workspace manager 是 SimpleNamespace mock（SDK 设计
如此），真机行为由热装实测覆盖。
"""

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

from qwenpaw.plugins.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ID = "qwenpaw-plugin-agent-tools"

EXPECTED_TOOLS = {
    "agent_tools_spawn",
    "agent_tools_communicate",
    "agent_tools_wait",
    "agent_tools_notice",
    "agent_tools_list_agent",
    "agent_tools_list_session",
    "agent_tools_context_read",
    "agent_tools_context_export",
}


def _run(coro):
    """同步驱动 + 现场恢复（TestHost 双重副作用修复）。

    实测宿主 TestHost 两个副作用（sdk-migration 诊断实锤）：
    1. load 后插件目录被移出 sys.path（loader 导入清理连坐同值条目）
    2. close 后 sys.modules 中从插件目录导入的扁平模块（tools.*、
       manager/notifier/submit 等——与插件目录同目录）被清除 → 后续
       测试函数内 import 触发重新导入 → 新模块对象 → 已打补丁失效。

    快照 sys.path + sys.modules，finally 全量恢复：路径补插、host 期间
    新增模块删除、host 清掉的旧模块对象回填（对象引用仍在测试模块
    全局变量中，回填即复活，monkeypatch 撤销语义不受影响）。
    """
    mods_before = dict(sys.modules)
    try:
        return asyncio.run(coro)
    finally:
        root = str(PLUGIN_DIR)
        if root not in sys.path:
            sys.path.insert(0, root)
        # 插件命名空间模块清除（host 期间导入，防跨测试残留）
        for name in list(sys.modules):
            if name.startswith("plugin_"):
                del sys.modules[name]
        # host 清掉的旧模块回填（不删 host 新增——C 扩展禁止二次初始化）
        for name, mod in mods_before.items():
            if name not in sys.modules:
                sys.modules[name] = mod
        # sys.path 只保 ROOT 存在、不还原顺序（importlib 缓存下易碎且无必要）


async def _driver_load(checker):
    with tempfile.TemporaryDirectory(prefix="agent-tools-host-") as tmp:
        async with PluginTestHost(Path(tmp)) as host:
            await host.load(PLUGIN_DIR)
            return await checker(host)


def test_load_registers_8_tools():
    async def check(host):
        tools = await host.available_tools()
        return {t for t in tools if t.startswith("agent_tools_")}

    mine = _run(_driver_load(check))
    assert mine == EXPECTED_TOOLS


def test_list_agent_runs_in_host():
    """TestHost 环境无 agent 配置——空表文案正常返回（不炸即链路通）。"""

    async def check(host):
        r = await host.call_tool("agent_tools_list_agent", {})
        assert not r.is_error
        text = r.content[0].text
        assert "[id" in text or "当前没有" in text

    _run(_driver_load(check))


def test_list_session_empty_ledger():
    async def check(host):
        r = await host.call_tool("agent_tools_list_session", {})
        assert not r.is_error
        assert "没有" in r.content[0].text

    _run(_driver_load(check))


def test_spawn_empty_task_is_error():
    async def check(host):
        r = await host.call_tool("agent_tools_spawn", {"task": ""})
        assert r.is_error
        assert "task 不能为空" in r.content[0].text

    _run(_driver_load(check))


def test_extra_argument_rejected():
    """additionalProperties=False：额外键在 jsonschema 校验层拦截。"""
    from qwenpaw.plugins.errors import ToolInputError

    async def check(host):
        with pytest.raises(ToolInputError):
            await host.call_tool("agent_tools_spawn", {"task": "x", "bogus": 1})

    _run(_driver_load(check))


def test_sync_handlers_ok():
    """同步 handler（list_agent/list_session）经 SDK invoke 正常返回。"""

    async def check(host):
        r = await host.call_tool("agent_tools_list_session", {})
        assert not r.is_error

    _run(_driver_load(check))


def test_reload_and_unload_cleanup():
    """reload 语义 + 卸载时 deferred 清理执行（quiescent）。"""

    async def check(host):
        snap = host.resource_snapshot(PLUGIN_ID)
        assert snap["state"] == "active"
        assert snap["active_calls"] == 0
        assert "agent_tools_shutdown" in snap["resources"]

        rr = await host.reload(PLUGIN_ID)
        assert rr.ok

        # load() 是外部源——卸载用 unload（install 副本才可用 uninstall）
        await host.unload(PLUGIN_ID)
        host.assert_quiescent(PLUGIN_ID)

    _run(_driver_load(check))
