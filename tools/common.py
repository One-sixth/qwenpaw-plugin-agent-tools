# -*- coding: utf-8 -*-
"""工具层共享 helper：chat_id 入口校验 / 终态描述 / 防误用拦截。"""

import logging

try:
    from ..manager import STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED
    from ..utils import make_error, truncate_text
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED
    from utils import make_error, truncate_text

logger = logging.getLogger(__name__)

WAIT_BODY_LIMIT = 8000

# 防误用拦截报错（设计 §3.9⑦ 作者定稿原文）
MISUSE_ERROR = (
    "当前是子agent对话回合，内容只需在正文正常输出即可，"
    "无需（也不能）通过交流工具回复父会话。"
)


def check_chat_id(chat_id):
    """chat_id 入口校验（簿记短 id 前缀匹配，v0.1.6）→ (chat_id, error, healed_record)。"""
    try:
        from ..utils import resolve_chat_id_input
        return resolve_chat_id_input(chat_id)
    except ImportError:  # 兼容直接导入
        from utils import resolve_chat_id_input
        return resolve_chat_id_input(chat_id)


def describe_terminal(turn, target, tracker) -> str:
    """终态回合 → 结果文本（completed 带正文截断；正文缺失时从会话文件兜底）。"""
    status = turn.status
    if status == STATUS_COMPLETED:
        body = turn.result_text or ""
        return (
            f"状态：completed\n—— 结果正文 ——\n{truncate_text(body, WAIT_BODY_LIMIT)}"
            if body
            else "状态：completed\n（正文为空；可用 communicate(chat_id, "
                 "action='recv') 读会话文件）"
        )
    if status == STATUS_FAILED:
        return f"状态：failed\n原因：{turn.error or '（无摘要）'}"
    if status == STATUS_CANCELLED:
        return "状态：cancelled（会话被 stop 停止）"
    return f"状态：{status}"


async def check_misuse(target_session_id: str,
                       target_agent_id: "str | None" = None) -> "str | None":
    """防误用拦截（祖先链收窄版，设计 §4.3）。

    当前会话在被调起回合中（命中活跃表）且目标在其祖先链上 → 返回
    拦截文案（3.9⑦）；否则 None 放行。拦截命中时做活性复核自愈：
    tracker 已 idle 且簿记非 running → 移除条目并放行。

    Args:
        target_session_id: 目标会话 session_id。
        target_agent_id: 目标 agent_id（调用方从 locate_chat 结果显式
            传入——2026-11-17 作者拍板修复：v0.1.9 前依赖簿记反查，
            主会话/官方会话不在簿记 → 反查空串 → 祖先链恒不命中 →
            子 agent 反向 send 主会话漏拦）。传 None 时回落簿记反查
            （兼容）。

    例外（作者拍板）：notice 触发的回合不注册活跃表 → is_active 不
    命中 → 天然放行——通知回合已不是问答会话，允许自由 send 交流。
    """
    from dataclasses import dataclass

    try:
        from ..manager import get_manager
        from ..utils import get_current_context
        from ..submit import get_workspace
    except ImportError:  # 兼容直接导入
        from manager import get_manager
        from utils import get_current_context
        from submit import get_workspace

    agent_id, _user, session_id = get_current_context()
    if not session_id:
        return None
    manager = get_manager()
    if not manager.is_active(agent_id, session_id):
        return None  # 当前会话不在被调起回合中

    if target_agent_id is None:
        target_agent_id = _lookup_agent(manager, target_session_id)
    # 祖先链判定（沿 parent 引用向上）——收窄边界（决策 14）：只拦
    # 「目标在祖先链」；self-send 不在自身祖先链 → 放行走正常排队
    on_chain = manager.is_on_ancestor_chain(
        agent_id, session_id,
        target_agent_id, target_session_id,
    )
    if not on_chain:
        return None

    # 活性复核自愈：当前会话的运行回合实际已结束 → 移除条目放行
    entry = manager.active_sessions.get((agent_id, session_id))
    if entry is not None:
        workspace = get_workspace(agent_id)
        if workspace is not None:
            try:
                status = await workspace.task_tracker.get_status(entry.chat_id)
                book = manager.get_record(entry.chat_id)
                if (status == "idle"
                        and book is not None
                        and (book.latest_turn is None
                             or book.latest_turn.is_terminal)):
                    manager.remove_active(agent_id, session_id)
                    return None
            except Exception:  # noqa: BLE001
                logger.warning("活性复核失败", exc_info=True)
    return MISUSE_ERROR


def _lookup_agent(manager, session_id: str) -> str:
    """session_id → agent_id（簿记反查；找不到返回空串——祖先链判定
    按 (agent_id, session_id) 元组匹配，空串不命中）。"""
    record = manager.get_record_by_session(session_id)
    return record.agent_id if record else ""
