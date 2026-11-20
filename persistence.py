# -*- coding: utf-8 -*-
"""簿记身份持久化（v0.1.8，作者拍板混合方案）。

- 「身份账」持久化：spawn 完成时把 agent_id / depth / parent / spawned_at
  写入宿主 ``chat.meta["agent_tools"]``——跟随宿主 chats.json 落盘，
  重启/热装清簿记后短 id 匹配、祖先链防误用可重建。
- 「易变账」留内存：turns / queues / active / notices 不持久化（回合
  历史本就在会话文件里，恢复无意义）。
- 恢复即缓存：resolve miss 时扫各 enabled agent workspace 的 chats.json，
  把带 agent_tools meta 的会话回填为轻量 SpawnRecord（restored=True）；
  回填后续 resolve 走内存命中，天然节流。

写入事务模式对齐宿主 ChatManager.set_session_model（lock 下
read-modify-write + upsert，只合并自己命名空间键，不动兄弟键）。
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

try:
    from .manager import SpawnRecord, get_manager
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import SpawnRecord, get_manager

logger = logging.getLogger(__name__)

META_KEY = "agent_tools"


def build_spawn_meta(record: SpawnRecord, parent_chat_id) -> dict:
    """SpawnRecord → chat.meta["agent_tools"] 身份字段（终身不变，一次写入）。"""
    parent_agent_id, parent_session_id = record.parent or (None, None)
    return {
        "agent_id": record.agent_id,
        "depth": int(record.depth),
        "parent_agent_id": parent_agent_id,
        "parent_session_id": parent_session_id,
        "parent_chat_id": parent_chat_id,
        "spawned_at": record.created_at.isoformat(timespec="seconds"),
    }


async def persist_spawn_identity(workspace, record: SpawnRecord,
                                 parent_chat_id=None) -> bool:
    """spawn 成功后把身份字段写入 chat.meta["agent_tools"]。

    Args:
        workspace: 模板 agent 的 workspace（chat 建在它名下，chat_manager
            从它取）。
        record: spawn 刚登记的 SpawnRecord。
        parent_chat_id: 发起者宿主 chat_id（调试/审计用；祖先链恢复用
            parent_agent_id + parent_session_id 即可）。

    Returns:
        True = 写入成功；False = chat_manager/chat 缺失（调用方容错，
        不影响 spawn 主流程）。
    """
    chat_manager = getattr(workspace, "chat_manager", None)
    if chat_manager is None:
        logger.warning("workspace.chat_manager 不可用，spawn 身份未持久化")
        return False
    async with chat_manager._lock:  # noqa: SLF001 对齐宿主 setter 内部模式
        existing = await chat_manager._repo.get_chat(record.chat_id)
        if existing is None:
            logger.warning("chat %s 不在 chats repo，spawn 身份未持久化",
                           record.chat_id[:8])
            return False
        meta = dict(existing.meta or {})
        meta[META_KEY] = build_spawn_meta(record, parent_chat_id)
        updated = existing.model_copy(update={"meta": meta})
        updated.updated_at = datetime.now(timezone.utc)
        await chat_manager._repo.upsert_chat(updated)
    return True


def restore_ledger_from_chats(mgr=None) -> int:
    """扫各 enabled agent workspace 的 chats.json，回填带身份 meta 的会话。

    仅在 resolve miss 时调用（同步毫秒级本地 JSON 读；回填即缓存，
    同一 miss 不重复扫）。跳过内存已有记录（正常注册过 / 已恢复过）。

    Returns:
        本次回填条数。
    """
    if mgr is None:
        mgr = get_manager()
    try:
        from qwenpaw.config.utils import load_config

        profiles = load_config().agents.profiles
    except Exception:  # noqa: BLE001  qwenpaw 不可导入/配置异常 → 恢复跳过
        logger.warning("load_config 不可用，meta 恢复跳过", exc_info=True)
        return 0

    restored = 0
    for _agent_id, profile in profiles.items():
        if not getattr(profile, "enabled", False):
            continue
        workspace_dir = getattr(profile, "workspace_dir", None)
        if not workspace_dir:
            continue
        chats_path = Path(workspace_dir).expanduser() / "chats.json"
        try:
            with open(chats_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            continue
        except Exception:  # noqa: BLE001
            logger.warning("chats.json 读取失败（%s），该 agent 跳过",
                           chats_path, exc_info=True)
            continue
        for chat in data.get("chats", []):
            identity = (chat.get("meta") or {}).get(META_KEY)
            if not isinstance(identity, dict):
                continue
            cid = chat.get("id")
            if not cid or cid in mgr.spawn_records:
                continue
            parent_a = identity.get("parent_agent_id")
            parent_s = identity.get("parent_session_id")
            aid = str(identity.get("agent_id") or identity.get("template")
                      or _agent_id)
            try:
                depth = int(identity.get("depth") or 1)
            except (TypeError, ValueError):
                depth = 1
            spawned = identity.get("spawned_at")
            try:
                created = (datetime.fromisoformat(spawned)
                           if spawned else datetime.now())
            except (TypeError, ValueError):
                created = datetime.now()
            mgr.spawn_records[cid] = SpawnRecord(
                agent_id=aid,
                session_id=str(chat.get("session_id") or ""),
                user_id=str(chat.get("user_id") or ""),
                channel=str(chat.get("channel") or "console"),
                chat_id=cid,
                depth=depth,
                parent=(parent_a, parent_s) if parent_a and parent_s else None,
                created_at=created,
                restored=True,
            )
            restored += 1
    if restored:
        logger.info("meta 恢复回填 %d 条 spawn 簿记", restored)
    return restored
