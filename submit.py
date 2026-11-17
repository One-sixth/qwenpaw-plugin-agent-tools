# -*- coding: utf-8 -*-
"""宿主适配器：_submit_turn 契约 + workspace 现取 + stop 序列 + 消费者。

内部函数路线（决策 11）：不经 HTTP loopback，直接调用 workspace /
channel / chat_manager / task_tracker 内部对象。内部序列照抄
POST /console/chat/task 端点（console.py:1090 起）。
已知代价：耦合 workspace 内部方法签名（stream_one / attach_or_start /
stream_from_queue），QwenPaw 升级可能 break——设计已接受。
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ── workspace 现取（宿主规矩：never cached object pointers） ──


def get_workspace_manager():
    """获取 MultiAgentManager 单例（插件 register 时已就位，设计 §2.1）。"""
    from qwenpaw.plugins.registry import PluginRegistry

    return PluginRegistry().get_workspace_manager()


def get_workspace(agent_id: str):
    """按 agent_id 现取 workspace（不启动、不缓存）。"""
    manager = get_workspace_manager()
    if manager is None:
        return None
    return manager.get_loaded_agent(agent_id)


def get_tool_coordinator(workspace):
    """获取跨 workspace 单例 tool_coordinator（stop 序列第 1 步用）。"""
    services = getattr(workspace, "_app_services", None)
    return getattr(services, "tool_coordinator", None)


# ── _submit_turn 契约（设计 §2.1 定稿） ──────────────────


@dataclass
class SubmitResult:
    """_submit_turn 返回。

    queue / run_state 仅 is_new_run=True 时有效；消费者策略由调用方决定：
    - spawn / communicate send：create_task 跑 consume_and_track（要终态正文）
    - notice 唤醒：直接 detach_subscriber 丢弃 queue（fire-and-forget）
    - is_new_run=False：**调用方无须处理 detach（本函数已 detach）**——
      防泄漏点在提交函数内闭环（复审 H1 落地）
    """

    chat_id: str
    is_new_run: bool
    queue: Optional[object] = None      # asyncio.Queue（订阅队列）
    run_state: Optional[dict] = None    # producer 标志共享槽


def derive_registration_fields(request_context: Optional[dict]) -> dict:
    """从 request_context 派生 subagent 注册字段（复刻端点
    _chat_registration_fields，console.py:380-397）。"""
    if not isinstance(request_context, dict):
        return {}
    if request_context.get("_spawn_subagent") is not True:
        return {}
    return {
        "source": "subagent",
        "parent_session_id": str(
            request_context.get("parent_session_id") or "",
        ) or None,
        "root_session_id": str(
            request_context.get("root_session_id") or "",
        ) or None,
    }


def _chat_name(text: str) -> str:
    """chat placeholder 名（首行截断，行为对齐端点 _extract_placeholder_name）。"""
    stripped = text.strip()
    first = stripped.splitlines()[0] if stripped else ""
    return first[:60] or "New Chat"


async def _submit_turn(
    workspace,
    session_id: str,
    user_id: str,
    channel: str,
    text: str,
    *,
    registration_fields: Optional[dict] = None,
    request_context: Optional[dict] = None,
) -> SubmitResult:
    """提交一轮：get_channel → native_payload → get_or_create_chat →
    attach_or_start（设计 §2.1 五步序列）。

    channel 参数保留契约占位（当前内部路线固定走 console channel——
    与端点 get_channel("console") 一致）。
    """
    console_channel = await workspace.channel_manager.get_channel("console")
    if console_channel is None:
        raise RuntimeError("Channel Console not found")

    meta = {"session_id": session_id, "user_id": user_id}
    if isinstance(request_context, dict) and request_context:
        meta["request_context"] = request_context

    content_parts = []
    try:
        from qwenpaw.schemas import _coerce_content_item

        content_parts.append(
            _coerce_content_item({"type": "text", "text": text}),
        )
    except Exception:  # noqa: BLE001
        content_parts = [{"type": "text", "text": text}]  # 降级 raw dict

    native_payload = {
        "channel_id": "console",
        "sender_id": user_id,
        "content_parts": content_parts,
        "message_metadata": None,
        "meta": meta,
    }

    reg = (
        registration_fields
        if registration_fields is not None
        else derive_registration_fields(request_context)
    )
    chat = await workspace.chat_manager.get_or_create_chat(
        session_id, user_id, "console", name=_chat_name(text), **reg,
    )

    run_state = {"producer_cancelled": False, "producer_error": None}
    tracker = workspace.task_tracker

    async def _tracked_stream(payload: dict):
        """producer 包装（端点同款）：捕获取消/异常置标志，不吞异常。"""
        try:
            async for sse_line in console_channel.stream_one(payload):
                yield sse_line
        except asyncio.CancelledError:
            run_state["producer_cancelled"] = True
            raise
        except Exception as exc:  # noqa: BLE001
            run_state["producer_error"] = exc
            raise

    queue, is_new_run = await tracker.attach_or_start(
        chat.id,
        native_payload,
        _tracked_stream,
        owner=workspace,
        on_finished=workspace.chat_manager.mark_chat_finished,
    )
    if not is_new_run:
        # 忙：queue 已挂进订阅列表并预填缓冲 → 必须立即 detach（泄漏点）
        await tracker.detach_subscriber(chat.id, queue)
        return SubmitResult(chat.id, False)

    return SubmitResult(chat.id, True, queue, run_state)


# ── 消费者（设计 §4.1 H1 采纳） ──────────────────────────


def extract_error_signal(sse_line: str) -> str:
    """从 SSE 行提取错误信号（rate_limited / error 形态），无则空串。

    消费者只检测错误信号；正文终态时从 session 文件读（口径统一，
    stale 分支同款）。
    """
    if not sse_line.startswith("data:"):
        return ""
    payload = sse_line[5:].strip()
    if not payload or payload == "[DONE]":
        return ""
    try:
        obj = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(obj, dict):
        return ""
    if obj.get("type") == "rate_limited":
        return f"rate_limited: {str(obj.get('error') or '')[:200]}"
    if "error" in obj:
        return f"error: {str(obj.get('error'))[:200]}"
    return ""


async def consume_and_track(workspace, record, turn, result: SubmitResult) -> None:
    """消费者：收 SSE 错误信号 → 写 TurnRecord 终态 + 移除活跃表条目。

    终态判定（设计 §4.6）：
    - producer_cancelled（run 被 stop）→ cancelled
    - producer_error / SSE 错误信号 → failed（token 额度耗尽等也触发通知）
    - 其余 → completed，正文从 session 文件读
    - 消费者自身被取消（宿主 shutdown）→ 不写终态（观察丢失），stale 分支兜底
    无论何种退出方式都移除活跃表条目（try/finally 保证）。
    """
    try:
        from .manager import (
            STATUS_CANCELLED,
            STATUS_COMPLETED,
            STATUS_FAILED,
            get_manager,
        )
        from . import session_reader
        from .notifier import get_notifier
    except ImportError:  # 兼容 pytest 直接以项目根导入
        from manager import (
            STATUS_CANCELLED,
            STATUS_COMPLETED,
            STATUS_FAILED,
            get_manager,
        )
        import session_reader
        from notifier import get_notifier

    manager = get_manager()
    chat_id = result.chat_id
    err_signal = ""
    consumer_cancelled = False
    try:
        async for sse_line in workspace.task_tracker.stream_from_queue(
            result.queue, chat_id,
        ):
            signal = extract_error_signal(sse_line)
            if signal and not err_signal:
                err_signal = signal
    except asyncio.CancelledError:
        consumer_cancelled = True
        raise  # 保持取消语义（宿主清理依赖），活跃表移除由 finally 保证
    except Exception as exc:  # noqa: BLE001
        if not turn.is_terminal:
            turn.status = STATUS_FAILED
            turn.error = f"consumer error: {exc!r}"
            turn.finished_at = datetime.now()
            # 异常分支同样触发 notice（2026-11-17 作者拍板：三种终态
            # 全部触发不留缺口——此前条件为「刚判定的终态」才触发，
            # 异常分支先写终态导致通知被跳过）
            try:
                get_notifier().on_turn_finished(chat_id, turn.status, "")
            except Exception:  # noqa: BLE001
                logger.warning("notice 触发失败（chat=%s）", chat_id,
                               exc_info=True)
    finally:
        # 消费者被取消判定（复审 R-A #3）：CancelledError 路径显式标记
        # 之外，还需 task.cancelling() 兜底——宿主 stream_from_queue 在
        # cancel_run=False 时吞掉 CancelledError（task_tracker.py:401+），
        # 异常分支不可达；热重载/关机取消时 cancelling()>0 仍成立，
        # 据此跳过终态写入与 notice（防假 completed/假唤醒）
        _task = asyncio.current_task()
        cancelled_now = consumer_cancelled or bool(
            _task is not None
            and getattr(_task, "cancelling", lambda: 0)() > 0
        )
        try:
            if not turn.is_terminal and not cancelled_now:
                if run_state_cancelled(result.run_state):
                    turn.status = STATUS_CANCELLED
                    turn.error = "会话被 stop 停止"
                elif run_state_error(result.run_state) is not None:
                    turn.status = STATUS_FAILED
                    turn.error = repr(run_state_error(result.run_state))
                elif err_signal:
                    turn.status = STATUS_FAILED
                    turn.error = err_signal
                else:
                    turn.status = STATUS_COMPLETED
                    try:
                        # 现取 workspace（宿主规矩：never cached pointers，
                        # 复审 C-M3；agent 卸载时降级空正文）
                        fresh_ws = get_workspace(record.agent_id) or workspace
                        turn.result_text = (
                            await session_reader.read_latest_turn_body(
                                fresh_ws, record,
                            )
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "终态正文读取失败（chat=%s）", chat_id,
                            exc_info=True,
                        )
                turn.finished_at = datetime.now()
                logger.info(
                    "回合终态：chat=%s status=%s（正文 %d 字符）",
                    chat_id, turn.status, len(turn.result_text or ""),
                )
                # notice 主触发链路（设计 §3.4 决策 6，复审 A-H1 接线）：
                # completed/failed/cancelled 全部触发；观察丢失（消费者
                # 被取消）不触发——监视协程/重注册路径兜底
                try:
                    get_notifier().on_turn_finished(
                        chat_id, turn.status, turn.result_text or "",
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("notice 触发失败（chat=%s）", chat_id,
                                   exc_info=True)
        finally:
            # 活跃表移除带身份守卫（复审 B-③ 动态实验实锤：T1
            # body-read 让出窗口内 flusher 注册 T2 覆盖同键条目，
            # 无条件 pop 会误删 T2 的条目 → T2 运行期拦截失效；
            # 仅当条目仍绑定当前回合时才移除）
            manager.remove_active_if_turn(
                record.agent_id, record.session_id, turn,
            )


def run_state_cancelled(run_state: Optional[dict]) -> bool:
    if not run_state:
        return False
    return bool(run_state.get("producer_cancelled"))


def run_state_error(run_state: Optional[dict]):
    if not run_state:
        return None
    return run_state.get("producer_error")


# ── stop 序列（设计 §2.1，顺序严格） ─────────────────────


async def stop_session(workspace, session_id: str, chat_id: str) -> dict:
    """停止序列：先取消工具会话，后停 producer（照抄 stop 端点顺序）。

    硬约束：只允许 agent_tools_communicate(action="stop") 与 flusher
    TOCTOU 补偿调用；消费者代码内禁止出现任何对目标会话的停止调用。
    """
    cancelled_tools = 0
    coordinator = get_tool_coordinator(workspace)
    if coordinator is not None:
        try:
            from qwenpaw.tool_calls import CancelReason

            cancelled_tools = await coordinator.cancel_running_for_session(
                session_id,
                agent_id=workspace.agent_id,
                reason=CancelReason.USER,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "工具会话取消失败（session=%s）", session_id[:30],
                exc_info=True,
            )
    stopped = await workspace.task_tracker.request_stop(chat_id, reason="user")
    logger.info(
        "stop 序列完成：chat=%s 工具取消 %d 项 producer 停止=%s",
        chat_id, cancelled_tools, stopped,
    )
    return {"cancelled_tools": cancelled_tools, "stopped": stopped}
