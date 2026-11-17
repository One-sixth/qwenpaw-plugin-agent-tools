# -*- coding: utf-8 -*-
"""会话文件读取：宿主归一化适配 + 轮/块分类 + 正文/monologue 判定。

复用宿主归一化（决策 11）：``session.get_session_state_dict`` →
``AgentState.model_validate`` → ``agentscope_msg_to_message``（chats/api.py
同款路径），一次性解决 tool_use/tool_call 双形态、timestamp 归一、hint 块
排除、data 媒体块映射、scroll 占位过滤。插件只在归一化结果上做轮/块分类。
"""

import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)

# 块 kind（设计 §3.6 块时间线）：
#   user / body（正文，恒可见）/ monologue（自言自语）/ think / tool_call /
#   tool_result / media


@dataclass
class Block:
    """分类后的块（context 时间线最小单元，列表顺序即时间序）。"""

    kind: str
    role: str
    timestamp: str = ""       # 归一化 timestamp 原样（ISO 字符串）
    text: str = ""            # user / body / monologue / think 内容
    tool_name: str = ""       # tool_call / tool_result
    tool_payload: str = ""    # tool_call: arguments 原样；tool_result: output 原样


@dataclass
class Turn:
    """一轮：user 消息开启，到下一条 user 消息前。"""

    blocks: List[Block] = field(default_factory=list)

    @property
    def body_text(self) -> str:
        """最新一轮最终正文（body 块按序拼接）。"""
        return "\n".join(b.text for b in self.blocks if b.kind == "body")


def _role_str(role) -> str:
    """Role 枚举/字符串 → 小写字符串（str, Enum 可直接比较，防御归一）。"""
    value = getattr(role, "value", role)
    return str(value or "").lower()


def _type_str(mtype) -> str:
    """MessageType 枚举 → 字符串。"""
    value = getattr(mtype, "value", mtype)
    return str(value or "").lower()


def _msg_text(message) -> str:
    """提取一条归一化 Message 的全部 text 块内容（按序拼接）。"""
    parts = []
    for content in message.content or []:
        text = getattr(content, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts)


async def fetch_normalized_messages(workspace, session_id: str, user_id: str,
                                    channel: str) -> list:
    """宿主归一化路径读取会话消息（chats/api.py:876-930 同款）。

    返回 List[Message]（qwenpaw.schemas 归一化形态）；无会话/空会话返回 []。
    """
    from agentscope.state import AgentState
    from qwenpaw.app.chats.utils import (
        agentscope_msg_to_message,
        parse_legacy_memory_state,
    )

    state = await workspace.session.get_session_state_dict(
        session_id, user_id, channel,
    )
    return _from_state_dict(
        state, session_id,
        AgentState, agentscope_msg_to_message, parse_legacy_memory_state,
    )


def _from_state_dict(state, session_id, AgentState,
                     agentscope_msg_to_message, parse_legacy_memory_state):
    """state dict → 归一化消息列表（chats/api.py:907-930 同款解析）。"""
    if not state:
        return []
    agent_raw = state.get("agent", {})
    memories = []
    state_raw = agent_raw.get("state") if isinstance(agent_raw, dict) else None
    if isinstance(state_raw, dict):
        try:
            agent_state = AgentState.model_validate(state_raw)
            memories = list(agent_state.context)
        except Exception:  # noqa: BLE001
            logger.debug(
                "AgentState 解析失败（session=%s），回退 legacy",
                session_id[:30], exc_info=True,
            )
    if not memories:
        memory_raw = agent_raw.get("memory", {}) if isinstance(agent_raw, dict) else {}
        if memory_raw:
            memories, _summary = parse_legacy_memory_state(memory_raw)
    if not memories:
        return []
    return agentscope_msg_to_message(
        memories, truncate_oversized_outputs=True,
    )


def classify_messages(messages) -> List[Turn]:
    """归一化消息 → 轮列表（每轮为时间序块列表，正文/monologue 已判定）。

    轮边界：user 消息开启，到下一条 user 消息前。
    正文判定（设计 §2.3）：轮内最后一条带 text 的 assistant 消息 → 正文
    （其全部 text 段按序拼接）；其余 assistant 消息的 text → monologue。
    """
    turns: List[Turn] = []
    current: Turn = Turn()
    opened = False

    for message in messages or []:
        role = _role_str(message.role)
        mtype = _type_str(message.type)
        timestamp = ""
        metadata = message.metadata or {}
        if isinstance(metadata, dict):
            timestamp = str(metadata.get("timestamp") or "")

        if role == "user" and mtype == "message":
            # user 消息开启新轮
            if opened:
                turns.append(current)
            current = Turn()
            opened = True
            current.blocks.append(Block(
                kind="user", role="user", timestamp=timestamp,
                text=_msg_text(message),
            ))
            continue

        if role not in ("assistant", "tool"):
            continue  # system 等非对话角色跳过

        # assistant 消息：按 Message.type 分类
        if mtype == "message":
            text = _msg_text(message)
            if text:
                # 暂记为 body 候选；轮闭合时最后一条留 body，其余改 monologue
                current.blocks.append(Block(
                    kind="body", role=role, timestamp=timestamp, text=text,
                ))
        elif mtype == "reasoning":
            for content in message.content or []:
                text = getattr(content, "text", None)
                if isinstance(text, str) and text:
                    current.blocks.append(Block(
                        kind="think", role=role, timestamp=timestamp, text=text,
                    ))
        elif mtype == "plugin_call":
            for content in message.content or []:
                data = getattr(content, "data", None)
                if isinstance(data, dict):
                    current.blocks.append(Block(
                        kind="tool_call", role=role, timestamp=timestamp,
                        tool_name=str(data.get("name") or ""),
                        tool_payload=str(data.get("arguments") or ""),
                    ))
        elif mtype == "plugin_call_output":
            for content in message.content or []:
                data = getattr(content, "data", None)
                if isinstance(data, dict):
                    current.blocks.append(Block(
                        kind="tool_result", role=role, timestamp=timestamp,
                        tool_name=str(data.get("name") or ""),
                        tool_payload=str(data.get("output") or ""),
                    ))
        elif mtype in ("image", "audio", "video", "file"):
            current.blocks.append(Block(
                kind="media", role=role, timestamp=timestamp,
                text=f"[{mtype}]",
            ))

    if opened:
        turns.append(current)

    # 正文回填：每轮内最后一条 body 候选留 body，其余降级 monologue
    for turn in turns:
        body_indexes = [
            i for i, b in enumerate(turn.blocks) if b.kind == "body"
        ]
        for i in body_indexes[:-1]:
            turn.blocks[i].kind = "monologue"
    return turns


async def read_latest_turn_body(workspace, record) -> str:
    """最新一轮最终正文（消费者终态 / wait stale 分支共用）。

    record 需带 session_id / user_id / channel（SpawnRecord 或等价对象）。
    无会话/无正文返回空字符串。
    """
    messages = await fetch_normalized_messages(
        workspace, record.session_id, record.user_id, record.channel,
    )
    turns = classify_messages(messages)
    if not turns:
        return ""
    return turns[-1].body_text


async def read_latest_turn_sections(workspace, record):
    """最新一轮的 (正文, 自言自语列表)（communicate recv 用）。

    正文 = 轮内最后一条带 text 的 assistant 消息；自言自语 = 该轮更早
    assistant 消息的 text 块（按时间序）。无会话返回 ("", [])。
    """
    messages = await fetch_normalized_messages(
        workspace, record.session_id, record.user_id, record.channel,
    )
    turns = classify_messages(messages)
    if not turns:
        return "", []
    last = turns[-1]
    body = last.body_text
    monologues = [b.text for b in last.blocks if b.kind == "monologue"]
    return body, monologues
