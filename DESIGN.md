# qwenpaw-plugin-agent-tools 总体设计文档

> 状态：**v2.2**（新增 agent_tools_list_agent；list 改名 list_session）｜ 2026-11-16 ｜ 撰写与拍板：作者
> v2.1 → v2.2 变更：新增第 7 工具 `agent_tools_list_agent`（列可用 agent，对齐官方 list_agents）；原 `agent_tools_list` 改名 `agent_tools_list_session`（第 8 工具）；文案节顺延 §3.9；meta.tools 8 工具。
> v2 → v2.1 变更：① communicate send 直接回合也建消费者跟踪终态（主链路修复）；② NoticeRecord 结构定稿 + 唤醒身份注册时捕获；③ `_submit_turn` 签名/返回契约定稿；④ communicate send 的 request_context 定义（续聊审批路由）；⑤ messenger 改 in-process 直调（决策 11 全自洽）；⑥ 前端伴生暂不做（决策 19）；⑦ list 工具与文案定稿分列 §3.7~§3.9；⑧ wait stale 分支、TTL 惰性清理、测试节、骨架补全（utils.py 等）、当前上下文获取器、depth 基准、TOCTOU 补偿、消费者禁级联硬约束、tracker buffer 风险。
> v1 → v2 变更：修正三处版本偏差（tool_call/created_at/agent.state.context）、stop 路径与顺序、detach 清理、防误用收窄为祖先链判定、spawn 深度限制、新增 agent_tools_list、notice 简化为仅完成通知、SpawnRecord 多回合模型、队列独立结构、返回文案定稿、热重载风险。
> 参考骨架：`qwenpaw-plugin-process-tools`（工程形态对齐）。⚠️ 文中宿主源码行号为指示性锚（以锁定的 qwenpaw_version 实测为准）。

---

## 1. 项目定位

面向 **agent / 子 agent 会话**的管理插件，把「spawn → 等待 → 交流 → 通知 → 上下文读取」整条链路接通。

官方工具链的空档（本插件的差异化价值）：

| 官方工具 | 限制 |
|---------|------|
| `spawn_subagent` | 只能以**当前 agent** 身份 spawn 一次性 worker |
| `chat_with_agent` / `submit_to_agent` | 持久会话语义，前台超时会**级联停止目标会话** |
| 无 | 没有「以其他 agent 为模板 spawn 会话」的组合 |
| 无 | 没有「向子 agent 会话续聊」的工具（人工在 WebUI 可以继续聊，agent 不能） |
| 无 | 没有会话上下文读取/导出工具（读 Scroll 驱逐前历史只限自己） |

与官方功能重叠**没关系**（作者已拍板）。

---

## 2. 核心机制（已对照 QwenPaw 源码验证 + 三路审查 + 两轮复审复核）

### 2.1 内部函数路线（不走 HTTP loopback）

插件工具函数运行在宿主进程内，直接使用内部对象：

- **拿 manager**：应用启动顺序为 `plugin_loader.registry.set_workspace_manager(workspace_registry)`（`app/_app.py:583`）先于 `load_all_plugins`（`:595`），插件 `register()` 时 `api._registry.get_workspace_manager()` 已就位；缓存到插件自己的模块级单例（process-tools 的 `get_manager()` 同款模式）。workspace 通过 `wm.agents[agent_id]` 或 `await wm.get_agent(agent_id)`（懒加载）获取。
  ⚠️ 宿主自己的规矩（`plugins/workspace_projector.py:445`）："never cached object pointers"——**每次操作现取 workspace**（含 flusher 循环内、消费者内），不缓存 workspace 对象。
- **当前会话完整上下文获取器**：插件统一从 `qwenpaw.app.agent_context` 读 `get_current_agent_id()` / `get_current_user_id()` / `get_current_session_id()` + 当前频道 contextvar，**缺省回落 "default"**（同 process-tools 的 `get_current_context()`，`utils.py:33-41` 范式）——context_read 当前会话、身份前缀、notice 唤醒身份、防误用判定全部依赖它
- **提交一轮（`_submit_turn` 契约定稿，三路共用）**：
  ```python
  async def _submit_turn(
      workspace, session_id, user_id, channel, text, *,
      registration_fields: dict | None = None,   # spawn 传 source="subagent"+parent/root；send/唤醒传 None
      request_context: dict | None = None,        # spawn 传官方构造；send 传续聊路由键；唤醒传 None
  ) -> SubmitResult:   # (chat_id, is_new_run)
  ```
  内部序列照抄 `POST /console/chat/task` 端点（`app/routers/console.py:1090` 起）：
  1. `workspace.channel_manager.get_channel("console")` → console_channel
  2. 构造 native_payload（`channel_id` / `sender_id` / `content_parts` / `message_metadata`（可为 None） / `meta{session_id, user_id, request_context}`）
  3. `workspace.chat_manager.get_or_create_chat(session_id, sender_id, channel_id, name=..., **(registration_fields or {}))` → chat（获得 chat_id）。**spawn 路径必须附加 `source="subagent"` + `parent_session_id` + `root_session_id`**（等价端点 `_chat_registration_fields`，`console.py:380-397`）——否则 WebUI 丢失父子关联
  4. `tracker.attach_or_start(chat.id, payload, _tracked_stream, owner=workspace, on_finished=...)` → `(queue, is_new_run)`；`is_new_run=False` = 会话忙 → **必须立即 `tracker.detach_subscriber(chat.id, queue)`**（queue 已挂进订阅列表并预填缓冲，不 detach 会泄漏整个运行期的 SSE 事件）
  5. **消费者策略**：spawn 与 communicate send 提交 → `asyncio.create_task` 跑消费者 `tracker.stream_from_queue(queue, chat.id)` 收集 SSE + 终态簿记（两者都需要终态正文）；**notice 唤醒提交 → 不建消费者，`is_new_run=True` 后直接 `detach_subscriber` 丢弃 queue**（唤醒是 fire-and-forget 通知动作，父会话该回合由父会话自身的 wait/notice 观察目标）；**唤醒路径 `is_new_run=False`（父会话忙）→ busy → notice flusher 忙等重试无上限**（显式，同 send 排队语义）
- **忙闲判定**：`tracker.get_status(chat.id)` 返回 `idle/running` 两态（`task_tracker.py:72-78`，覆盖 spawn、communicate、人工 WebUI 发起的一切回合）
- **停止**（顺序严格，照抄 stop 端点 `console.py:616-640`）：
  1. **先**取消工具会话：`cancel_running_for_session(session_id, agent_id=目标workspace.agent_id, reason=...)` ——该方法在跨 workspace 单例 `app_services.tool_coordinator` 上（**不是 tracker 的方法**）；进程内获取：`workspace._app_services.tool_coordinator`（`workspace.py:433-435` 注入）或 `api._registry.get_plugin_http_app().state.app_services.tool_coordinator`。顺序原因（端点注释原文）：工具子进程先观察 cancel_event 优雅退出，再停 producer
  2. **后**停 producer：`workspace.task_tracker.request_stop(chat.id, reason="user")`（reason 是关键字参数）
  ⚠️ **硬约束：stop 序列只允许被 `agent_tools_communicate(action="stop")` 调用；消费者代码内禁止出现任何对目标会话的停止调用**（保证「绝不停止目标会话」总纲的机制层落地）
- **消息历史**：**复用宿主归一化**（作者拍板）：`session.get_session_state_dict(session_id, user_id, channel)` → `AgentState.model_validate` → `agentscope_msg_to_message(...)`（`chats/api.py:876-930` 同款路径；`chats/utils.py` 的转换函数一次性解决 tool_use/tool_call 双形态、timestamp 归一、hint 块排除、data 媒体块映射、scroll 占位过滤）。session 路径定位用 `session_relative_paths(session_id, user_id, channel)`，**不要手拼路径**
- **Scroll 驱逐历史**：`make_recall_history(history_db_path=..., session_id=..., agent_id=...)`（`agents/context/scroll/recall_tool.py:839`，公共工厂，in-process 只读）直接复用；`history_db_path` 用模块级函数 `history_service_path(ws)`（需以目标 workspace 为参调用；agent 未启用 scroll 时返回 `None` → 负索引区间整体不可用，优雅降级）
  ⚠️ **实施权衡（v0.1.0 落地记录）**：工厂返回 LLM 工具形态（ToolChunk 文本 + seq 行格式），插件块时间线渲染需要**结构化行数据**——故 context.py 直用工厂底层 `MemorySpace`（同款 read-only ATTACH + authorizer，每调用新建读完 close 不泄漏连接），并自行处理 `blocks` 列 JSON 字符串解析（写入端 `_to_json`）与 `sql_query` row_cap/`_truncated` 标记行（复审 B-M1/B-M2）
- **通知气泡**：`qwenpaw.app.console_push_store` 内存单例可直接 import（`append(session_id, text, sticky=...)` 要求两者非空否则静默丢弃；气泡 60s 过期 + 500 条上限——忙等重投时旧气泡会被清，投递设计需知晓）

**已知代价**：耦合 workspace 内部方法签名（`stream_one` / `attach_or_start` / `stream_from_queue`），QwenPaw 升级可能 break——作者接受（个人插件紧跟宿主版本）。

### 2.2 ID 体系（已实测确认）

| ID | 规则 | 唯一性 | 用途 |
|----|------|--------|------|
| session_id | 主会话 `{毫秒ts}-{8字符}`；spawn 子会话 `sub-{8位hex}`；跨 agent `{from}:to:{to}:{ts}:{uuid8}` | **per-agent 命名空间内唯一**，跨 agent 可能同形态 | session 文件定位、get_or_create_chat 三元组 |
| chat_id | UUID4，持久化于各 agent workspace 的 `chats.json` | **全局唯一、创建后不变**（唯一失效方式=删除会话，显式失效） | **对外主键** |
| task_id | `task-{12位hex}`（HTTP 端点层簿记） | 端点层 | **弃用，不暴露** |

- 官方全局解析器：`await find_chat_matches(load_config(), reference_id, current_agent_id)`（`app/chats/resolver.py:37`；`load_config` 来自 `qwenpaw.config.utils`，mtime 缓存无参可调）。**返回 `list[ChatResolution]`**（`agent_id` + 完整 ChatSpec）：`len==0` → 未找到；`len>1` → 歧义（对齐 `/resolve` 端点 409 语义）；`len==1` → `ChatResolution.agent_id` 即会话归属
- 插件簿记存 `(agent_id, session_id, chat_id)` + `user_id`/`channel`（session 读取需要完整三元组，可从 ChatResolution 的 ChatSpec 补全）；内部 tracker 按 chat_id、session 文件按 session_id

### 2.3 消息块结构（当前版本实锤，2026-08+ 格式）

> ⚠️ v1 的实测取样偏差已修正：早期 session 文件（2026-06）为 `tool_use`/`timestamp`/`agent.memory.content` 旧格式，当前版本如下（抽验 `default_1786713472242-8kj7lpf.json`：tool_call 48 处 / tool_use 0 处 / timestamp 0 处 / created_at 16 处）。

- raw JSON 顶层：`{"agent": {"state": {"session_id", "summary", "context": [...], ...}, "scroll": ..., "mode_state": ...}}`——**消息在 `agent.state.context`**
- 消息信封：`{name, role(user/assistant/system), content: [块], id, metadata, created_at, finished_at, usage}`——**时间字段是 `created_at`**（ISO-T 微秒格式如 `2026-08-14T21:18:27.370181`，naive 本地时间；`timestamp` 只是宿主侧属性别名）
- 块类型：`thinking` / `text` / **`tool_call`**（2.0 形态：`input` 是 **JSON 字符串**，含 `state`/`suggested_rules` 字段） / `tool_result` / `data`（媒体块） / `hint`（宿主明确排除出 transcript）
- 兼容口径：工具块判定 `btype in ("tool_use", "tool_call")`（与宿主 `chats/utils.py` 同口径，兼容旧会话文件）；`input` 兼容 JSON 字符串与 dict 双形态
- **复用宿主归一化**（见 2.1）：上述解析细节由 `agentscope_msg_to_message` 承担，插件只在归一化结果上做轮/块分类

**正文 vs 自言自语（monologue）判定规则**：一轮 = 一条 user 消息开启，到下一条 user 消息前。轮内**最后一条**带 text 的 assistant 消息 → 正文；其余 assistant 消息的 text 块 → monologue。一条 assistant 消息多段 text（实测 128:1 极罕见，唯一实例为运行时追加的 max-iterations 终止通知）→ **全部归入正文按序拼接**，无需特判。

**timestamp 输出**：源数据 naive 本地时间 → 输出**本地时间+偏移**格式（如 `2026-11-16 11:12:45.102+08:00`）（作者拍板）。

---

## 3. 工具规格（8 个）

对外主键一律 **chat_id**；`agent_id` 不对外暴露（簿记推导 / 全局 resolver 解析）。所有收 chat_id 的工具入口做**形态校验**（2026-11-17 作者拍板）：只认 UUID 全量形态，或**短 id = 长 id 第一段（严格 8 位 hex）**——在簿记已知 chat_id 内前缀匹配，命中 1 条解析、≥2 条显式歧义报错「存在多个重复，请给完整的长 id」（git commit hash 同款，禁止静默取第一个）、0 条无匹配；session_id 形态输入给专门提示帮助自纠（2026-11-17 拍板移除 session_id 一步自愈——自愈依赖插件簿记，官方会话/主会话恒拒绝，语义混乱）。

### 3.1 agent_tools_spawn

```
agent_tools_spawn(task, template="", background=True, timeout=None) -> text
```

- `template`：目标 agent 的 **id**（list_agents 返回的 id 字段，非显示名）；空字符串 = 当前 agent 身份。无效时报错：`ERROR: template 'xxx' 不存在或未启用。用 list_agents 查看可用 agent（注意传 id 字段，如 'demo-agent'，非显示名）。`
- `background=True` 默认：提交后立即返回（文案见 3.9①）
- `background=False`：前台等待，但 **timeout 语义 = wait 语义**——超时**绝不停止目标会话**，返回「仍在运行」+ 四选项提示（见 3.9⑤）
- 自动在 task 文本头部加中文身份前缀（见 4.5）
- request_context：**整体照抄官方 `_build_spawn_request_context`**（`agent_management.py:973-1015`）——含 `_spawn_subagent: True`、`root_session_id`/`root_agent_id`/`parent_session_id`/`user_id`/`channel`/`channel_meta`/`inherited_project_dirs`/approval 继承；**缺 user_id/channel/channel_meta 会让审批路由三维为空**（审查 M9）
- 深度限制：SpawnRecord.depth = 发起者链深 +1（发起者不在簿记 → 基准 0，子 depth=1）；超 `MAX_SPAWN_DEPTH=5` 拒绝（报错提示当前链路与上限）
- **不支持 batch**（作者拍板）
- 成功后写簿记 SpawnRecord（turns 初始化）+ 登记防误用活跃表（含 parent 引用）

### 3.2 agent_tools_communicate

```
agent_tools_communicate(chat_id, action: "send"|"recv"|"stop", text="", ...) -> text
```

纯后台工具（作者拍板），action 模式对齐 `process_tools_communicate`（命名亦对称，作者拍板）：

- **send**：向该会话发起新一轮。会话忙 → **排队**（不报错）：入独立 `_SessionQueue`（见 4.2）+ 懒启动 flusher；会话空闲 → 直接提交（**建消费者跟踪终态**，见 4.1 消费者策略）。立即返回（文案见 3.9②③）。自动加身份前缀。text 为空时报错：`ERROR: send 需要 text 参数（要发送的消息文本）；recv/stop 不需要 text`
  - **request_context（续聊审批路由）**：从簿记取该 chat 原 spawn 回合的审批路由键（`root_session_id`/`user_id`/`channel`/`channel_meta`）**原样携带**（经 `_submit_turn(request_context=...)`），保证续聊回合审批路由与 spawn 回合一致（审查 M10）；`_spawn_subagent: True` 标志一并携带（对已存在会话 get_or_create 无副作用）；无簿记的会话（人工 WebUI 会话）→ 传 None（走宿主默认路由，降级接受）
- **recv**：读该会话**最新一轮的全部正文**（轮内最后一条带 text 的 assistant 消息 = 最终输出 + 该轮更早 assistant 的 text 块 = 自言自语，按时间序拼接）；thinking 块、tool_call/tool_result 块不读；轮内无 text → 返回提示（见 3.9④）。**docstring 首行注明：仅读最新一轮；读历史轮用 agent_tools_context_read**。任务运行中也能读（现场读进度）
- **stop**：先 `tool_coordinator.cancel_running_for_session(...)` 再 `tracker.request_stop(chat.id, reason="user")`（顺序见 2.1），**同时清空该会话 `_SessionQueue` 并取消 flusher**
- 防误用拦截见 4.3（祖先链收窄版）

### 3.3 agent_tools_wait

```
agent_tools_wait(chat_id, timeout=900) -> text
```

- 智能轮询（wait_agent_task 同款节奏）：0-10s 每 1s、10-30s 每 3s、30-60s 每 5s、60s+ 每 10s
- 终态返回**最新回合的最终结果正文**（口径见 4.1 多回合模型）
- **超时不杀会话**（作者明确决策）：返回「仍运行中」+ 四选项提示（见 3.9⑤），其中续等选项显式写「可加大 timeout」
- 起始即 idle 且全程 idle（未簿记 + 无 running 迹象）→ 返回「无运行中回合」而非「已完成」（防重启后误报，审查 M8）
- **stale 分支**：簿记=running 但 tracker=idle（消费者异常退出未写终态）→ 视为终态：从 session 文件读正文，标注「已完成（簿记与运行状态不一致，正文从会话文件读取）」（复审 M4 采纳）
- 消费者跑批期间：wait 每次轮询顺带做 TTL 惰性清理（见 4.1）

### 3.4 agent_tools_notice

```
agent_tools_notice(chat_id, cancel=False) -> text
```

- **照抄 process-tools 通知设计**（作者拍板），**但去掉周期通知——只要完成通知**（作者拍板；不设 interval_seconds）
- 注册制 opt-in + 会话聚合投递（3s 窗口 swap-and-clear）+ console 双投递（`console_push_store` 气泡 + `_submit_turn` 唤醒父会话）+ 非 console 频道信使路由（in-process 直调 `run_wake`，见 4.6）+ 忙等重试无上限 + cancel 取消并撤回待发通知
- **completed / failed / cancelled 三种终态全部触发**（作者明确要求：token 额度耗尽等异常终止也要通知）
- 注册时目标已终态（消费者已退出、退出事件已错过）→ **不注册**，直接返回终态 + 结果正文，引导 `wait/communicate recv`（对齐 process-tools「已结束不可注册」边界）
- 未簿记回合的监视协程：**必须先观察到 `running` 再转 `idle` 才触发**（迁移门，防重启后对从未运行的会话发假完成通知，审查 M8）
- notice 注册记录结构（复审 H2 采纳）：

```python
NoticeRecord:
    target_chat_id, target_agent_id, target_session_id   # 被监视的子会话
    wake_agent_id, wake_user_id, wake_session_id, wake_channel   # 唤醒目标=父会话完整身份
    registered_at, cancelled, completion_sent
# wake_* 在注册时从当前上下文捕获（get_current_* contextvar + 频道 contextvar，缺省回落 "default"
# ——同 process-tools tools/notice.py 的 session_key 捕获范式）
```

### 3.5 agent_tools_context_read

```
agent_tools_context_read(chat_id=None, start=None, count=5,
                         include_think=False, include_tool=False,
                         include_monologue=False, include_compressed=False) -> text
```

- `chat_id=None` → 当前会话（当前上下文获取器）
- **正负 idx 定位**（时间线连续，负=更早）：`idx >= 0` = 可见区域轮（0 = 可见区最旧轮）；`idx < 0` = Scroll 驱逐轮（`-1` = 紧邻可见区最近的被驱逐轮，`-E` = 最早）。映射：被驱逐轮按 seq 升序 s=0..E-1 → `idx = s - E`
- **`start=None` 默认读可见区最近 `count` 轮**（对齐 LLM「读最近」直觉，审查 MED-4 采纳）；显式传 start 才按索引起读。**负 start = 从最末轮往前数第 |start| 轮起**（与 Python `list[-5:]` 直觉一致）
- docstring 内嵌 idx 语义示例（见 3.9⑥）
- 结果开头固定输出索引信息（格式消歧义）：`本次读取 idx 1~3（共 3 轮）｜可用范围 -10~20（负=驱逐历史，需 include_compressed=True；正=当前可见区）`；include_compressed=False 时输出 `可用范围 0~20（未启用驱逐历史，include_compressed=True 可读负索引）`
- 过滤参数（作者拍板）：`[user]` 块与正文块**恒可见**；`include_monologue` 只控制自言自语；`include_think` / `include_tool` 控制思考块与工具块
- `include_compressed=True` 的负 idx 轮经 `make_recall_history` 读取；`history_service_path(ws)` 返回 None → 负索引不可用 + 提示

### 3.6 agent_tools_context_export

```
agent_tools_context_export(chat_id=None, output_path=<必填>,
                           include_think=False, include_tool=False,
                           include_monologue=False, include_compressed=False) -> text
```

- 全量导出为 Markdown 文件（`output_path` 必填，作者拍板），返回路径；缺失时报错给自愈示例（见 3.9⑧）
- **块时间线格式**（作者设计稿定稿）——按块粒度平铺，每块一段，分隔线 + `[块类型] 时间戳` + 内容：

```
————————
[user] 2026-11-16 11:12:45.102+08:00
（用户提问，恒可见）

————————
[think] 2026-11-16 11:12:47.330+08:00            ← include_think
（思考内容）

————————
[monologue] 2026-11-16 11:12:49.881+08:00        ← include_monologue
（自言自语）

————————
[tool_call] 2026-11-16 11:12:50.214+08:00        ← include_tool
read_file(file_path="D:\Software\xtquant\xtdata.py")

————————
[tool_result] 2026-11-16 11:12:52.845+08:00      ← include_tool
（session 文件内已有内容，原样导出）

————————
[assistant] 2026-11-16 11:13:01.552+08:00        ← 正文，恒可见
（最终输出）
```

- `[tool_call]` 内容 = 函数形式 `工具名(参数名=值, ...)`：字符串值加引号、数字/布尔/null 原样、list/dict 紧凑 JSON 内联、**不截断原样输出**（作者拍板）
- `[tool_result]` **所见即所得**：session 文件内存什么导什么，不做二次截断、不读取上游截断协议附带的 tool_result 结果文件
- `include_compressed=True` 时驱逐轮的块一并导出（标注负 idx）；`hint` 块恒不导出；`data` 媒体块标注占位

### 3.7 agent_tools_list_agent（第 7 工具，作者拍板新增，对齐官方 list_agents）

```
agent_tools_list_agent() -> text
```

- **数据来源（纯内部路线）**：`load_config()` 读 `agents.profiles`（id / workspace_dir / enabled）+ 逐个读各 workspace 的 `agent.json`（`name` / `description`）——不走官方 HTTP `/agents`（决策 11 自洽）
- **输出**：紧凑一行式，每行 `id ｜ name ｜ enabled ｜ description`；当前 agent 标注 ★，disabled 标注（disabled 不可作 template，spawn 校验会拒绝）
- 用途：spawn 的 `template` 取值来源（功能与官方 `list_agents` 对齐，输出格式更紧凑省 token）
- docstring：`查看当前所有可用 agent（id/名字/启用状态/描述）。spawn 的 template 传这里的 id 字段（非显示名）。`

### 3.8 agent_tools_list_session（第 8 工具，原 agent_tools_list 改名，作者拍板）

```
agent_tools_list_session() -> text
```

- 列出簿记中全部 SpawnRecord（三段式：运行中 / 已完成 / 失败取消），每行：编号、状态、chat_id、session_id、template、spawn 时间、最新回合状态
- **通知注册状态一并显示**（🔔 标记，参考 process_tools_list 的通知标记列）
- docstring 边界：`仅列出本插件本进程内 spawn 的会话（宿主重启/插件重载后清空）；人工 WebUI 会话不在列`
- 用途：会话被上下文压缩后 LLM 找回 chat_id 的插件级发现路径
- 返回文案见 3.9⑨

### 3.9 返回文案定稿（精简原则：chat_id 完整值只在 [CHAT_ID] 行出现一次；示例行用参数名占位，LLM 自填；说明文字从简）

**① spawn 后台成功**：
```
[CHAT_ID: <uuid>]
[SESSION: sub-xxxxxxxx]

子 agent 会话已运行。后续可选（chat_id 即上面 CHAT_ID 值）：
  agent_tools_wait(chat_id) 等完成取正文（推荐）
  agent_tools_notice(chat_id) 挂完成通知
  agent_tools_communicate(chat_id, action='recv') 读进度
  agent_tools_communicate(chat_id, action='stop') 停止
```

**② communicate send 提交成功**：
```
已提交：会话 <chat_id> 开始新一轮（已加身份前缀）。等完成用 agent_tools_wait(chat_id)，读进度用 action='recv'。
```

**③ communicate send 排队**：
```
已排队：会话 <chat_id> 忙碌或有排队消息，本消息排第 N 位，按顺序自动提交。stop 可停止并清空排队。
```

排队条件（2026-11-17 扩展）：`running` **或队列非空**——busy→idle 过渡窗口内
（flusher 尚未取走队首）直接提交会超车排队消息，统一入队保 FIFO。send 入口
见 stop 残留的死结构（cancel_flag=True）→ drop 重建，防消息被 stale flag 静默吞掉。

**④ recv 无正文**：
```
无正文可读：最新一轮尚无正文（可能仍在思考/调用工具）。可稍后 recv 再读、wait 等完成、context_read 读历史轮。
```

**⑤ 前台/等待超时**（spawn 前台与 wait 共用句式）：
```
等待超时：会话 <chat_id> 仍运行中（已等 N 秒，未停止、不受影响）。
可 wait(chat_id, timeout=1800) 续等、notice(chat_id) 挂通知、recv 读进度、stop 停止。
```

**⑥ context_read docstring 内嵌 idx 说明**：
```
idx：正=可见区轮（0=最旧）；负=Scroll 驱逐轮（-1=紧邻可见区，需 include_compressed=True）。
start 负数=从末轮倒数（-5 即倒数第 5 轮起）；默认读最近 count 轮。
```

**⑦ 防误用拦截报错**（作者定稿原文，不变）：
```
当前是子agent对话回合，内容只需在正文正常输出即可，无需（也不能）通过交流工具回复父会话。
```

**⑧ output_path 缺失**：
```
ERROR: output_path 必填（导出 Markdown 路径）。建议写 <workspace>\memory\context_export_<chat_id前8位>.md。
```

**⑨ agent_tools_list_session 返回**（三段式 + 🔔 通知标记）：
```
== 运行中（2） ==
#1 [running] chat_id=ab12cd34-… session=sub-1a2b3c4d（template=demo-agent，spawn 11:03:45，最新回合 running）🔔
== 已完成（3） ==
#3 [completed] chat_id=…（template=demo-agent，完成 11:20:01）
== 失败/取消（1） ==
#6 [failed] chat_id=…（原因：<摘要>）
（无记录时输出：当前没有本插件 spawn 的会话记录）
```

## 4. 关键设计

### 4.1 簿记（多回合模型）

插件 manager 单例维护（进程内存态，宿主重启/插件热重载丢失、无恢复语义——作者接受）：

```python
SpawnRecord:  # 每 chat 一条
    agent_id, session_id, user_id, channel, chat_id
    template, depth           # spawn 链深（未簿记发起者基准 0）
    parent: (agent_id, session_id) | None   # 发起者（祖先链追溯用）
    spawn_request_context: dict | None      # spawn 回合审批路由键（communicate send 续聊时原样携带）
    turns: list[TurnRecord]   # 多回合：spawn + 每次 communicate send 各一条
    # TurnRecord: status(running/completed/failed/cancelled), started_at,
    #             finished_at, result_text, error
    # 口径：wait/notice/「最新状态」一律跟随最新 TurnRecord

_SessionQueue:  # 独立结构（communicate 目标可以是任意会话）
    {chat_id: _SessionQueue(queue: deque, flusher_task, cancel_flag)}

NoticeRecord:  # 见 3.4（target_* + wake_* 身份 + cancelled/completion_sent）
```

- **消费者策略（复审 H1 采纳）**：spawn 与 communicate send 的提交**都建消费者**（收集 SSE + 写 TurnRecord 终态与 result_text——send 目标 chat 无 SpawnRecord 时**新建记录**（turns 从该回合起计），有则**追加 TurnRecord**；「不新建 SpawnRecord」仅指 notice 唤醒路径）
- **TTL 清理（复审 M6 采纳）**：惰性清理——每次簿记访问（wait/notify/list/拦截判定入口）顺带扫描过期终态记录（24h）并按 500 条 LRU 淘汰；无后台任务

### 4.2 排队 flusher（竞态闭合）

- communicate send 遇忙 → 入 `_SessionQueue` + 懒启动 flusher（每会话一个，done() 检查复用）
- flusher 循环：睡 3~5s → 查 tracker → running 继续 → idle → **复查 cancel 标志 → 提交前用 resolver 校验目标 chat 仍存在**（已删除 → 丢弃队列 + 记日志 + 可选通知父会话，防 get_or_create_chat 重建僵尸会话）→ `_submit_turn` 提交队首（建消费者跟踪终态）
- **被抢占**（提交时 `is_new_run=False`）：立即 `detach_subscriber` + 请求放回队首 + 重试
- **TOCTOU 双闸**：① 取队首前复查 cancel 标志（await 段内 stop 到达 → 本次提交放弃）；② **提交成功后再次复查**——若 stop 恰在「复查通过 → attach_or_start 完成」窗口内到达 → 对刚启动的 run **补偿执行 stop 序列**（复审 ⚠️M5 采纳）
- flusher 自身异常退出 → done() 懒启动模式由下次 send 复活；队列上限 10 条，超限报错
- **连续失败放弃**（2026-11-17 作者拍板）：仅「空闲 → 投递 → 失败 → 放回队首」计一次失败（running=没轮到、被抢占=外部正当占用，均不计；成功清零）；连续 10 次（`FLUSHER_MAX_CONSECUTIVE_FAILURES`）→ **先 drop_queue 再发气泡**（sticky，发往队首消息发起者会话）+ ERROR 日志，放弃退出——消息不丢原则在此有界让位于止损
- 排队请求提交后变新回合：追加该 chat SpawnRecord 的 TurnRecord

### 4.3 防误用拦截（祖先链收窄版，作者拍板）

- **登记**：spawn / communicate send 提交成功时，登记目标 `(agent_id, session_id)` → 活跃表条目（含 `parent` = 发起者会话引用）
- **判定（收窄版）**：communicate 被调用时——若当前会话在被调起回合中（命中活跃表）**且** action=send **且** 目标会话在**当前会话祖先链**上（沿 parent 引用向上追溯，visited 集合防环 + 深度上限 32）→ 报错（文案 3.9⑦）
  - S1 send 父 A → 拦 ✅；S1 send 自己 spawn 的 S2 → 放行（后代方向）；recv/stop 任何目标 → 放行；兄弟互发 → 放行
  - 环不可能形成：向祖先的 send 被拦 → parent 链恒为树
- **移除**：消费者 `try/finally` 保证**无论何种退出方式**（正常/producer_error/CancelledError/未捕获异常）都写终态 + 移除活跃表条目（审查 H1 采纳）
- **自愈通道**：拦截命中时做活性复核——tracker 已 idle 且簿记非 running → 移除条目并放行
- **深度限制**：spawn 链 `MAX_SPAWN_DEPTH=5`，超限拒绝
- 边界（作者已知）：仅拦截本插件的 communicate；官方 chat_with_agent/submit_to_agent 不在拦截范围；**人工 WebUI 在子会话发起的回合不进活跃表，不被拦**（接受）

### 4.4 wait 语义总纲（作者明确决策）

**前台等待超时 = 只放弃等待，绝不停止目标会话。** 所有前台等待（spawn 前台、wait）一律遵循且强制 timeout 有界——环形前台互等最多空转到超时，不可能永久死锁。超时返回四选项提示（3.9⑤）。与官方 `chat_with_agent` 的级联取消语义**有意不同**。**机制层硬约束**：stop 序列只允许 communicate stop 调用（见 2.1）。

### 4.5 身份前缀（作者定稿文案，spawn 与 communicate send 共用，2026-11-17 改版）

**完整版**（发起者 chat_id 反查成功）：
```
[系统提示：本回合请求来自 Agent「demo-agent」（agent_id: default，chat_id: 32b6b25e-f641-4e92-a1e3-f0e5423348df）。你的回复对方会读取到，本回合无需再向对方发消息；之后要主动联系对方，就用这个 chat_id 调 agent_tools_communicate 发送。]
```

**降级短文案**（异常防御，预期永不触发——发起者三类（主会话/task session/插件子会话）全在 chats 表，数据实锤 269 条；触发即宿主异常，WARNING 日志留痕）：
```
[系统提示：本回合请求来自 Agent「demo-agent」（agent_id: default）。你的回复对方会读取到，本回合无需再向对方发消息。]
```

- Name = 发起者 agent.json 的 `name`；chat_id = 发起者会话的宿主 chat_id（UUID，`resolve_parent_chat_id` 经宿主 resolver 反查）
- **UUID 全文只出现一次**：回访指引用「这个 chat_id」就近指代（2026-11-17 作者拍板）
- 回复指引「对方会读取到」统一 spawn（直接返回）与 send 排队（wait/recv 拉取）两种场景语义
- 发起者 session 不在宿主 chats 表（官方 task session 等）→ 降级版，无回访指引
- agent.json `name` 为空 → 保留空引号「」（不影响识别，agent_id 段仍在）

### 4.6 notice 通知机制要点（照抄 process-tools + 复审修正）

- 聚合投递 / 双投递 / 忙等重试无上限：全对齐 process-tools notifier.py
- **信使路由改 in-process**（复审 M1 采纳，决策 18）：messenger.py 保留 `run_wake` 链路（对齐 process-tools messenger.py：channel 预检 → get_or_create_chat → 忙检 → 跑一轮 → send_event 回频道），但 **notifier flusher 直接 in-process `await run_wake(...)`，不经 HTTP loopback、无 web_api.py**——决策 11「内部函数路线」全自洽
- 终态判定区分**两种取消**（审查 M1 采纳）：`producer_cancelled`（run 的 stream_one 被取消 = 目标会话被 stop）→ `cancelled`；消费者任务自身被取消（宿主 shutdown 等，run 可能还在跑）→ **不算终态**，记录为观察丢失
- 重启/未簿记场景：迁移门（3.4）+ wait 的「无运行中回合」降级（3.3）+ stale 分支（3.3）

---

## 5. 文件骨架（对齐 process-tools 工程形态，复审 M7 补全）

```
qwenpaw-plugin-agent-tools/
├── plugin.json          # id/name/version/meta.tools 声明 8 工具（逐工具元数据见下表）
├── plugin.py            # 入口：register_tool × 8 + 启动钩子 + 卸载钩子（取消全部任务）
├── manager.py           # 单例：SpawnRecord/turns 簿记 + _SessionQueue + 防误用活跃表 + NoticeRecord 注册表
│                        #   + TTL 惰性清理 + reset_manager()/inject_spawn_record()（测试钩子）
├── submit.py            # _submit_turn 契约实现（§2.1）+ workspace 现取 + tool_coordinator 获取 + 消费者
├── session_reader.py    # 宿主归一化适配（get_session_state_dict → AgentState → agentscope_msg_to_message）
│                        #   + 轮/块分类 + 正文/monologue 判定 + idx 映射
├── context.py           # context_read / context_export 共用逻辑 + Markdown 渲染（块时间线）
├── notifier.py          # notice：注册制 + 聚合投递 + 双投递 + in-process 唤醒 + 迁移门 + reset_notifier()（测试钩子）
├── messenger.py         # run_wake 链路（对齐 process-tools messenger.py；in-process 直调，无 HTTP 端点）
├── utils.py             # 当前上下文获取器（get_current_context）/ make_error / make_success /
│                        #   chat_id 形态校验（只认 UUID） / truncate 小工具
├── tools/
│   ├── __init__.py
│   ├── spawn.py
│   ├── communicate.py
│   ├── wait.py
│   ├── notice.py
│   ├── list_agent.py    # agent_tools_list_agent（第 7 工具：列可用 agent）
│   ├── list_session.py  # agent_tools_list_session（第 8 工具：列簿记会话）
│   ├── context_read.py
│   └── context_export.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py      # autouse reset 单例 fixture + 临时数据目录（process-tools conftest 范式）
│   ├── helpers.py       # mock 工厂：tracker/attach_or_start/stream_from_queue/request_stop/
│   │                    #   workspace.channel_manager/chat_manager/find_chat_matches/
│   │                    #   get_session_state_dict/agentscope_msg_to_message/make_recall_history
│   ├── test_spawn.py / test_communicate.py / test_wait.py / test_notice.py /
│   ├── test_list_agent.py / test_list_session.py / test_context.py / test_manager.py / test_submit.py
├── requirements.txt     # （预计仅测试依赖；运行时零第三方依赖，纯宿主内部 API）
├── README.md / CHANGELOG.md / AGENTS.md / MEMORY.md
```

**不包含**（决策 18/19）：web_api.py（messenger in-process 化）、frontend/index.js（前端伴生暂不做）。

**meta.tools 逐工具声明**（name/description/icon/tool_type/target_param）：

| name | icon | tool_type | target_param |
|------|------|-----------|--------------|
| agent_tools_spawn | 🚀 | internal | （无） |
| agent_tools_communicate | 💬 | internal | chat_id |
| agent_tools_wait | ⏳ | internal | （无） |
| agent_tools_notice | 🔔 | internal | （无） |
| agent_tools_list_agent | 🧭 | internal | （无） |
| agent_tools_list_session | 📋 | internal | （无） |
| agent_tools_context_read | 📖 | internal | （无） |
| agent_tools_context_export | 📤 | internal | （无） |

### 6. 测试策略（复审 M5 采纳）

- **单例 reset**：`manager.reset_manager()` / `notifier.reset_notifier()`（conftest autouse fixture，防跨测试状态泄漏）
- **簿记注入**：`manager.inject_spawn_record(...)` 造任意簿记状态（wait stale 分支、list 三段式、拦截祖先链均靠注入测）
- **mock 清单**（helpers.py）：tracker 四方法（get_status/attach_or_start/stream_from_queue/request_stop）、workspace.channel_manager/chat_manager、`find_chat_matches`、`get_session_state_dict`/`agentscope_msg_to_message`、`make_recall_history`、`console_push_store`
- **真实 fixture**：现网 session JSON 截段（含 tool_call 新格式 + 旧 tool_use 格式各一）做 session_reader 解析测试
- 对照 process-tools tests/ 结构与命名

---

## 7. 风险与降级语义（已知并接受）

| 风险 | 处置 |
|------|------|
| 内部函数签名随 QwenPaw 升级 break | 个人插件紧跟宿主版本；`qwenpaw_version.min/max` 锁定范围；文档行号为指示性锚 |
| 簿记进程内存态，宿主重启/**插件热重载**丢失 | find_chat_matches 兜底定位；未簿记回合降级语义已定义（wait「无运行中回合」/stale 分支/notice 迁移门） |
| **插件热重载孤儿任务**（开发期高频 `install --force`） | 卸载钩子取消本插件全部任务（flusher/消费者/notice 监视协程）；长任务关键操作前校验 manager 实例归属 |
| 人工 WebUI 回合无成功/失败细分 | wait 报「已完成」+ 读到的正文；起始即 idle → 「无运行中回合」；stale → 标注不一致 |
| spawn 子会话落在模板 agent workspace | WebUI 切到该 agent 可见、可人工续聊（这正是「非一次性」设计） |
| 兄弟会话乒乓互发（LLM 行为层活锁） | 机制不防护：每环消耗真实回合 token 成本可见、stop 任何一方即断；祖先链拦截已切断「向调起者回发」类环；队列上限 10 条 |
| 排队期间目标会话被删除 | flusher 提交前 resolver 校验，已删除 → 丢弃队列 + 日志 |
| console 气泡 60s 过期/500 条上限 | 忙等重投设计知晓此约束（气泡丢失不影响唤醒主链路） |
| 簿记终态记录内存增长 | TTL 24h / 500 条 LRU，惰性清理 |
| **tracker SSE buffer 随 run 增长**（「非一次性」长会话下更明显） | 宿主行为，插件不可控；消费者读完 SSE 后 run 终态 buffer 由宿主生命周期管理（合入核对 ❌建议4 补记） |
| WebUI 不自动刷新（notice 唤醒落盘后） | 一期不做前端伴生（决策 19）；观察实际使用后再定 |

---

## 8. 已拍板决策记录（勿作为问题翻案）

1. 与官方工具重叠没关系；不做差异化规避
2. spawn 默认后台；前台 timeout = wait 语义，**超时绝不停止目标会话**；所有前台等待强制 timeout 有界；stop 序列只允许 communicate stop 调用（硬约束）
3. 子 agent 会话**非一次性**：完成后父 agent 可 communicate 续聊、人工可 WebUI 续聊
4. 不支持 batch
5. communicate 纯后台（send 立即返回 / recv 立即读 / stop 立即停）；拿最终正文用 wait 或 notice
6. notice 照抄 process-tools 通知设计，**但去掉周期通知，只要完成通知**；failed/cancelled 也触发通知
7. 身份前缀固定中文，文案见 4.5；防误用拦截报错文案见 3.9⑦
8. 对外主键 chat_id（形态校验只认 UUID，2026-11-17 拍板移除 session_id 自愈）；task_id 弃用；agent_id 不对外暴露
9. context_read 正负 idx + start/count 翻页 + 默认读最近 count 轮；正文与 user 提问恒可见
10. context_export 必填 output_path、Markdown 块时间线格式、tool_call 函数形式不截断、tool_result 所见即所得不读结果文件
11. 内部函数路线（不走 HTTP loopback）；接受内部签名耦合；session 解析**复用宿主归一化**（`agentscope_msg_to_message`）
12. 时间戳输出 = 本地时间+偏移
13. communicate 工具命名 = `agent_tools_communicate`（与 `process_tools_communicate` 全称对称）
14. **防误用拦截 = 祖先链收窄版**（只拦 send 且目标在祖先链）+ `MAX_SPAWN_DEPTH=5` + 活性复核自愈（无死锁可能，无需全拦）
15. **list 双工具**：`agent_tools_list_agent`（第 7 工具，列可用 agent：load_config + agent.json 纯内部读，紧凑一行式，★ 当前 / disabled 标注；对齐官方 list_agents）+ `agent_tools_list_session`（第 8 工具，原 agent_tools_list 改名：簿记会话列表 + 🔔 通知注册状态）
16. 返回文案定稿（3.9①~⑨）；**精简原则：chat_id 完整值只在 [CHAT_ID] 行出现一次，示例行用参数名占位（LLM 自填），说明文字从简（作者拍板：uuid 长、吃 token）**
17. **spawn 与 communicate send 提交都建消费者跟踪终态**；notice 唤醒不建消费者（fire-and-forget）；send 无簿记时新建 SpawnRecord
18. **messenger in-process 化**：保留 run_wake 链路、notifier 直调，无 web_api.py、无 HTTP loopback（决策 11 全自洽）
19. **前端伴生（自动刷新）一期不做**（唤醒落盘后 WebUI 不刷新问题观察后再定）
20. **list_agent / list_session 分名**：列 agent 与列会话是两个语义，拆名防混淆（作者拍板）
