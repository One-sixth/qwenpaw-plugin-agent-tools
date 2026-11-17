# -*- coding: utf-8 -*-
"""Agent Tools 插件入口。

注册 8 个 agent/子会话管理工具到 QwenPaw Agent 工具箱：
spawn / communicate / wait / notice / list_agent / list_session /
context_read / context_export。

启动钩子空操作占位（簿记纯内存，无持久清理需求）；卸载钩子取消本插件
全部后台任务（flusher / 消费者 / notice 监视协程），防热重载孤儿
（设计 §7）。
"""

import asyncio
import logging

from qwenpaw.plugins.api import PluginApi

from .manager import get_manager
from .notifier import get_notifier
from .tools.spawn import agent_tools_spawn
from .tools.communicate import agent_tools_communicate
from .tools.wait import agent_tools_wait
from .tools.notice import agent_tools_notice
from .tools.list_agent import agent_tools_list_agent
from .tools.list_session import agent_tools_list_session
from .tools.context_read import agent_tools_context_read
from .tools.context_export import agent_tools_context_export

logger = logging.getLogger(__name__)


class AgentToolsPlugin:
    """Agent Tools 插件主类。"""

    def register(self, api: PluginApi) -> None:
        logger.info("注册 Agent Tools 插件（8 个工具）...")

        api.register_tool(
            tool_name="agent_tools_spawn",
            tool_func=agent_tools_spawn,
            description=(
                "以指定 agent 为模板 spawn 持久子会话（默认后台，返回 chat_id），"
                "可用 wait 等完成取正文。"
            ),
            icon="🚀",
            tool_type="internal",
            target_param="",
            enabled=True,
        )
        api.register_tool(
            tool_name="agent_tools_communicate",
            tool_func=agent_tools_communicate,
            description=(
                "与子会话交互：send 发起新一轮（忙则排队）/ "
                "recv 读最新一轮正文 / stop 停止会话。"
            ),
            icon="💬",
            tool_type="internal",
            target_param="chat_id",
            enabled=True,
        )
        api.register_tool(
            tool_name="agent_tools_wait",
            tool_func=agent_tools_wait,
            description=(
                "前台等待子会话回合结束并返回最终正文（超时不停止目标会话）。"
            ),
            icon="⏳",
            tool_type="internal",
            target_param="",
            enabled=True,
        )
        api.register_tool(
            tool_name="agent_tools_notice",
            tool_func=agent_tools_notice,
            description=(
                "注册子会话完成通知（completed/failed/cancelled 均触发，"
                "仅会话空闲时投递）；cancel=True 取消。"
            ),
            icon="🔔",
            tool_type="internal",
            target_param="",
            enabled=True,
        )
        api.register_tool(
            tool_name="agent_tools_list_agent",
            tool_func=agent_tools_list_agent,
            description=(
                "查看当前所有可用 agent（id/名字/启用状态/描述）。"
                "spawn 的 template 传这里的 id 字段。"
            ),
            icon="🧭",
            tool_type="internal",
            target_param="",
            enabled=True,
        )
        api.register_tool(
            tool_name="agent_tools_list_session",
            tool_func=agent_tools_list_session,
            description=(
                "列出本插件 spawn 的全部会话（状态/chat_id/最新回合/通知注册 🔔），"
                "用于找回 chat_id。"
            ),
            icon="📋",
            tool_type="internal",
            target_param="",
            enabled=True,
        )
        api.register_tool(
            tool_name="agent_tools_context_read",
            tool_func=agent_tools_context_read,
            description=(
                "读取会话上下文（默认最近数轮），支持正负索引翻页与 "
                "think/tool/monologue/驱逐历史开关。"
            ),
            icon="📖",
            tool_type="internal",
            target_param="",
            enabled=True,
        )
        api.register_tool(
            tool_name="agent_tools_context_export",
            tool_func=agent_tools_context_export,
            description=(
                "把会话上下文全量导出为 Markdown 块时间线文件"
                "（output_path 必填）。"
            ),
            icon="📤",
            tool_type="internal",
            target_param="",
            enabled=True,
        )

        # ── 启动钩子：占位（簿记纯内存态，无持久清理需求） ──
        api.register_startup_hook(
            "agent_tools_startup",
            self._startup_mark,
            priority=90,
        )
        # ── 卸载钩子：取消本插件全部后台任务（防热重载孤儿） ──
        api.register_shutdown_hook(
            "agent_tools_shutdown",
            self._shutdown_tasks,
            priority=110,
        )

        logger.info("✓ Agent Tools 插件注册完成（8 个工具）")

    def _startup_mark(self) -> None:
        """启动标记：获取单例确保注册时点状态可用。"""
        get_manager()
        get_notifier()

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
