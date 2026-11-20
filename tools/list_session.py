# -*- coding: utf-8 -*-
"""agent_tools_list_session — 列簿记会话（设计 §3.8，找回 chat_id 的发现路径）。"""

import logging

try:
    from ..manager import STATUS_RUNNING, TERMINAL_STATUSES, get_manager
    from ..utils import make_success
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import STATUS_RUNNING, TERMINAL_STATUSES, get_manager
    from utils import make_success

logger = logging.getLogger(__name__)


def agent_tools_list_session():
    """列出本插件 spawn 的全部会话（状态/chat_id/最新回合/通知注册 🔔），用于找回 chat_id；人工会话不在列。"""
    manager = get_manager()
    manager.sweep_expired()
    records = manager.all_records()
    if not records:
        return make_success(
            "当前没有本插件 spawn 的会话记录"
            "（历史 spawn 会话在首轮操作后自动从宿主 chats.json 恢复）"
        )

    notices = {n.target_chat_id for n in manager.all_notices()
               if not n.cancelled and not n.completion_sent}

    running, done, failed, restored_list = [], [], [], []
    for record in records:
        status = record.status
        turn = record.latest_turn
        if record.restored and turn is None:
            # meta 恢复的轻量记录：无回合历史，仅身份（v0.1.8）
            spawned_at = record.created_at.strftime("%Y-%m-%d %H:%M:%S")
            restored_list.append(
                f"[restored] chat_id={record.chat_id} session={record.session_id}"
                f"（agent_id={record.agent_id}，spawned {spawned_at}，"
                f"历史回合不随簿记恢复）"
            )
            continue
        latest = ""
        if turn is not None:
            latest = f"，最新回合 {turn.status}"
        bell = " 🔔" if record.chat_id in notices else ""
        spawn_at = record.created_at.strftime("%H:%M:%S")
        base = (
            f"chat_id={record.chat_id} session={record.session_id}"
            f"（agent_id={record.agent_id}，spawn {spawn_at}{latest}）{bell}"
        )
        if status == STATUS_RUNNING or (turn is not None
                                        and turn.status == STATUS_RUNNING):
            running.append(f"[running] {base}")
        elif status in TERMINAL_STATUSES:
            if status == "completed":
                done.append(f"[completed] {base}")
            else:
                reason = turn.error if turn is not None and turn.error else "（无摘要）"
                failed.append(f"[{status}] chat_id={record.chat_id}（原因：{reason}）")
        else:
            done.append(f"[{status or 'unknown'}] {base}")

    sections = []
    if restored_list:
        sections.append(f"== 恢复（{len(restored_list)}） ==\n" + "\n".join(
            f"#{i + 1} {line}" for i, line in enumerate(restored_list)
        ))
    if running:
        sections.append(f"== 运行中（{len(running)}） ==\n" + "\n".join(
            f"#{i + 1} {line}" for i, line in enumerate(running)
        ))
    if done:
        sections.append(f"== 已完成（{len(done)}） ==\n" + "\n".join(
            f"#{i + 1} {line}" for i, line in enumerate(done)
        ))
    if failed:
        sections.append(f"== 失败/取消（{len(failed)}） ==\n" + "\n".join(
            f"#{i + 1} {line}" for i, line in enumerate(failed)
        ))
    return make_success("\n".join(sections))
