# -*- coding: utf-8 -*-
"""测试 helpers：mock 工厂（tracker/workspace/resolver/归一化/通知气泡）。"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime


# ── tracker mock（四方法：get_status/attach_or_start/stream_from_queue/request_stop） ──


class MockTracker:
    """TaskTracker 等价 mock：可编排状态序列。"""

    def __init__(self, statuses=None):
        # statuses: 队列；get_status 逐个弹出，耗尽后停留末值
        self.statuses = list(statuses or ["idle"])
        self._lock = asyncio.Lock()
        self.attached = []          # (run_key, is_new_run) 记录
        self.detached = []          # (run_key, queue) 记录
        self.stopped = []           # (run_key, reason) 记录
        self.consumed = []          # 消费者收到的 SSE
        self.next_queue = None      # attach_or_start 返回的自定义队列

    async def get_status(self, run_key):
        async with self._lock:
            if len(self.statuses) > 1:
                return self.statuses.pop(0)
            return self.statuses[0]

    async def attach_or_start(self, run_key, payload, stream_fn,
                              owner=None, on_finished=None,
                              channel_id=None):
        self.attached.append((run_key, True))
        if self.next_queue is not None:
            queue = self.next_queue
            self.next_queue = None
        else:
            queue = asyncio.Queue()
            for sse in self.consumed:
                queue.put_nowait(sse)
            queue.put_nowait(None)   # sentinel：attach 即收尾（测试场景）
        return queue, True

    async def detach_subscriber(self, run_key, queue):
        self.detached.append((run_key, queue))

    async def request_stop(self, run_key, *, reason="user"):
        self.stopped.append((run_key, reason))
        return True

    async def stream_from_queue(self, queue, run_key, *, cancel_run=False):
        while True:
            sse = await queue.get()
            if sse is None:      # sentinel
                return
            yield sse


class IdleTracker(MockTracker):
    def __init__(self):
        super().__init__(statuses=["idle"])


class RunningThenIdleTracker(MockTracker):
    def __init__(self):
        super().__init__(statuses=["running", "idle"])


# ── workspace mock ──


class MockChannel:
    async def get_channel(self, name):
        return object() if name == "console" else None


class MockChatManager:
    async def get_or_create_chat(self, session_id, user_id, channel,
                                 name="New Chat", **reg):
        from dataclasses import dataclass as _dc

        @_dc
        class _Chat:
            id: str
            session_id: str
            user_id: str
            channel: str

        return _Chat(id=f"chat-{session_id[:12]}", session_id=session_id,
                     user_id=user_id, channel=channel)

    async def mark_chat_finished(self, run_key, finish_time):
        pass


@dataclass
class MockWorkspace:
    agent_id: str = "child"
    session_id: str = "sub-mock0001"
    user_id: str = "default"
    channel: str = "console"
    task_tracker: object = None
    channel_manager: object = None
    chat_manager: object = None
    session: object = None
    stream_query_calls: list = field(default_factory=list)

    def __post_init__(self):
        if self.task_tracker is None:
            self.task_tracker = MockTracker()
        if self.channel_manager is None:
            self.channel_manager = MockChannel()
        if self.chat_manager is None:
            self.chat_manager = MockChatManager()
        if self.session is None:
            self.session = MockSession()

    async def stream_query(self, req):
        self.stream_query_calls.append(req)
        # 产一个 message Completed 事件（messenger send_event 收）
        yield {"object": "message", "status": "completed", "text": "回复"}

    _app_services = None

    def set_coordinator(self, coordinator):
        self._app_services = type("_S", (), {"tool_coordinator": coordinator})()


class MockCoordinator:
    """tool_coordinator mock：cancel_running_for_session 记录。"""

    def __init__(self, cancelled=0):
        self.cancelled_count = cancelled
        self.calls = []

    async def cancel_running_for_session(self, session_id, *, agent_id,
                                         reason=None):
        self.calls.append((session_id, agent_id, reason))
        return self.cancelled_count


class MockSession:
    """workspace.session mock：get_session_state_dict 返回预置 state。"""

    def __init__(self, state=None):
        self.state = state or {}
        self.calls = []

    async def get_session_state_dict(self, session_id, user_id="", channel=""):
        self.calls.append((session_id, user_id, channel))
        return self.state


# ── 归一化 message stub（session_reader 输入形态） ──


class StubContent:
    def __init__(self, text=None, data=None):
        self.text = text
        self.data = data


class StubMsg:
    def __init__(self, role, mtype, content, timestamp=None):
        self.role = role
        self.type = mtype
        self.content = content
        self.metadata = {"timestamp": timestamp} if timestamp else {}


def make_state_dict(msgs):
    """构造宿主归一化前的 state dict（AgentState 形态）。

    测试里 session_reader 的 fetch_normalized_messages 会 mock 掉
    AgentState/agentscope_msg_to_message，这里只提供容器。
    """
    return {"agent": {"state": {"session_id": "x", "context": msgs}}}


# ── console_push_store mock ──


class MockPushStore:
    def __init__(self):
        self.appended = []

    async def append(self, session_id, text, *, sticky=False):
        self.appended.append((session_id, text, sticky))


# ── helper：回收 spawn 簿记 ──


def make_turn(status="running", finished=None, result_text="", error=""):
    from manager import TurnRecord

    return TurnRecord(
        status=status, finished_at=finished, result_text=result_text,
        error=error,
    )


def now():
    return datetime.now()


def make_record(chat_id, *, agent_id="child", session_id="sub-m0000001",
                user_id="u", channel="console", template="", depth=0,
                parent=None, turns=None, restored=False):
    """SpawnRecord 工厂（persistence/list_session 等测试共用）。"""
    from manager import SpawnRecord

    return SpawnRecord(
        agent_id=agent_id, session_id=session_id, user_id=user_id,
        channel=channel, chat_id=chat_id, template=template,
        depth=depth, parent=parent, turns=turns or [], restored=restored,
    )
