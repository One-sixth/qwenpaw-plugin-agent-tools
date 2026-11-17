# -*- coding: utf-8 -*-
"""通知系统：注册制 + 完成通知（completed/failed/cancelled 全触发）+ 会话聚合投递。

照抄 process-tools 通知设计（决策 6）并去掉周期通知——只要完成通知。
核心语义：

- **opt-in**：子会话不自动推送任何东西，必须显式 agent_tools_notice 注册；
  cancel=True 取消注册并撤回待发通知；
- **触发源双路**：
  1. 簿记路径（主）：消费者（submit.consume_and_track）写终态后调
     ``on_turn_finished`` → 入队聚合器；
  2. 监视协程（未簿记兜底）：注册时目标 running 但簿记缺失 → 轮询
     tracker，**先观察到 running 再转 idle 才触发**（迁移门，防重启后
     对从未运行的会话发假完成通知，审查 M8）；幂等由 completion_sent 保证。
- **注册时目标已终态** → 不注册，直接返回终态 + 正文（引导 wait/recv）；
- **会话聚合投递**（swap-and-clear 同款）：
    1. 首条通知启动 flusher，睡 FLUSH_WINDOW_SECONDS（3s）聚合；
    2. 窗口到点原子拿走全部累积 → 过滤已取消 → 空则退出；
    3. 会话忙（唤醒提交 is_new_run=False / 信使 busy）→ 存活条目放回
       队首再等 3s，**忙等无上限、永不放弃**；
    4. 空闲 → 聚合为一条文本投出（文本注册时已冻结，撤回靠 cancelled
       过滤 + 3s 窗口）。
  真失败不重试记日志；busy 是唯一重试态。无锁设计：单线程事件循环 +
  全同步原子段。
- **console 双投递**：console_push_store 气泡 + _submit_turn 唤醒父会话
  （唤醒提交不建消费者，is_new_run=True 后直接 detach——fire-and-forget）；
- **非 console 信使路由**：in-process ``await run_wake``（决策 18，无 HTTP）。
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

try:
    from .manager import TTL_HOURS, NoticeRecord, get_manager
    from .utils import truncate_text
except ImportError:  # 兼容 pytest 直接以项目根导入
    from manager import TTL_HOURS, NoticeRecord, get_manager
    from utils import truncate_text

logger = logging.getLogger(__name__)

# 聚合窗口（秒）：首条通知后至少等这么久；会话忙时同粒度忙等（无上限）
FLUSH_WINDOW_SECONDS = 3.0

# 通知正文截断上限（正文可能巨大，防撑爆父会话上下文）
NOTIFY_BODY_LIMIT = 4000


@dataclass
class _NoticeAccumulator:
    """一个唤醒目标会话的通知累积器（key = wake 四元组）。

    仅内存态：宿主关闭/崩溃即丢，无恢复语义。
    """

    channel: str = "console"   # 首条通知的频道快照（同 key 恒定）
    items: list = field(default_factory=list)
    flusher_task: Optional[asyncio.Task] = None


@dataclass(eq=False)
class PendingNotice:
    """聚合缓存条目：注册引用（可撤回）+ 已冻结文本。

    eq=False：flusher/cancel 按对象身份过滤（swap-and-clear 拿走即
    消费，不做字段相等比较）。
    """

    record: NoticeRecord
    text: str

    def is_alive(self) -> bool:
        """渲染/投递前的存活检查（cancel 三道闸之一）。"""
        return not self.record.cancelled


def build_notice_text(record: NoticeRecord, status: str,
                      result_text: str) -> str:
    """构建完成通知文本（终态时冻结；completed 带正文截断）。"""
    # template 兜底显示目标 agent id（监视协程路径簿记缺失 → label 空）
    label = record.template_label or record.target_agent_id
    head = f"[agent 会话通知] chat_id={record.target_chat_id}（template={label}）"
    if status == "completed":
        body = truncate_text(result_text or "", NOTIFY_BODY_LIMIT)
        tail = "" if len(result_text or "") <= NOTIFY_BODY_LIMIT else (
            f"\n\n（正文超长已截断至 {NOTIFY_BODY_LIMIT} 字符，"
            "完整内容用 agent_tools_communicate(chat_id, action='recv') 读取）"
        )
        return f"{head}\n状态：completed\n—— 结果正文 ——\n{body}{tail}"
    reason = f"\n原因：{record.last_error}" if record.last_error else ""
    hint = "" if status == "failed" else "\n（会话被 stop 停止）"
    return (
        f"{head}\n状态：{status}{reason}{hint}\n"
        "结果正文：无（可用 agent_tools_communicate(chat_id, action='recv') "
        "尝试读取会话文件）。"
    )


class AgentToolsNotifier:
    """通知系统单例（纯内存态）。"""

    def __init__(self) -> None:
        self._accumulators: dict = {}

    # ── 累积器管理 ──

    def _acc_key(self, record: NoticeRecord) -> tuple:
        return (
            record.wake_agent_id, record.wake_user_id,
            record.wake_session_id, record.wake_channel,
        )

    def _get_or_create_acc(self, record: NoticeRecord) -> _NoticeAccumulator:
        key = self._acc_key(record)
        acc = self._accumulators.get(key)
        if acc is None:
            acc = _NoticeAccumulator(channel=record.wake_channel)
            self._accumulators[key] = acc
        return acc

    # ── 触发（消费者终态 / 监视协程共用入口） ──

    def on_turn_finished(self, chat_id: str, status: str,
                         result_text: str = "") -> bool:
        """回合终态通知入队（幂等：completion_sent 三道闸之一）。

        Returns: True = 已入队。
        """
        manager = get_manager()
        record = manager.get_notice(chat_id)
        if record is None or record.cancelled or record.completion_sent:
            return False
        record.completion_sent = True
        if status == "failed":
            record.last_error = manager.latest_error(chat_id)

        text = build_notice_text(record, status, result_text)
        acc = self._get_or_create_acc(record)
        acc.items.append(PendingNotice(record=record, text=text))
        self._ensure_flusher(acc, key=self._acc_key(record))
        logger.info(
            "完成通知入队：chat=%s status=%s wake=%s",
            chat_id, status, record.wake_session_id[:30],
        )
        return True

    # ── 未簿记兜底监视协程（迁移门，审查 M8） ──

    async def watch_untracked(self, record: NoticeRecord) -> None:
        """轮询 tracker 直到目标回合终态（须先见 running 再见 idle）。

        注册时目标 running 但簿记缺失（重启/消费者丢失）时的兜底路径；
        簿记路径同时有效时由 completion_sent 幂等去重。
        安全阀：最长轮询 TTL_HOURS（对齐簿记 TTL），超时放弃并记日志
        （复审 C-M1：防目标永不 running 的永久协程）。
        """
        try:
            from .submit import get_workspace
        except ImportError:  # 兼容直接导入
            from submit import get_workspace
        from datetime import datetime, timedelta

        deadline = datetime.now() + timedelta(hours=TTL_HOURS)
        seen_running = False
        try:
            while True:
                # 现取 workspace（宿主规矩：never cached pointers）
                workspace = get_workspace(record.target_agent_id)
                if workspace is None:
                    return  # agent 已下线，监视无意义
                tracker = workspace.task_tracker
                status = await tracker.get_status(record.target_chat_id)
                if status == "running":
                    seen_running = True
                elif seen_running and status == "idle":
                    # 迁移门通过：确有回合跑完 → 终态通知（正文从会话文件读）
                    try:
                        from .manager import get_manager
                        from .utils import locate_chat
                        from . import session_reader
                    except ImportError:  # 兼容直接导入
                        from manager import get_manager
                        from utils import locate_chat
                        import session_reader

                    result_text = ""
                    try:
                        # 兜底场景的本质就是簿记缺失（复审 A-H1 修复后的
                        # 实测修正）——locate_chat resolver 兜底定位读取，
                        # 不依赖簿记对象
                        loc = await locate_chat(record.target_chat_id)
                        if loc is not None:
                            result_text = await session_reader.read_latest_turn_body(
                                workspace, loc,
                            )
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "监视协程正文读取失败（chat=%s）",
                            record.target_chat_id, exc_info=True,
                        )
                    self.on_turn_finished(
                        record.target_chat_id, "completed", result_text,
                    )
                    return
                # 未见 running 的 idle：迁移门拦截，继续轮询
                if datetime.now() >= deadline:
                    logger.warning(
                        "监视协程超时放弃（chat=%s，%.0fh 无终态）",
                        record.target_chat_id[:8], TTL_HOURS,
                    )
                    return
                await asyncio.sleep(FLUSH_WINDOW_SECONDS)
        except asyncio.CancelledError:
            pass  # cancel 注册 / 宿主 shutdown / 卸载钩子

    # ── cancel ──

    def cancel(self, chat_id: str) -> int:
        """取消注册 + 撤回待发条目 + 取消监视协程（三道闸）。"""
        manager = get_manager()
        record = manager.cancel_notice(chat_id)
        removed = 0
        if record is not None:
            # 取消兜底监视协程（复审 C-M1）
            task = record.watch_task
            if task is not None and not task.done():
                task.cancel()
            for acc in self._accumulators.values():
                before = len(acc.items)
                acc.items = [
                    p for p in acc.items if p.record is not record
                ]
                removed += before - len(acc.items)
        return removed

    # ── flusher ──

    def _ensure_flusher(self, acc: _NoticeAccumulator, key: tuple) -> None:
        """懒启动 flusher（每唤醒目标会话一个）。"""
        if acc.flusher_task is not None and not acc.flusher_task.done():
            return
        acc.flusher_task = asyncio.create_task(self._flusher(acc, key))

    async def _flusher(self, acc: _NoticeAccumulator, key: tuple) -> None:
        """聚合投递循环（swap-and-clear + 忙等放回，无上限）。"""
        while True:
            await asyncio.sleep(FLUSH_WINDOW_SECONDS)
            # 窗口到点原子拿走全部累积并清空队列（拿走即消费）
            batch, acc.items = acc.items, []
            batch = [p for p in batch if p.is_alive()]  # 过滤已取消
            if not batch:
                if not acc.items:
                    self._accumulators.pop(key, None)
                    return
                continue  # 投递期间新通知直接入队、不受本批影响

            ok = await self._deliver(key, acc, batch)
            if ok:
                if not acc.items:
                    self._accumulators.pop(key, None)
                    return
                continue
            # busy：存活条目放回队首（保持顺序），忙等无上限
            acc.items[:0] = batch

    # ── 投递 ──

    async def _deliver(self, key: tuple, acc: _NoticeAccumulator,
                       batch: list) -> bool:
        """聚合文本一口气投出。True=投出（含真失败放弃）；False=busy 重试。"""
        try:
            from .submit import _submit_turn, get_workspace
            from .messenger import run_wake
        except ImportError:  # 兼容直接导入
            from submit import _submit_turn, get_workspace
            from messenger import run_wake

        wake_agent_id, wake_user_id, wake_session_id, wake_channel = key
        text = "\n\n".join(p.text for p in batch)
        # 投递前存活复查（复审 C-M2：撤回落在渲染后→投递 await 窗口）
        if not all(p.is_alive() for p in batch):
            logger.info("通知撤回生效于投递窗口（%d 条丢弃）", len(batch))
            return True  # 已取消批次自然消失
        workspace = get_workspace(wake_agent_id)
        if workspace is None:
            logger.warning("唤醒目标 workspace 不存在（agent=%s）", wake_agent_id)
            return True  # 真失败不重试

        if wake_channel == "console":
            # 双投递①：气泡（60s 过期 + 500 条上限——丢失不影响唤醒主链路）
            try:
                from qwenpaw.app import console_push_store

                await console_push_store.append(wake_session_id, text)
            except Exception:  # noqa: BLE001
                logger.warning("通知气泡投递失败", exc_info=True)
            # 双投递②：唤醒父会话（不建消费者，fire-and-forget）
            try:
                result = await _submit_turn(
                    workspace, wake_session_id, wake_user_id, "console", text,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("唤醒提交异常：%r", exc)
                return True  # 真失败不重试
            if not result.is_new_run:
                return False  # 父会话忙 → flusher 忙等重试无上限
            await workspace.task_tracker.detach_subscriber(
                result.chat_id, result.queue,
            )
            logger.info("通知已投出：唤醒 %s（%d 条聚合）",
                        wake_session_id[:30], len(batch))
            return True

        # 非 console：信使路由（in-process 直调）
        result = await run_wake(
            workspace, channel=wake_channel, user_id=wake_user_id,
            session_id=wake_session_id, text=text,
        )
        if result.get("busy"):
            return False  # 忙等重试
        if not result.get("ok"):
            logger.warning("信使投递失败：%s", result.get("error"))
        return True  # ok/真失败批次自然消失


# ── 模块级单例 ────────────────────────────────────────────

_notifier: Optional[AgentToolsNotifier] = None


def get_notifier() -> AgentToolsNotifier:
    """获取通知单例（首次调用时创建）。"""
    global _notifier
    if _notifier is None:
        _notifier = AgentToolsNotifier()
    return _notifier


def reset_notifier() -> None:
    """测试钩子：清空单例与累积器（conftest autouse fixture 用）。"""
    global _notifier
    _notifier = None
    try:
        from .messenger import reset_gates
    except ImportError:  # 兼容 pytest 直接以项目根导入
        from messenger import reset_gates

    reset_gates()
