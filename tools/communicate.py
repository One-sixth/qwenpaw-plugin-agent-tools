# -*- coding: utf-8 -*-
"""agent_tools_communicate — 与子会话交互：send / recv / stop（设计 §3.2）。

纯后台工具（决策 5）：send 立即返回（忙则排队）、recv 立即读、stop 立即停。
拿最终正文用 wait 或 notice。
"""

import asyncio
import logging

try:
    from ..manager import (
        QUEUE_LIMIT,
        STATUS_RUNNING,
        PendingSend,
        TurnRecord,
        SpawnRecord,
        get_manager,
    )
    from ..session_reader import read_latest_turn_sections
    from ..submit import (
        _submit_turn,
        consume_and_track,
        get_workspace,
        stop_session,
    )
    from ..utils import (
        build_identity_prefix,
        get_current_context,
        locate_chat,
        make_error,
        make_success,
        resolve_parent_chat_id,
    )
    from .common import check_chat_id, check_misuse
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import (
        QUEUE_LIMIT,
        STATUS_RUNNING,
        PendingSend,
        TurnRecord,
        SpawnRecord,
        get_manager,
    )
    from session_reader import read_latest_turn_sections
    from submit import (
        _submit_turn,
        consume_and_track,
        get_workspace,
        stop_session,
    )
    from utils import (
        build_identity_prefix,
        get_current_context,
        locate_chat,
        make_error,
        make_success,
        resolve_parent_chat_id,
    )
    from tools.common import check_chat_id, check_misuse

logger = logging.getLogger(__name__)

ACTIONS = ("send", "recv", "stop")

# flusher 轮询节奏（设计 §4.2：睡 3~5s）
FLUSHER_POLL_SECONDS = 3.0

# 连续提交失败放弃阈值（2026-11-17 作者拍板）：仅「空闲 → 投递 → 失败
# → 放回队首」路径计数；会话忙（running）只是没轮到，不算失败。
FLUSHER_MAX_CONSECUTIVE_FAILURES = 10

RECV_EMPTY = (
    "无正文可读：最新一轮尚无正文（可能仍在思考/调用工具）。"
    "可稍后 recv 再读、wait 等完成、context_read 读历史轮。"
)


async def agent_tools_communicate(
    chat_id: str,
    action: str = "send",
    text: str = "",
):
    """与子会话交互：send 发起新一轮（忙则排队）/ recv 读最新一轮正文 / stop 停止。

    仅读最新一轮；读历史轮用 agent_tools_context_read。
    Args:
        chat_id: 子会话 id（完整 UUID，也支持其第一段 8 位 hex 短 id）。
        action: send=发起新一轮（text 必填，忙则排队）；recv=读最新一轮正文（最终输出+自言自语）；stop=停止并清空排队。
        text: send 的消息文本；recv/stop 无需传。
    """
    action = str(action or "").strip().lower()
    if action not in ACTIONS:
        return make_error(
            "会话交互",
            f"action「{action}」无效，仅支持 {' / '.join(ACTIONS)}",
        )

    cid, err, _healed = check_chat_id(chat_id)
    if err:
        return make_error("会话交互", err)

    target = await locate_chat(cid)
    if target is None:
        return make_error(
            "会话交互",
            f"找不到会话 {cid}",
            "用 agent_tools_list_session 查看已知会话",
        )
    workspace = get_workspace(target.agent_id)
    if workspace is None:
        return make_error(
            "会话交互", f"agent '{target.agent_id}' 的 workspace 未运行",
        )

    if action == "send":
        return await _do_send(workspace, target, text)
    if action == "recv":
        return await _do_recv(workspace, target)
    return await _do_stop(workspace, target)


# ── send ──


async def _do_send(workspace, target, text: str):
    text = str(text or "")
    if not text.strip():
        return make_error(
            "ERROR: send 需要 text 参数（要发送的消息文本）；"
            "recv/stop 不需要 text",
        )

    # 防误用拦截（祖先链收窄版，设计 §4.3；target_agent_id 显式传入——
    # v0.1.10 修复：主会话不在簿记，反查空串曾致反向 send 漏拦）
    misuse = await check_misuse(target.session_id,
                                target_agent_id=target.agent_id)
    if misuse:
        return make_error(misuse)

    manager = get_manager()
    agent_id, _user, session_id = get_current_context()
    parent_name = _agent_name(agent_id)
    parent_chat_id = await resolve_parent_chat_id(
        workspace, agent_id, session_id,
    )
    final_text = (
        build_identity_prefix(parent_name, agent_id, parent_chat_id)
        + "\n\n" + text
    )

    tracker = workspace.task_tracker
    status = await tracker.get_status(target.chat_id)
    existing_queue = manager.get_queue(target.chat_id)
    if existing_queue is not None and existing_queue.cancel_flag:
        # stop 残留的死结构（flusher 已取消，永远不会再清理）——丢弃
        # 重建，否则本次排队会被 stale flag 静默吞掉（复审 R-A #2 /
        # R-B #1 端到端实锤）
        manager.drop_queue(target.chat_id)
        existing_queue = None
    if status == "running" or (
        existing_queue is not None
        and (existing_queue.items or existing_queue.in_flight)
    ):
        # 忙、队列非空、或 flusher 已取队首未定局 → 排队。队列非空
        # 也排队（2026-11-17 作者拍板）：busy→idle 过渡窗口内 flusher
        # 尚未取走队首时，直提消息会超车排队消息（T1 实测 FIFO 竞态
        # 3→2）——统一入队保序；in_flight 覆盖「已取未提」段（复审
        # B-①：popleft→resolver/attach 让出窗口内直提超车 → 倒挂）
        queue = manager.get_or_create_queue(target.chat_id)
        if len(queue.items) >= QUEUE_LIMIT:
            return make_error(
                "发送消息",
                f"排队已满（{QUEUE_LIMIT} 条）。等待当前回合结束或 stop 清空",
            )
        queue.items.append(PendingSend(
            text=final_text,
            parent=(agent_id, session_id) if session_id else None,
            request_context=(
                target.book.spawn_request_context
                if target.book is not None else None
            ),
        ))
        _ensure_flusher(workspace, target)
        pos = len(queue.items)
        return make_success(
            f"已排队：会话 {target.chat_id} 忙碌或有排队消息，本消息排"
            f"第 {pos} 位，按顺序自动提交。stop 可停止并清空排队。"
        )

    # 空闲 → 直接提交（建消费者跟踪终态）
    return await _submit_send(workspace, target, final_text)


async def _submit_send(workspace, target, final_text: str):
    """提交一轮 send：建/追加簿记 + 活跃表 + 消费者。"""
    manager = get_manager()
    agent_id, user_id, session_id = get_current_context()
    try:
        result = await _submit_turn(
            workspace, target.session_id, target.user_id, target.channel,
            final_text,
            request_context=(
                target.book.spawn_request_context
                if target.book is not None else None
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return make_error("发送消息", f"提交失败：{exc!r}")
    if not result.is_new_run:
        # 被抢占（提交瞬间目标忙）→ detach 已在 _submit_turn 内闭环。
        # 复审 A-M2：此前返回「未送达，稍后重试」依赖 LLM 重试，与
        # flusher 路径（放回队首必达）不对称——改为入队复用 flusher
        # 投递机制，消息必达
        queue = manager.get_or_create_queue(target.chat_id)
        if len(queue.items) >= QUEUE_LIMIT:
            return make_error(
                "发送消息",
                f"排队已满（{QUEUE_LIMIT} 条）。等待当前回合结束或 stop 清空",
            )
        queue.items.append(PendingSend(
            text=final_text,
            parent=(agent_id, session_id) if session_id else None,
            request_context=(
                target.book.spawn_request_context
                if target.book is not None else None
            ),
        ))
        _ensure_flusher(workspace, target)
        return make_success(
            f"已排队：会话 {target.chat_id} 恰好转忙，本消息已入队，"
            f"将按顺序自动提交（当前第 {len(queue.items)} 位）。"
        )

    # 簿记：无 SpawnRecord 新建（send 无簿记场景，设计 §4.1）；有则追加回合
    turn = TurnRecord()
    book = manager.get_record(target.chat_id)
    if book is None:
        book = SpawnRecord(
            agent_id=target.agent_id, session_id=target.session_id,
            user_id=target.user_id, channel=target.channel,
            chat_id=target.chat_id, template=target.agent_id, depth=0,
            parent=None, spawn_request_context=None, turns=[turn],
        )
        manager.register_spawn(book)
    else:
        book.turns.append(turn)
    manager.register_active(
        target.agent_id, target.session_id, target.chat_id,
        parent=(agent_id, session_id) if session_id else None,
        turn=turn,
    )

    import asyncio

    task = asyncio.create_task(consume_and_track(workspace, book, turn, result))
    manager.register_task(task)
    return make_success(
        f"已提交：会话 {target.chat_id} 开始新一轮（已加身份前缀）。"
        "等完成用 agent_tools_wait(chat_id)，读进度用 action='recv'。"
    )


# ── 排队 flusher（设计 §4.2，TOCTOU 双闸） ──


def _ensure_flusher(workspace, target) -> None:
    """懒启动 flusher（每会话一个，done() 检查复用）。"""
    manager = get_manager()
    queue = manager.get_queue(target.chat_id)
    if queue is None:
        return
    task = queue.flusher_task
    if task is not None and not task.done():
        return
    queue.flusher_task = asyncio.create_task(
        _flusher(workspace, target),
    )


async def _flusher_give_up(target, queue, last_exc) -> None:
    """连续提交失败达阈值：ERROR 日志留痕 + 气泡提示开发者检查。

    气泡目标：queue.items 中**最旧**有 parent 的条目（pending 已放回
    队首，即最早排队失败的消息发起者；复审 R-B #3 修正 docstring 措辞
    ——旧稿误写「最近一次」）。多发起者时其余发起者无气泡，仅 ERROR
    日志兜底。调用前置条件：**本函数被调前队列结构已被 drop_queue 移
    除**（R-B #2 顺序），items 从对象引用读取不受影响。

    气泡用 sticky=True——注意宿主 console_push_store 后端有全局 60s
    保留期，**sticky 不延长后端保留**（复审 R-B #4），仅透传给前端由
    其决定展示；UI 未及时轮询则气泡丢失，ERROR 日志是最终兜底。
    """
    dropped = len(queue.items)
    parent_session = next(
        (p.parent[1] for p in queue.items if p.parent), None,
    )
    logger.error(
        "排队投递连续 %d 次失败，放弃重试（chat=%s，丢弃 %d 条）：%r",
        FLUSHER_MAX_CONSECUTIVE_FAILURES, target.chat_id[:8], dropped,
        last_exc,
    )
    text = (
        f"⚠️ agent-tools 排队消息投递失败：会话 {target.chat_id[:8]} 连续 "
        f"{FLUSHER_MAX_CONSECUTIVE_FAILURES} 次提交失败，已放弃重试并清理"
        f"{dropped} 条排队消息。这是异常状态，需要开发者检查宿主日志。"
    )
    if parent_session:
        try:
            from qwenpaw.app import console_push_store

            await console_push_store.append(
                parent_session, text, sticky=True,
            )
        except Exception:  # noqa: BLE001
            logger.warning("放弃提示气泡投递失败", exc_info=True)


async def _flusher(workspace, target) -> None:
    """排队投递循环：睡 3~5s → 查 tracker → idle → 复查 cancel → 校验
    目标存在 → 提交队首（TOCTOU 双闸 + 被抢占放回重试 + 提交异常回滚
    ——复审 C-H2：popleft 后提交失败不得静默丢消息）。

    失败计数（2026-11-17 作者拍板）：仅「空闲 → 投递 → 失败 → 放回队首」
    计数，连续达 FLUSHER_MAX_CONSECUTIVE_FAILURES 放弃并发气泡提示开发
    者；会话忙（running）与被抢占（is_new_run=False）不计——只是没轮到。
    """
    manager = get_manager()
    consecutive_failures = 0
    while True:
        queue = manager.get_queue(target.chat_id)
        if queue is None:
            return
        await asyncio.sleep(FLUSHER_POLL_SECONDS)
        # 现取 workspace（宿主规矩：never cached pointers，复审 C-M3；
        # agent 下线 → 队列无意义，丢弃退出）
        fresh_ws = get_workspace(target.agent_id)
        if fresh_ws is None:
            logger.warning("flusher 退出：agent %s 已下线（丢弃队列）",
                           target.agent_id)
            manager.drop_queue(target.chat_id)
            return
        workspace = fresh_ws
        tracker = workspace.task_tracker
        try:
            status = await tracker.get_status(target.chat_id)
        except Exception:  # noqa: BLE001
            logger.warning("flusher get_status 异常，本轮跳过", exc_info=True)
            continue
        if status == "running":
            continue

        # 闸①：取队首前复查 cancel（stop 恰在 await 段内到达 → 放弃；
        # 补日志留痕——复审 R-A #2：此前静默丢弃不可查）
        queue = manager.get_queue(target.chat_id)
        if queue is None:
            logger.info(
                "flusher 闸①退出：队列已移除，退出"
                "（chat=%s）", target.chat_id[:8],
            )
            return
        if queue.cancel_flag:
            # 此分支在 popleft 之前——丢弃数即当前 items 数（复审 A-L2：
            # 旧稿 +1 多计；此处也无「已取 pending」可言）
            logger.info(
                "flusher 闸①退出：stop 残留 flag，丢弃 %d 条"
                "（chat=%s）", len(queue.items), target.chat_id[:8],
            )
            manager.drop_queue(target.chat_id)
            return
        if not queue.items:
            manager.drop_queue(target.chat_id)
            return
        pending = queue.items.popleft()
        queue.in_flight = True  # 已取未定局：窗口内新 send 一律排队（B-①）

        # 提交前 resolver 校验目标 chat 仍存在（防重建僵尸会话）
        current_agent, _, _ = get_current_context()
        try:
            from qwenpaw.config.utils import load_config
            from qwenpaw.app.chats.resolver import find_chat_matches

            matches = await find_chat_matches(
                load_config(), target.chat_id, current_agent,
            )
        except ImportError:
            matches = [object()]  # 非 qwenpaw 环境（测试）跳过校验
        except Exception:  # noqa: BLE001
            matches = []
        if not matches:
            logger.warning(
                "排队目标会话已删除，丢弃队列（chat=%s，%d 条）",
                target.chat_id[:8], len(queue.items) + 1,
            )
            manager.drop_queue(target.chat_id)
            return

        try:
            result = await _submit_turn(
                workspace, target.session_id, target.user_id, target.channel,
                pending.text, request_context=pending.request_context,
            )
        except Exception as exc:  # noqa: BLE001
            # 提交异常：放回队首 + 日志，下轮重试（复审 C-H2：不得静默
            # 丢消息）；连续异常由闸①/cancel/agent 下线退出路径兜底
            consecutive_failures += 1
            logger.warning(
                "排队提交异常，放回队首重试（chat=%s，连续第 %d 次）：%r",
                target.chat_id[:8], consecutive_failures, exc,
            )
            queue = manager.get_queue(target.chat_id)
            if queue is None or queue.cancel_flag:
                return
            queue.in_flight = False  # 放回 → 定局，窗口结束（B-①）
            queue.items.appendleft(pending)
            if (
                consecutive_failures
                >= FLUSHER_MAX_CONSECUTIVE_FAILURES
            ):
                # 先移除结构再发气泡（复审 R-B #2：drop 后于气泡 await
                # 的话，气泡发送窗口内新 send 会排进即将被 drop 的队列
                # 被误伤）；queue 对象仍被 give_up 参数引用可读
                manager.drop_queue(target.chat_id)
                await _flusher_give_up(target, queue, exc)
                return
            continue
        if not result.is_new_run:
            # 被抢占（提交瞬间目标转忙）→ 放回队首重试；不算失败，
            # 不计数（外部正当占用会话，继续等）
            queue = manager.get_queue(target.chat_id)
            if queue is not None:
                queue.in_flight = False  # 放回 → 定局（B-①）
                queue.items.appendleft(pending)
            continue

        consecutive_failures = 0  # 投递成功，计数清零
        queue = manager.get_queue(target.chat_id)
        if queue is not None:
            queue.in_flight = False  # 提交成功 → 定局（B-①）

        # 簿记 + 活跃表 + 消费者（排队请求提交后变新回合）
        turn = TurnRecord()
        book = manager.get_record(target.chat_id)
        if book is None:
            book = SpawnRecord(
                agent_id=target.agent_id, session_id=target.session_id,
                user_id=target.user_id, channel=target.channel,
                chat_id=target.chat_id, template=target.agent_id, depth=0,
                parent=pending.parent, spawn_request_context=None,
                turns=[turn],
            )
            manager.register_spawn(book)
        else:
            book.turns.append(turn)
        manager.register_active(
            target.agent_id, target.session_id, target.chat_id,
            parent=pending.parent, turn=turn,
        )
        task = asyncio.create_task(consume_and_track(workspace, book, turn, result))
        manager.register_task(task)

        # 闸②：提交成功后再次复查——stop 恰在「复查通过 → attach 完成」
        # 窗口内到达 → 对刚启动的 run 补偿执行 stop 序列（复审 ⚠️M5）
        queue = manager.get_queue(target.chat_id)
        if queue is not None and queue.cancel_flag:
            logger.info(
                "TOCTOU 补偿：stop 在提交窗口内到达（chat=%s）",
                target.chat_id[:8],
            )
            await stop_session(workspace, target.session_id, target.chat_id)
        # 队列还有剩余 → 继续循环（下轮轮询）；空则下一轮清理退出


# ── recv ──


async def _do_recv(workspace, target):
    """读最新一轮全部正文（最终输出 + 自言自语；不含思考与工具调用）。"""
    try:
        body, monologues = await read_latest_turn_sections(workspace, target)
    except Exception as exc:  # noqa: BLE001
        return make_error("读取会话", f"会话文件读取失败：{exc!r}")
    sections = []
    if monologues:
        sections.append("—— 自言自语 ——\n" + "\n".join(monologues))
    if body:
        sections.append("—— 最终输出 ——\n" + body)
    if not sections:
        return make_success(RECV_EMPTY)
    return make_success(f"会话 {target.chat_id} 最新一轮：\n" + "\n\n".join(sections))


# ── stop ──


async def _do_stop(workspace, target):
    """停止会话：stop 序列 + 清空排队 + 显式取消 flusher。"""
    manager = get_manager()
    cleared = manager.clear_queue(target.chat_id)
    # 显式取消 flusher（复审 C-L2 对齐设计 §3.2「取消 flusher」）
    queue = manager.get_queue(target.chat_id)
    if queue is not None and queue.flusher_task is not None:
        queue.flusher_task.cancel()
    try:
        summary = await stop_session(
            workspace, target.session_id, target.chat_id,
        )
    except Exception as exc:  # noqa: BLE001
        return make_error("停止会话", f"stop 序列失败：{exc!r}")
    # 复审 B-②（动态实验实锤）：stop 的 await 窗口内到达的 send 排进
    # 重建的新活队列，但旧 flusher 已取消未 done → _ensure_flusher 拒
    # 绝重启 → 孤立滞留（用户已收到「已排队」承诺却永不投递）。stop
    # 完成后复查：队列非空 → 等旧 flusher 退出（已取消，至多一轮即死）
    # → 重启投递（窗口内消息 = stop 后新请求）
    q_after = manager.get_queue(target.chat_id)
    if q_after is not None and q_after.items:
        old = q_after.flusher_task
        if old is not None and not old.done():
            try:
                await old
            except Exception:  # noqa: BLE001  已取消/异常退出均视为已死
                pass
        _ensure_flusher(workspace, target)
    note = f"，已清空排队 {cleared} 条" if cleared else ""
    return make_success(
        f"已停止：会话 {target.chat_id}（工具取消 {summary['cancelled_tools']} 项，"
        f"producer 停止={summary['stopped']}{note}）。"
        "终态由消费者写入，完成后 notice 通知照常触发。"
    )


# ── 内部 helper ──


def _agent_name(agent_id: str) -> str:
    try:
        from qwenpaw.config.utils import load_agent_config

        return str(load_agent_config(agent_id).name or agent_id)
    except Exception:  # noqa: BLE001
        return agent_id
