# -*- coding: utf-8 -*-
"""agent_tools_spawn — 以指定 agent 为模板 spawn 持久子会话（设计 §3.1）。"""

import logging
import uuid

try:
    from ..manager import (
        MAX_SPAWN_DEPTH,
        STATUS_RUNNING,
        SpawnRecord,
        TurnRecord,
        get_manager,
    )
    from ..submit import (
        _submit_turn,
        consume_and_track,
        get_workspace,
        get_workspace_manager,
    )
    from ..utils import (
        build_identity_prefix,
        get_current_context,
        make_error,
        make_success,
        resolve_parent_chat_id,
    )
    from .wait import wait_for_terminal
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import (
        MAX_SPAWN_DEPTH,
        STATUS_RUNNING,
        SpawnRecord,
        TurnRecord,
        get_manager,
    )
    from submit import (
        _submit_turn,
        consume_and_track,
        get_workspace,
        get_workspace_manager,
    )
    from utils import (
        build_identity_prefix,
        get_current_context,
        make_error,
        make_success,
        resolve_parent_chat_id,
    )
    from tools.wait import wait_for_terminal

logger = logging.getLogger(__name__)

_WAIT_HINT = (
    "\n子 agent 会话已运行。后续可选（chat_id 即上面 CHAT_ID 值，"
    "完整 UUID；后续调用也支持其第一段 8 位 hex 简写）：\n"
    "  agent_tools_wait(chat_id) 等完成取正文（推荐）\n"
    "  agent_tools_notice(chat_id) 挂完成通知\n"
    "  agent_tools_communicate(chat_id, action='recv') 读进度\n"
    "  agent_tools_communicate(chat_id, action='stop') 停止"
)


async def agent_tools_spawn(
    task: str,
    template: str = "",
    background: bool | str | int = True,
    timeout: int | float | str | None = None,
):
    """以指定 agent 为模板 spawn 持久子会话（默认后台，返回 chat_id）；完成后可用 communicate 续聊。

    Args:
        task: 子会话首轮任务描述。
        template: 目标 agent 的 id（agent_tools_list_agent 返回的 id，非显示名）；空=自己。
        background: True=后台立即返回（默认）；False=前台等完成（直接返回最终正文）或超时（超时只放弃等待，不停止会话）。
        timeout: background=False 时的等待上限（秒），默认 600。
    """
    task = str(task or "").strip()
    if not task:
        return make_error("spawn 子会话", "task 不能为空")

    template = str(template or "").strip()
    agent_id, user_id, session_id = get_current_context()
    channel = _current_channel_safe()
    template_id = template or agent_id

    # ── template 校验（load_config 纯内部读） ──
    profile = _get_profile(template_id)
    if profile is None:
        return make_error(
            f"ERROR: template '{template_id}' 不存在或未启用。"
            "用 agent_tools_list_agent 查看可用 agent"
            "（注意传 id 字段，如 'demo-agent'，非显示名）",
        )

    # ── 深度限制（发起者不在簿记 → 基准 0，子 depth=1） ──
    manager = get_manager()
    parent_book = manager.get_record_by_session(session_id)
    depth = (parent_book.depth + 1) if parent_book else 1
    if depth > MAX_SPAWN_DEPTH:
        return make_error(
            "spawn 子会话",
            f"超过 spawn 链深上限（当前链深 {depth - 1}，上限 "
            f"{MAX_SPAWN_DEPTH}）",
        )

    # ── 模板 workspace 现取（未启动 → 报错引导） ──
    workspace = get_workspace(template_id)
    if workspace is None:
        return make_error(
            "spawn 子会话",
            f"agent '{template_id}' 的 workspace 未运行"
            "（在 WebUI 切到该 agent 启动后重试）",
        )

    # ── request_context：官方构造照抄（审批路由 + 三维 + 继承） ──
    try:
        from qwenpaw.agents.tools.agent_management import (
            _build_spawn_request_context,
        )

        request_context = _build_spawn_request_context(agent_id)
    except Exception:  # noqa: BLE001
        logger.warning("_build_spawn_request_context 失败，降级最小构造",
                       exc_info=True)
        request_context = {
            "root_session_id": session_id,
            "root_agent_id": agent_id,
            "parent_session_id": session_id,
            "user_id": user_id,
            "channel": channel,
            "_spawn_subagent": True,
        }

    # ── 子会话 session_id（宿主 spawn 形态 sub-{8位hex}） ──
    sub_session_id = f"sub-{uuid.uuid4().hex[:8]}"

    # ── 身份前缀（Name = 发起者 agent.json 的 name） ──
    parent_name = _agent_name(agent_id)
    parent_chat_id = await resolve_parent_chat_id(
        workspace, agent_id, session_id,
    )
    text = (
        build_identity_prefix(parent_name, agent_id, parent_chat_id)
        + "\n\n" + task
    )

    try:
        result = await _submit_turn(
            workspace, sub_session_id, user_id, channel, text,
            request_context=request_context,
        )
    except Exception as exc:  # noqa: BLE001
        return make_error("spawn 子会话", f"提交失败：{exc!r}")
    if not result.is_new_run:
        # 新生成 session_id 理论不可能忙（碰撞防御）
        return make_error("spawn 子会话", "目标会话意外忙碌（碰撞？），重试即可")

    # ── 簿记 + 活跃表 ──
    turn = TurnRecord()
    record = SpawnRecord(
        agent_id=template_id, session_id=sub_session_id, user_id=user_id,
        channel=channel, chat_id=result.chat_id, template=template_id,
        depth=depth,
        parent=(agent_id, session_id) if session_id else None,
        spawn_request_context=request_context, turns=[turn],
    )
    manager.register_spawn(record)
    manager.register_active(
        template_id, sub_session_id, result.chat_id,
        parent=(agent_id, session_id) if session_id else None,
        turn=turn,
    )
    manager.sweep_expired()

    # ── 消费者跟踪终态（设计 §4.1；登记可取消——复审 C-H1） ──
    import asyncio

    task = asyncio.create_task(consume_and_track(workspace, record, turn, result))
    manager.register_task(task)

    # ── 身份持久化（v0.1.8：chat.meta["agent_tools"]，重启/热装后恢复） ──
    try:
        from ..persistence import persist_spawn_identity
    except ImportError:  # 兼容 pytest 直接以项目根导入
        from persistence import persist_spawn_identity
    try:
        await persist_spawn_identity(workspace, record, parent_chat_id)
    except Exception:  # noqa: BLE001
        logger.warning("spawn 身份持久化失败（不影响本会话）", exc_info=True)

    if _to_bool(background, default=True):
        return make_success(
            f"[CHAT_ID: {result.chat_id}]\n"
            f"[SESSION: {sub_session_id}]\n"
            f"{_WAIT_HINT}"
        )

    # ── 前台等待（wait 语义：超时绝不停止目标会话） ──
    wait_timeout = _to_float(timeout, default=600.0)
    body = await wait_for_terminal(
        workspace, record, turn, wait_timeout, chat_id=result.chat_id,
    )
    if body is None:
        # 3.9⑤ 共用句式（决策 16：chat_id 完整值只出现一次——返回不带
        # [CHAT_ID] 头，让会话标识在超时文案中出现）
        return make_success(
            f"等待超时：会话 {result.chat_id} 仍运行中"
            f"（已等 {int(wait_timeout)} 秒，未停止、不受影响）。\n"
            "可 wait(chat_id, timeout=1800) 续等、notice(chat_id) 挂通知、"
            "recv 读进度、stop 停止。"
        )
    return make_success(body)


# ── 内部 helper ──


def _current_channel_safe() -> str:
    try:
        from ..utils import current_channel
        return current_channel()
    except ImportError:  # 兼容直接导入
        from utils import current_channel
        return current_channel()


def _get_profile(template_id: str):
    """load_config 读 agent profile（enabled 过滤）。"""
    try:
        from qwenpaw.config.utils import load_config

        profiles = load_config().agents.profiles
        profile = profiles.get(template_id)
        if profile is not None and profile.enabled:
            return profile
    except Exception:  # noqa: BLE001
        logger.warning("load_config 读取失败", exc_info=True)
    return None


def _agent_name(agent_id: str) -> str:
    """发起者 agent.json 的 name（失败回落 agent_id）。"""
    try:
        from qwenpaw.config.utils import load_agent_config

        return str(load_agent_config(agent_id).name or agent_id)
    except Exception:  # noqa: BLE001
        return agent_id


def _to_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y"):
        return True
    if text in ("false", "0", "no", "n"):
        return False
    return default


def _to_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
