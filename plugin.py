# -*- coding: utf-8 -*-
"""Agent Tools 插件入口（SDK 形态，qwenpaw 2.3.0+）。

以 ToolSpec 注册 8 个 agent/子会话管理工具到 QwenPaw：
spawn / communicate / wait / notice / list_agent / list_session /
context_read / context_export。

- schema 来源：_TOOL_SCHEMAS 固化常量（schemas.py，**唯一正式 schema
  来源**，LLM 可见）——docstring / Args 注解只是代码注释，不参与 schema
  生成、不要求同步（作者拍板：不做双向锚定）；改 schema 只改 schemas.py，
  改完跑 show_tool_schema.py 刷新快照。
- handler 适配：SDK 以 (context, arguments dict) 调用——_wrap 解包回原签名
  kwargs，8 个工具函数本体零改动；ToolContext 注入 request-local
  contextvar（get_current_context 优先读，内部 API 回落）。
- 卸载清理：context.resources.defer（Registration 逆序执行），替代旧
  register_shutdown_hook（SDK HookPhase 无生命周期相位，设计 §7 语义不变：
  取消 flusher / 消费者 / notice 监视协程，防热重载孤儿）。
"""

import asyncio
import inspect
import logging

from qwenpaw.plugins import PluginContext
from qwenpaw.plugins.tools import ToolSpec

from .manager import get_manager
from .notifier import get_notifier
from .schemas import _TOOL_SCHEMAS
from .utils import _CALL_CTX
from .tools.spawn import agent_tools_spawn
from .tools.communicate import agent_tools_communicate
from .tools.wait import agent_tools_wait
from .tools.notice import agent_tools_notice
from .tools.list_agent import agent_tools_list_agent
from .tools.list_session import agent_tools_list_session
from .tools.context_read import agent_tools_context_read
from .tools.context_export import agent_tools_context_export

logger = logging.getLogger(__name__)


def _wrap(handler):
    """SDK handler 适配：arguments dict → 原签名 kwargs 解包。

    参数宽容归一（_to_bool/_to_float/str strip）由各 handler 内部完成，
    与旧路径行为一致；额外键由 schema additionalProperties=False 在
    jsonschema 校验层拦截。sync/async handler 均兼容（list_agent/
    list_session 为同步 def）。
    ToolContext 注入 _CALL_CTX（utils.get_current_context 优先读，
    agent_id/session_id 走 SDK 公共面），调用完 reset——工具函数本体
    零改动。
    """

    async def invoke(context, arguments):
        token = _CALL_CTX.set(context)
        try:
            result = handler(**dict(arguments))
            if inspect.isawaitable(result):
                result = await result
            return result
        finally:
            _CALL_CTX.reset(token)

    return invoke


# (name, handler, icon, tool_type, target_param)
_TOOLS = [
    ("agent_tools_spawn", agent_tools_spawn, "🚀", "internal", ""),
    ("agent_tools_communicate", agent_tools_communicate, "💬", "internal",
     "chat_id"),
    ("agent_tools_wait", agent_tools_wait, "⏳", "internal", ""),
    ("agent_tools_notice", agent_tools_notice, "🔔", "internal", ""),
    ("agent_tools_list_agent", agent_tools_list_agent, "🧭", "internal", ""),
    ("agent_tools_list_session", agent_tools_list_session, "📋", "internal",
     ""),
    ("agent_tools_context_read", agent_tools_context_read, "📖", "internal",
     ""),
    ("agent_tools_context_export", agent_tools_context_export, "📤",
     "internal", ""),
]


class AgentToolsPlugin:
    """Agent Tools 插件主类（SDK Plugin Protocol）。"""

    def register(self, context: PluginContext) -> None:
        logger.info("注册 Agent Tools 插件（8 个工具，SDK 形态）...")

        for name, handler, icon, tool_type, target_param in _TOOLS:
            spec = _TOOL_SCHEMAS[name]
            context.register_tool(ToolSpec(
                name=name,
                description=spec["description"],
                input_schema=spec["input_schema"],
                handler=_wrap(handler),
                enabled=True,
                tool_type=tool_type,
                target_param=target_param,
                icon=icon,
            ))

        # ── 激活回调：资源创建必须在 on_activate（SDK 生命周期契约，
        #    register 仅声明——register 里碰 resources 会 ResourceError） ──
        context.on_activate(self._activate)

        logger.info("✓ Agent Tools 插件注册完成（8 个工具）")

    def _activate(self, context: PluginContext) -> None:
        """激活：单例预热 + 资源注入 + 卸载清理登记（原 startup/shutdown
        hook 等价物）。"""
        manager = get_manager()
        get_notifier()
        manager.resources = context.resources  # flusher 托管（B3）
        context.resources.defer(
            self._shutdown_tasks, "agent_tools_shutdown",
        )

    async def _shutdown_tasks(self) -> None:
        """应用退出/插件卸载：取消全部登记任务（flusher/消费者/监视协程
        ——设计 §7 承诺完整兑现，复审 C-H1）。"""
        cancelled = 0

        manager = get_manager()
        for queue in list(manager.session_queues.values()):
            task = queue.flusher_task
            if task is not None and not task.done():
                task.cancel()
                cancelled += 1
            queue.cancel_flag = True

        # 消费者 / notice 监视协程（登记表统一取消）
        cancelled += manager.cancel_all_tasks()

        notifier = get_notifier()
        for acc in list(notifier._accumulators.values()):
            task = acc.flusher_task
            if task is not None and not task.done():
                task.cancel()
                cancelled += 1

        if cancelled:
            logger.info("Agent Tools 卸载：已取消 %d 个后台任务", cancelled)
        manager.reset_manager()


plugin = AgentToolsPlugin()
