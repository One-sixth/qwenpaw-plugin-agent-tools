# -*- coding: utf-8 -*-
"""Agent Tools 簿记单例。

进程内存态（宿主重启 / 插件热重载丢失，无恢复语义——设计已接受）：
- SpawnRecord / TurnRecord：多回合簿记，wait / notice /「最新状态」一律
  跟随最新 TurnRecord
- _SessionQueue：communicate send 遇忙的独立排队结构（每会话一个 flusher）
- 活跃表：防误用拦截的祖先链判定数据源（提交成功登记 / 消费者退出移除）
- NoticeRecord：完成通知注册表（wake_* 身份注册时捕获）

提供 reset_manager() / inject_spawn_record() 测试钩子与 TTL 惰性清理。
"""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────

MAX_SPAWN_DEPTH = 5        # spawn 链深上限（发起者不在簿记 → 基准 0，子 depth=1）
QUEUE_LIMIT = 10           # 每会话排队上限，超限报错
TTL_HOURS = 24             # 终态记录过期时长
MAX_RECORDS = 500          # 簿记上限，超出按最旧活动 LRU 淘汰
ANCESTOR_MAX_DEPTH = 32    # 祖先链追溯步数上限（visited 防环）

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED)


# ── 数据结构 ──────────────────────────────────────────────


@dataclass
class TurnRecord:
    """一个回合（spawn 提交 / communicate send 提交 / 排队提交各一条）。"""

    status: str = STATUS_RUNNING
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    result_text: str = ""
    error: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


@dataclass
class SpawnRecord:
    """每 chat 一条的会话簿记。"""

    agent_id: str
    session_id: str
    user_id: str
    channel: str
    chat_id: str
    depth: int = 0
    parent: Optional[Tuple[str, str]] = None          # (agent_id, session_id)
    spawn_request_context: Optional[dict] = None      # spawn 回合审批路由键
    created_at: datetime = field(default_factory=datetime.now)
    turns: "list[TurnRecord]" = field(default_factory=list)
    restored: bool = False                            # 由 chats.json meta 恢复回填（v0.1.8，非本轮 spawn）

    @property
    def latest_turn(self) -> Optional[TurnRecord]:
        return self.turns[-1] if self.turns else None

    @property
    def status(self) -> Optional[str]:
        """最新回合状态（口径：wait/notice/「最新状态」一律跟随最新回合）。"""
        turn = self.latest_turn
        return turn.status if turn else None

    @property
    def last_active_at(self) -> datetime:
        """最后活动时间（TTL/LRU 依据）：最新回合结束时间，否则 spawn 时间。"""
        turn = self.latest_turn
        if turn and turn.finished_at:
            return turn.finished_at
        if turn:
            return turn.started_at
        return self.created_at


@dataclass
class PendingSend:
    """排队中的 send 请求（flusher 提交时需要的全部信息）。"""

    text: str                      # 已加身份前缀的最终文本
    parent: Optional[Tuple[str, str]]   # 发起者会话引用（活跃表登记用）
    request_context: Optional[dict] = None  # 续聊审批路由键（原样携带）
    enqueued_at: datetime = field(default_factory=datetime.now)


@dataclass
class SessionQueue:
    """communicate send 排队结构（每会话一个，flusher 懒启动）。"""

    chat_id: str
    items: "deque[PendingSend]" = field(default_factory=deque)
    flusher_task: Optional[object] = None   # asyncio.Task（懒启动时赋值）
    cancel_flag: bool = False               # stop 时置位：flusher 取队首前复查
    in_flight: bool = False                 # 已取队首未定局（复审 B-①：
                                            # popleft→attach 让出窗口内新
                                            # send 直提会超车 → 标记期间
                                            # 一律排队保 FIFO）


@dataclass
class ActiveEntry:
    """防误用活跃表条目：该 (agent_id, session_id) 有本插件发起的回合运行中。

    提交成功时登记，消费者 try/finally 移除（无论何种退出方式）。
    turn = 注册时绑定的回合——消费者移除前比对身份（复审 B-③：
    T1 消费者 body-read 让出窗口内 flusher 注册 T2 覆盖同键条目，
    T1 无条件移除会误删 T2 的条目导致拦截失效；绑定后 T1 只移除
    自己的回合条目）。
    """

    chat_id: str
    parent: Optional[Tuple[str, str]] = None
    turn: Optional["TurnRecord"] = None


@dataclass
class NoticeRecord:
    """完成通知注册（设计 §3.4）。wake_* 在注册时从当前上下文捕获。"""

    target_chat_id: str
    target_agent_id: str
    target_session_id: str
    wake_agent_id: str
    wake_user_id: str
    wake_session_id: str
    wake_channel: str
    registered_at: datetime = field(default_factory=datetime.now)
    cancelled: bool = False
    completion_sent: bool = False
    last_error: str = ""          # failed 终态时由 on_turn_finished 填充
    watch_task: Optional[object] = None   # 未簿记兜底监视协程（cancel 时取消）


# ── 簿记单例 ──────────────────────────────────────────────


class AgentToolsManager:
    """Agent Tools 簿记单例（纯内存结构，无 IO）。"""

    def __init__(self) -> None:
        self.spawn_records: Dict[str, SpawnRecord] = {}     # chat_id → record
        self.session_queues: Dict[str, SessionQueue] = {}   # chat_id → queue
        self.active_sessions: Dict[Tuple[str, str], ActiveEntry] = {}
        self.notice_records: Dict[str, NoticeRecord] = {}   # chat_id → record
        self.background_tasks: "set" = set()  # 消费者/监视协程登记（卸载钩子统一取消）
        self.resources = None  # SDK ResourceScope（plugin._activate 注入；None=回落裸 create_task）

    def register_task(self, task) -> None:
        """登记后台任务（消费者/监视协程），完成时自动移除（防热重载孤儿）。"""
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    def spawn_managed(self, coro, description: str):
        """创建后台任务：SDK resources.task 托管优先（卸载自动清理 +
        异常兜底记录，任务完成 Registration 自动回收）；未注入（测试 /
        直调工具函数）回落裸 create_task，行为与旧路径一致。"""
        if self.resources is not None:
            return self.resources.task(coro, description)
        return asyncio.create_task(coro)

    def cancel_all_tasks(self) -> int:
        """取消全部登记任务（卸载钩子用）。返回取消数。"""
        count = 0
        for task in list(self.background_tasks):
            if not task.done():
                task.cancel()
                count += 1
        self.background_tasks.clear()
        return count

    # ── SpawnRecord 簿记 ──

    def register_spawn(self, record: SpawnRecord) -> SpawnRecord:
        """登记新 spawn 会话（turns 由调用方初始化）。"""
        self.spawn_records[record.chat_id] = record
        return record

    def get_record(self, chat_id: str) -> Optional[SpawnRecord]:
        return self.spawn_records.get(chat_id)

    def get_record_by_session(self, session_id: str) -> Optional[SpawnRecord]:
        """session_id → 簿记反查（chat_id 一步自愈用）。"""
        for record in self.spawn_records.values():
            if record.session_id == session_id:
                return record
        return None

    def append_turn(self, chat_id: str, turn: TurnRecord) -> TurnRecord:
        """追加回合。无簿记时新建 SpawnRecord（send 无簿记场景，设计 §4.1）。

        返回该回合所属的 SpawnRecord。
        """
        record = self.spawn_records.get(chat_id)
        if record is None:
            raise KeyError(chat_id)  # 调用方（submit 层）负责新建完整记录
        record.turns.append(turn)
        return record

    def all_records(self) -> "list[SpawnRecord]":
        return list(self.spawn_records.values())

    # ── 防误用活跃表 ──

    def register_active(
        self,
        agent_id: str,
        session_id: str,
        chat_id: str,
        parent: Optional[Tuple[str, str]],
        turn: Optional[TurnRecord] = None,
    ) -> None:
        self.active_sessions[(agent_id, session_id)] = ActiveEntry(
            chat_id=chat_id, parent=parent, turn=turn,
        )

    def remove_active_if_turn(
        self, agent_id: str, session_id: str, turn: TurnRecord,
    ) -> None:
        """身份守卫移除：仅当条目仍绑定当前回合时才 pop（复审 B-③）。"""
        entry = self.active_sessions.get((agent_id, session_id))
        if entry is not None and entry.turn is turn:
            self.active_sessions.pop((agent_id, session_id), None)

    def remove_active(self, agent_id: str, session_id: str) -> None:
        self.active_sessions.pop((agent_id, session_id), None)

    def is_active(self, agent_id: str, session_id: str) -> bool:
        """当前会话是否有本插件发起的回合运行中（防误用判定前提）。"""
        return (agent_id, session_id) in self.active_sessions

    def is_on_ancestor_chain(
        self,
        cur_agent_id: str,
        cur_session_id: str,
        target_agent_id: str,
        target_session_id: str,
    ) -> bool:
        """目标会话是否在当前会话的祖先链上（沿 parent 向上追溯）。

        visited 集合防环 + 步数上限（设计 §4.3）；命中即拦截。
        """
        key = (cur_agent_id, cur_session_id)
        target = (target_agent_id, target_session_id)
        visited = set()
        for _ in range(ANCESTOR_MAX_DEPTH):
            if key in visited:
                return False  # 环防御（理论不可达，拦截已保证 parent 链为树）
            visited.add(key)
            entry = self.active_sessions.get(key)
            if entry is None:
                return False
            if entry.parent is None:
                return False
            if entry.parent == target:
                return True
            key = entry.parent
        return False

    # ── 排队 ──

    def get_queue(self, chat_id: str) -> Optional[SessionQueue]:
        return self.session_queues.get(chat_id)

    def get_or_create_queue(self, chat_id: str) -> SessionQueue:
        queue = self.session_queues.get(chat_id)
        if queue is None:
            queue = SessionQueue(chat_id=chat_id)
            self.session_queues[chat_id] = queue
        return queue

    def clear_queue(self, chat_id: str) -> int:
        """清空队列（stop 语义），返回清掉条数；结构保留待 flusher 收尾。"""
        queue = self.session_queues.get(chat_id)
        if queue is None:
            return 0
        queue.cancel_flag = True
        n = len(queue.items)
        queue.items.clear()
        return n

    def drop_queue(self, chat_id: str) -> None:
        """队列收尾后彻底移除结构。"""
        self.session_queues.pop(chat_id, None)

    # ── notice 注册表 ──

    def register_notice(self, record: NoticeRecord) -> NoticeRecord:
        self.notice_records[record.target_chat_id] = record
        return record

    def get_notice(self, chat_id: str) -> Optional[NoticeRecord]:
        return self.notice_records.get(chat_id)

    def cancel_notice(self, chat_id: str) -> Optional[NoticeRecord]:
        record = self.notice_records.get(chat_id)
        if record is not None:
            record.cancelled = True
        return record

    def all_notices(self) -> "list[NoticeRecord]":
        return list(self.notice_records.values())

    def latest_error(self, chat_id: str) -> str:
        """最新回合的 error 摘要（failed 通知文本用）。"""
        record = self.spawn_records.get(chat_id)
        turn = record.latest_turn if record else None
        return (turn.error or "") if turn else ""

    # ── TTL 惰性清理（设计 §4.1：每次簿记访问入口顺带扫描，无后台任务） ──

    def sweep_expired(
        self,
        ttl_hours: float = TTL_HOURS,
        max_records: int = MAX_RECORDS,
    ) -> Tuple[int, int]:
        """扫描过期终态记录 + LRU 淘汰。返回 (过期删除数, LRU 淘汰数)。"""
        now = datetime.now()
        ttl_delta = timedelta(hours=ttl_hours)
        expired = 0

        # 终态且 finished 超 TTL 的 SpawnRecord
        for chat_id in [
            cid for cid, r in self.spawn_records.items()
            if r.latest_turn is not None
            and r.latest_turn.is_terminal
            and r.latest_turn.finished_at is not None
            and now - r.latest_turn.finished_at > ttl_delta
        ]:
            del self.spawn_records[chat_id]
            expired += 1

        # 已投递 / 已取消的 NoticeRecord
        for chat_id in [
            cid for cid, n in self.notice_records.items()
            if (n.completion_sent or n.cancelled)
            and now - n.registered_at > ttl_delta
        ]:
            del self.notice_records[chat_id]

        # LRU：超出上限按最后活动时间从旧到新淘汰（running 记录顺延跳过
        # ——其消费者会把终态写进被淘汰的孤儿对象，复审 C-L3）
        evicted = 0
        over = len(self.spawn_records) - max_records
        if over > 0:
            ordered = sorted(
                self.spawn_records.values(), key=lambda r: r.last_active_at
            )
            for record in ordered:
                if over <= 0:
                    break
                if (record.latest_turn is not None
                        and record.latest_turn.status == STATUS_RUNNING):
                    continue  # 运行中记录不淘汰
                del self.spawn_records[record.chat_id]
                evicted += 1
                over -= 1
        return expired, evicted

    # ── 测试钩子 ──

    def reset_manager(self) -> None:
        """清空全部簿记（conftest autouse fixture 用）。

        resources/background_tasks 有意不清——生产卸载后插件模块被宿主
        重建（loader pop 模块），新单例 resources=None 由 _activate 重新
        注入；测试侧由 conftest 显式归位 m.resources=None。
        """
        self.spawn_records.clear()
        self.session_queues.clear()
        self.active_sessions.clear()
        self.notice_records.clear()

    def inject_spawn_record(
        self,
        chat_id: str,
        agent_id: str = "child",
        session_id: str = "sub-test0001",
        user_id: str = "default",
        channel: str = "console",
        depth: int = 1,
        parent: Optional[Tuple[str, str]] = None,
        spawn_request_context: Optional[dict] = None,
        turns: Optional["list[TurnRecord]"] = None,
    ) -> SpawnRecord:
        """注入任意簿记状态（wait stale 分支 / list 三段式 / 拦截祖先链测试用）。"""
        record = SpawnRecord(
            agent_id=agent_id,
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            chat_id=chat_id,
            depth=depth,
            parent=parent,
            spawn_request_context=spawn_request_context,
            turns=turns if turns is not None else [],
        )
        self.spawn_records[chat_id] = record
        return record


# ── 模块级单例 ────────────────────────────────────────────

_manager: Optional[AgentToolsManager] = None


def get_manager() -> AgentToolsManager:
    """获取簿记单例（首次调用时创建）。"""
    global _manager
    if _manager is None:
        _manager = AgentToolsManager()
    return _manager
