# MEMORY.md — qwenpaw-plugin-agent-tools

## 一句话定位

agent / 子 agent 会话管理插件：spawn 其他 agent 模板的持久子会话 + 续聊/等待/通知/上下文读取全链路 + 短 id（歧义拒绝+schema 可发现性）+ 簿记身份持久化 + 防误用三态 + schema 瘦身（8 工具，当前 v0.2.6，2026-11-17 首发，v0.2.0 起 SDK 形态）。

## 关键档案

- 设计蓝本：`DESIGN.md`（8 工具规格 + 拍板决策 + 宿主锚点）
- 环境事实与硬约束：见 `AGENTS.md`（勿回退清单）
- 架构与用法：见 `README.md`
- 版本细节：见 `CHANGELOG.md`（每批次完整记录）

## 当前状态（v0.2.6）

- **list_agent disabled 行去冗余（v0.2.6，2026-11-26，作者报 bug）**：`tools/list_agent.py` 删局部变量 `enabled`，enabled 列从 `disabled（disabled，不可作 agent_id）` 改为纯 `disabled`；「disabled 不可作 agent_id」说明保留在表头（文案逐字未变），DESIGN §3.7 同步；测试补三条断言（表头保留说明 / disabled 行整行精确 + 行内负向 / enabled 行整行精确）；三路无上下文交叉复审（代码 / 文档契约 / 运行时热装）无 HIGH，抽验修掉 DESIGN 括注与表头字面不符、测试缺正向断言两处；schema 快照 `--check` 一致（8 工具）；167 测试全绿
- **✅ 部署状态（2026-11-26）**：仓库 `plugin.json` = 0.2.6，已打包 `D:\Temp\qwenpaw-plugin-agent-tools-0.2.6.zip`（Python zipfile 按 `git ls-files -z` 原始相对路径写 46 条，⚠️ PowerShell `Compress-Archive -Path $files` 会把目录拍平到 zip 根、禁用）→ `qwenpaw plugin install <zip> --force` 返回 `active; enabled=True`；装机 `plugin.json` version=0.2.6、`tools/list_agent.py` 旧 `enabled` 变量已消失、表头完整；`qwenpaw plugin list` 显示 v0.2.6。**实机验收通过（下一回合）**：`agent_tools_list_agent` 输出 qa 行为 `QwenPaw_QA_Agent_0.2 ｜ QA Agent ｜ disabled ｜ Builtin Q&A helper ...`（行内冗余说明已消失、表头完整），印证「热装下一回合即生效、无需新会话」；安装当回合曾报 `generation 0 is no longer active`（工具仍绑旧 generation 的瞬态现象，非插件故障）；已 commit 并 push、发 release v0.2.6（作者授权）

- **✅ 部署状态（2026-11-20，历史）**：当时仓库 `plugin.json` = 0.2.4、已热装 v0.2.4 且本会话实机验收通过——zip 用 `git ls-files -z` 导无顶层目录（45 文件，`D:\Temp\qwenpaw-plugin-agent-tools-0.2.4.zip`）→ `qwenpaw plugin install --force`（`active; enabled=True`），装机目录 `C:\Users\<用户>\.qwenpaw\plugins\qwenpaw-plugin-agent-tools` 实测 version=0.2.4、`tools/spawn.py` template 引用=0；实机验证：spawn 参数已是 `agent_id`、传 `template=` 被 `Additional properties are not allowed` 硬拒、`list_agent` 表头 `[agent_id ｜ name ｜ enabled ｜ description]`、`list_session` 显示 `agent_id=default`、`wait` 超 9000 截断并全文落盘调用方 workspace `tool_results\agent-tools-75791b5f….txt`（60 行完整、尾部指引「从第 44 行开始续读」准确）；**热装后下一回合即生效、无需新会话**（工具每回合从注册表装配，不是会话级持久快照）；**未 commit（作者自行提交）**
- **多会话 wait/notice（v0.2.5，2026-11-20，作者拍板）**：`chat_id` 支持单个或多个（真 list / JSON 字符串数组均容错，`normalize_chat_id_list` 去重保序，参照 tool-wait_agent_task）；wait 多 id = 先整体预校验、任一不存在提前失败全都不等、全部有效则并发等待全部完成才返回（每块 `### 会话 {cid}`、独立截断落盘）；notice 多 id = 部分成功、每块 `chat_id=<完整id>` + 5 句文案（完成通知已注册/完成通知已取消/目标会话未注册完成通知/目标会话未找到，注册失败/目标会话未找到，取消失败，兜底/幂等/撤回不单写）；单会话仍走原逻辑（wait 逐字节不变、notice 用 5 句文案）；schema `chat_id` → anyOf string 或 array；全失败保持块格式（is_error=True）、取消先校验存在性且无待发条数、uuid 只在 chat_id 行出现一次、wait/notice 均单块异常隔离；167 测试全绿（+36）
- **复审遗留（已处置）**：① 历史 `tests/test_regression_fixes.py` 含 `C:\Users\<用户>\...`——作者判定不算泄露，不处置（working tree 已改 tempfile，仅为可移植性）；② `tests/test_multi_chat_id.py` 已入库（29 用例）；③ 0.2.4 + 0.2.5 已整批提交
- **✅ 部署状态（2026-11-20 更新）**：仓库 `plugin.json` = 0.2.5，**已热装 v0.2.5**（zip `D:\Temp\qwenpaw-plugin-agent-tools-0.2.5.zip` 45 文件 → `qwenpaw plugin install --force` active/enabled；装机实测 version=0.2.5、wait/notice 含 `normalize_chat_id_list`、wait 含 `return_exceptions`、schema `chat_id` anyOf string|array）→ **已 commit（作者授权；0.2.4+0.2.5 整批）**；热装下一回合即生效，本会话可直接实机验证多会话
- **截断全文落盘 + 上限分档（v0.2.4，2026-11-20，作者拍板）**：notice/wait/recv 正文超上限（分档：notice 4000 聚合多通知收小 / wait 9000 单目标放宽 / recv 9000 与 wait 同档——recv 此前无截断全量返回）→ 全文写入**读尾部的那个会话**的 workspace `tool_results` 目录（复用宿主 `save_text_output`，命名 `agent-tools-{uuid}.txt`，子目录名读 agent.json `tool_results_cache` 配置与宿主对齐），尾部改文件指引 `<<<TRUNCATED>>>正文已截断（上限 {limit} 字符）。全文：{path}（共 N 行），从第 {M} 行开始续读（包含被截断的那行）`（不点名工具/参数）——原因：旧 wait 截断只盖 `…` 无恢复指引、notice 旧指引指向 recv（recv 加限制后会失效），故统一改落盘文件指针；notice 通知头改三行 `[Agent会话通知]`/`agent_id={target_agent_id}`/`chat_id=...`（agent 大写去空格、template→agent_id 取真实 agent id 弃用 template_label、空格改换行）；用户隔离检查：宿主 tool_results 是 workspace 维度（本机按 agent 隔离→用户隔离），wait/recv 侧=调用方会话、notice 侧=唤醒方会话（`wake_agent_id`），不拿错；落盘失败降级纯截断提示；utils 新增 `get_agent_workspace_dir`/`get_tool_results_dir`/`save_truncated_body`；131 绿（+9：5 单元 + 4 集成全链路）
- **命名统一（v0.2.4，作者拍板）**：list_agent 列名 `id`→`agent_id`、spawn 参数 `template`→`agent_id`（schemas.py 属性名 + 函数签名 + docstring/报错/提示 + list_session 显示 `template=`→`agent_id=` 全同步，tools_schema.json 快照已重生成）；内部 `SpawnRecord.template` 字段**已移除**、统一用 `agent_id`（持久化写 `agent_id` 键、读回落 `template` 兼容旧会话）；spawn 内部父会话变量 `agent_id`→`parent_agent_id`、`template_id`→`target_agent_id` 避撞名；顺带移除死字段 `NoticeRecord.template_label`（通知头改版后只写不读）；131 绿
- **notice 气泡 busy 重试刷屏修复（v0.2.3，2026-11-18，作者报 bug）**：wait + notice 并用、父会话继续干活期间气泡反复弹（每 3s 一次直到停手）——根因 `_deliver` 双投递顺序颠倒（照抄 process-tools 时把「唤醒→气泡」写成「气泡→唤醒」，busy 重试每次都 append 气泡）；修复对齐 process-tools「唤醒成功才发气泡」语义：busy 不发气泡、成功才发、真失败仍尽力发（文本可见）；气泡逻辑抽 `_push_bubble()`；测试 122 全绿（+2 回归）；**真机验收通过（spawn 60s 等待任务 + notice + wait 并用，wait 返回后持续干活 31s 覆盖 ~10 次 busy 重试气泡全程 0 条，停手后恰好 1 条气泡 + 通知 turn 送达，console_push_store 全程实测）**
- **context_export 相对路径修复（v0.2.2，2026-11-18）**：`Path(output_path)` 旧挂宿主进程 cwd（实测落 miniconda Scripts/，文件「消失」）→ utils 新增 `get_workspace_dir()`（内核 `qwenpaw.config.context.get_tool_base_dir` 三级回落「项目目录→agent 工作区→WORKING_DIR」，无 qwenpaw 回落空串；file-tools v1.8.3 / process-tools v0.6.8 同款 wrapper）+ `resolve_workspace_path()`（相对挂基准、绝对/`~` 照用，纯字符串运算）；成功消息回显绝对路径；schema 描述同步（schemas.py 唯一来源 → show_tool_schema.py 快照重生成）；测试 120 全绿（+3：相对挂基准落盘/消息含绝对路径集成 + resolve 两单元）；宿主内实测（internal-debug REPL）：回合外无 contextvar 回落 WORKING_DIR 而非宿主 cwd，回合内与 file-tools 27 工具同语义；**真机验收通过**（另一会话实测，zip 热装 + commit 同日完成）
- 测试 120 全绿（纯 mock + 真单例集成 + TestHost，无 qwenpaw 运行时依赖）；已热装服役；**自然验收闭环（2026-11-18）**——spawn→wait 正文回收 / notice 全链路唤醒 / B2 注入路径实战 / B3 托管 task name 前缀实锤 / 分裂窗口收敛（8/8 SDK descriptor 绑 gen4）
- **SDK 深化三件套（v0.2.1，2026-11-18）**：① A1 schema 固化——8 工具 description/input_schema 从 agentscope 运行时推断切换 schemas.py 固化常量（v0.2.0 推断结果原样，盲测文案零漂移），**运行时零 agentscope 私有依赖**；**schemas.py = 唯一正式 schema 来源（LLM 可见）**，docstring 降级代码注释**不做双向锚定**（作者拍板：双向对齐麻烦），短 id 锁定对象下迁至常量（test_persistence.py），show_tool_schema.py 改常量导出器（--check 模式）+ 快照升级含 additionalProperties；② B2 ToolContext 注入——_wrap set/reset `utils._CALL_CTX`，get_current_context 优先读（agent_id=workspace_id 实锤同源），工具函数零改动；⚠️ ToolContext 无 user_id，注入路径 user_id 仍内核 API 补齐（公共面缺口）；③ B3 task 托管——manager.spawn_managed（resources.task 优先/裸 create_task 回落），两处 flusher 迁托管（Registration 自动回收无累积），_shutdown_tasks 保留兜底（stop_task done→return 幂等实锤），消费者/监视协程不动
- **schema 瘦身（v0.1.11，2026-11-18）**：8 工具 docstring 按作者三诉求（省 token/易懂/不写内部原理）优化，LLM 可见 schema 1388→1095 字符（-21%）；删内部原理（WebUI/chats.json meta 恢复/版本号）+ 去术语（Scroll 驱逐→被压缩历史）+ 消「见上」断链（idx 并入 start 参数）+ notice 投递时机并入第一段；**盲测驱动**：3 个无上下文子 agent 纸面问答零试错全对，并抓回 5 微调（count 上限 50 数值/spawn 前台直接返回正文/notice 每回合触发/output_path 相对工作区/删「全量」歧义）。**意外发现**：知识库「\n\n 截断规则」已过时（agentscope 改 short+long 全拼接，docstring 全段可见），已勘误
- **schema 短 id 可发现性（v0.1.9）**：5 个收 chat_id 的工具参数描述 + spawn 返回提示（_WAIT_HINT 第一现场）均注明「完整 UUID 或其第一段 8 位 hex 短 id」；回归锁定用例防丢失。模糊语言测试通过：无上下文子 agent 拿到 8 位 id（措辞无暗示）→ [tool_call] 实锤原样传参 → 「拿到什么传什么」是 LLM 自然直觉
- **簿记持久化（v0.1.8 混合方案，真机验收通过）**：身份账挂 `chat.meta["agent_tools"]`（template/depth/parent 三元组/spawned_at，spawn 时一次写入，跟随宿主 chats.json 落盘）；resolve 短 id 无匹配时自动扫各 agent workspace 的 chats.json 回填（restored=True 轻量记录，created_at 从 spawned_at 还原，parent 链恢复→防误用祖先链重启后可用）；回填即缓存。易变账（turns/queues/active/notices）留内存不持久化。**meta 上线前的存量 spawn 会话无身份标牌不可恢复**；v0.1.8 起 spawn 的新会话均有
- **热装分裂是瞬态窗口会收敛**（v0.1.8 验收实测）：热装后 gc 多实例分裂，但隔一轮对话后 wrapper 已指新代码（spawn 新增调用点生效）；收敛前簿记操作不可信，**验收以文件级事实为准**（chats.json 落盘不受分裂影响）。**v0.1.10 补充**：分裂窗口内第三方 agent 的工具链同样未收敛——防误用拦截真机复测在窗口内放行（旧代码），收敛后才拦截生效——涉及新行为的真机验收必须等收敛
- **防误用三态全景（v0.1.10 真机闭环）**：① qa 回合（spawn/send 调起）send 祖先 → **拦截**（内容走正文）；② notice 触发回合（含发起者收到子辈完成通知、消息投递唤醒）→ **放行**（不注册 active，零代码例外）；③ send 非祖先（兄弟/第三方）→ 放行。子 agent notice 回合反向 send 主会话的完整链路实测通过（第三方 agent spawn 孙辈+挂 notice→通知唤醒→自主 send 送达）
- **主键规则**（三层解析，resolve_chat_reference）：完整 UUID 直接命中 → 短 id（严格 8 位 hex = 长 id 第一段）簿记内前缀匹配（命中 1 解析 / ≥2 显式歧义报错 / 0 无匹配）→ session_id 形态拒绝 + 自纠提示。**不支持**官方 task_id；session_id 一步自愈已移除（作者拍板：语义混乱）
- **身份前缀**（v0.1.2 改版定稿）：`[系统提示：本回合请求来自 Agent「demo-agent」（agent_id: default，chat_id: <UUID>）。你的回复对方会读取到，本回合无需再向对方发消息；之后要主动联系对方，就用这个 chat_id 调 agent_tools_communicate 发送。]`——UUID 只出现一次（「这个 chat_id」就近指代）；spawn（直接返回）与 send 排队（wait/recv 拉取）双场景语义统一为「对方会读取到」；反查 resolve_parent_chat_id 宿主 resolver，失败 → 异常防御 WARNING + 短文案（发起者三类全在 chats 表，正常恒成功）
- **并发守护**：SessionQueue.in_flight（「已取未提」窗口 FIFO 保护）；ActiveEntry.turn 身份守卫（remove_active_if_turn 防误删后续回合条目）；stop 完成后复查队列非空 → 等旧 flusher 退出 + 重启（防孤立滞留）；直提被抢占 → 入队必达；flusher give_up 先 drop 后气泡；消费者取消 task.cancelling() 守卫
- **notice**：重复注册检查含 completion_sent（投递后陈旧记录不拦截再注册）；消费者异常分支也触发（三终态全触发）

## 版本沿革（首发 2026-11-17）

| 版本 | 内容 |
|---|---|
| 0.1.0 | 首发实现（DESIGN v2.2 → 8 工具 + 三轮热装修复 + 实测全链路通过） |
| 0.1.1 | 三路复审修复（export 变量复用 HIGH / 死队列 HIGH / give_up 顺序 / cancelling 守卫 / COUNT 轻量化）+ meta category agents |
| 0.1.2 | 身份前缀改版（UUID×1 指代 + 回访指引 + 双场景统一）+ resolve_parent_chat_id |
| 0.1.3 | 前缀反查字段名 bug 修正（chat.id）+ 降级重定位异常防御（作者质疑驱动，宿主内真跑验证） |
| 0.1.4 | 二轮三路复审核实修复（动态复现实验实锤）：B-② stop 窗口孤立滞留 / B-③ remove_active 守卫 / B-① in_flight / A-M1 notice 再注册 / A-M2 被抢占入队必达 |
| 0.1.5 | 主键收紧：移除 session_id 一步自愈，只认 chat_id |
| 0.1.6 | 短 id 支持：严格第一段 8 位 hex + 簿记前缀匹配 + 歧义显式拒绝（git 模式） |
| 0.1.7 | 生效验证实战 hotfix：resolve_chat_id_input 的 all_records 漏括号（method 当 property 迭代→全部校验工具真机必炸，mock 集成盲区）修复 + 集成测试补盲（91 绿）+ 热装模块分裂实锤（详见 CHANGELOG 0.1.7） |
| 0.1.8 | 簿记身份持久化（作者拍板混合方案）：spawn 身份字段挂宿主 chat.meta["agent_tools"]（chats.json 落盘）+ resolve miss 时从 chats.json 回填（restored=True，parent 链恢复→防误用重启后可用）+ 回填即缓存；易变账（turns/queues）留内存。真机实锤：写入/恢复/短 id 全链路通（详见 CHANGELOG 0.1.8） |
| 0.1.9 | schema 短 id 可发现性：5 工具参数描述 + spawn 返回提示统一补「完整 UUID 或第一段 8 位 hex 短 id」（此前零处提及、唯一提示在报错文案）；101 绿 +schema 回归锁定用例。**模糊语言测试通过**：无上下文第三方 agent 拿到 8 位 id（措辞无短 id 暗示）→ [tool_call] 实锤原样传参 `communicate(chat_id="14c9e0c7")` → miss 触发 meta 恢复 → 命中——「拿到什么传什么」是 LLM 自然行为，恢复链路在真实第三方调用下全串联工作 |
| 0.1.10 | 防误用祖先链 gap 修复（通知回合反向 send 测试发现）：check_misuse 增 target_agent_id 显式参数（locate 直传，主会话不在簿记反查空串的 fail-open 缺口堵死）；notice 回合放行为零代码天然例外；三态全景真机闭环；104 绿（详见 CHANGELOG 0.1.10） |
| 0.1.11 | schema 瘦身（2026-11-18，作者拍板）：8 工具 docstring 1388→1095 字符（-21%）——删内部原理/去 Scroll 术语/消见上断链；盲测驱动（3 无上下文子 agent 零试错全对 + 抓回 5 微调）；意外发现知识库「\n\n 截断规则」已过时（agentscope 改 short+long 全拼接）并勘误；104 绿（详见 CHANGELOG 0.1.11） |
| 0.2.0 | v2.3.0 SDK 迁移试点（2026-11-18，作者拍板，分支 sdk-migration）：注册面换皮 PluginContext.register_tool + ToolSpec；schema 动态推断（agentscope 推断函数）v0.1.11 文案零漂移 + additionalProperties=False；make_error/make_success → SDK ToolResult（字段兼容 104 测试透明）；生命周期实锤——资源创建必须 on_activate（register 碰 resources 报 ResourceError）；TestHost 集成测试 +7 + 宿主双副作用实锤（load 移 sys.path 条目 / close 清同目录扁平模块 → 快照恢复修复）；体检基线 48 findings（38 private-import = 换皮不换骨预期）；111 绿（详见 CHANGELOG 0.2.0） |
| 0.2.1 | SDK 深化三件套（2026-11-18，作者拍板）：A1 schema 固化 schemas.py（唯一正式 schema 来源，运行时零 agentscope 依赖；docstring 解耦不做双向锚定——双向锁定用例即建即撤，短 id 锁定下迁至常量）+ B2 ToolContext 注入 _CALL_CTX（agent_id/session_id 走公共面，user_id 公共面缺口保留内核）+ B3 flusher 迁 resources.task 托管（spawn_managed 注入优先/回落，Registration 自动回收）；B1 流式明确不做；四路对抗复审+动态测试（零 HIGH，5 处修复：监视协程 on_turn_finished 补防护/短 id 参数级锁/wait timeout null/异常传播契约用例/reset 注释），117 绿（详见 CHANGELOG 0.2.1） |
| 0.2.2 | context_export 相对路径修复（2026-11-18，作者直觉测试抓出）：旧 `Path(output_path)` 挂宿主进程 cwd（实测落 miniconda Scripts/ 文件「消失」+ 成功消息回显相对路径误导）→ utils 新增 `get_workspace_dir()`（内核 `get_tool_base_dir` 三级回落）+ `resolve_workspace_path()`（相对挂基准/绝对与 `~` 照用，纯字符串运算）；成功消息回显绝对路径；schemas.py 描述同步 + 快照重生成；120 绿（+3 用例）（详见 CHANGELOG 0.2.2） |
| 0.2.3 | notice 气泡 busy 重试刷屏修复（2026-11-18，作者报 bug）：wait+notice 并用、父会话干活期间气泡每 3s 反复弹——根因 `_deliver` 双投递顺序颠倒（照抄 process-tools 时「唤醒→气泡」写成「气泡→唤醒」，busy 重试每次都 append）；修复对齐「唤醒成功才发气泡」：busy 不发/成功才发/真失败仍尽力发；抽 `_push_bubble()`；122 绿（+2 回归）（详见 CHANGELOG 0.2.3） |
| 0.2.4 | 截断全文落盘 + 上限分档（2026-11-20，作者拍板）：notice/wait/recv 正文超上限（notice 4000 聚合收小 / wait 9000 单目标 / recv 9000 同档——recv 此前无截断）→ 全文写读尾部会话的 workspace `tool_results`（宿主 `save_text_output`，`agent-tools-{uuid}.txt`，子目录名读 agent.json `tool_results_cache`）+ 尾部改文件指引（路径+总行数+从第 M 行续读，不点名工具，落盘失败降级纯提示）——旧 wait 截断只盖 `…` 无指引、notice 旧指针指向 recv（recv 加限制后会失效），故统一改落盘指针；用户隔离检查（宿主 tool_results 是 workspace 维度，wait/recv 侧=调用方/notice 侧=唤醒方会话）；utils 新增 3 函数；131 绿（+9）（详见 CHANGELOG 0.2.4） |
| 0.2.5 | wait/notice 多会话（2026-11-20，作者拍板）：chat_id 支持单个/多个（真实 list / JSON 字符串数组容错，normalize_chat_id_list 去重保序、拒非字符串项）；wait 多 id = 任一不存在提前失败全不等、全部有效并发等待全部完成才返回（单块异常隔离）；notice 多 id = 部分成功（每块 `chat_id=<完整id>` + 5 句文案）、全无效整单 error、取消先校验存在性（不存在→取消失败）、多块异常隔离；schema chat_id anyOf string 或 array；167 绿（+36）（详见 CHANGELOG 0.2.5） |
| 0.2.6 | list_agent disabled 行去冗余（2026-11-26，作者报 bug）：enabled 列从 `disabled（disabled，不可作 agent_id）` 改纯 `disabled`，说明只在表头；DESIGN §3.7 + 测试断言同步（表头正向 / disabled 整行精确+负向 / enabled 整行精确）；三路无上下文复审无 HIGH、抽验修两处；schema 快照一致、167 绿；已热装 v0.2.6、下一回合实机验收通过（详见 CHANGELOG 0.2.6） |


## 设计决策要点（拍板记录）

- **短 id**：= 长 chat_id 第一段，严格 8 位 hex（SHORT_ID_RE）；匹配范围 = 插件簿记（管理对象即簿记会话，主会话/官方 task session 不混入）；碰撞数学：uuid4 前 8 字符均匀 32 bit（版本/变体位不在前 8 字符），n=269 撞率 0.0008%、7.7 万条 50%——**概率低不是放行理由，歧义显式拒绝才是**（禁止静默取第一个）
- **前缀回访指引**：子 agent 可主动找发起者（设计初衷）——chat_id 反查 + 「这个 chat_id」就近指代；agent_id 保留（作者拍板）；「的子agent会话」标注删除
- **驱逐区重叠 = 预期语义**（作者拍板）：hist.conversation_history 是全量日志，与可见区重叠符合预期无需除重——docstring 已注记防复审再误报
- **flusher give_up 精确语义**：仅「空闲→投递→失败→放回队首」计次；running/被抢占不计；成功清零；10 次 → drop + sticky 气泡（队首消息发起者）+ ERROR
- **通知分桶**：按唤醒目标四元组独立 flusher，互不阻塞；同父 3s 聚合
- **task_id 弃用**（决策 8）：官方 task session 与插件簿记平行，不打通（如需混用再评估 resolver 兜底方案）

## 经验教训（本插件沉淀）

- **复审结论必须核实再修**（作者要求）：A 路代码逐条核对 + B 路时序发现写 **asyncio 动态复现实验**实锤——14 项 13 属实 1 半误报（B-② 后半句「S 被静默丢弃」是幻觉，实验证伪：滞留消息反而被后续 send 救回）
- **纯函数测试 ≠ 集成正确性（v0.1.7 实锤）**：短 id 用例全过但真机必炸——用例直接调纯函数 `resolve_chat_reference`（手动传 ids），包装函数 `resolve_chat_id_input`（utils→manager 单例接线，`all_records` 漏括号炸点所在）零集成覆盖。教训：**包装/接线层必须有真依赖（真单例）集成用例**，mock 掉的恰是容易写错的那层
- **热装模块分裂（§7「热装清簿记」深层机制）**：热装 reload 新建模块对象替换 sys.modules（非原地），工具 wrapper 绑定的旧模块实例被引用存活——顶层 import 绑旧 manager（spawn 写旧单例）vs 函数体内动态 import 命中新模块（resolve 读新单例）→ **簿记写旧读新分裂**。热装后本会话所有簿记操作不可信；新会话统一消失。会话内救急 = REPL 把旧单例 spawn_records 搬进新单例
- **防御分支先验证真实场景**：别发明不存在的降级路径（v0.1.3 教训——「降级保障」实为死代码且字段名 bug 让它反而全触发）；getattr 链取字段前核对模型真实字段名（ChatSpec.id 非 chat_id）
- **为计数覆盖复用变量**必须检查该变量的所有下游消费点（R-A #1 教训：export 变量复用导致驱逐内容泄漏进导出文件）
- **宿主 API 签名核对**价值极高，但「数据形态漂移」（JSON 字符串列 / row_cap 截断 / 枚举缺值）是签名核对的盲区——复审任务书单列「数据形态」维度
- **动态实验调试坑**：asyncio.create_task 不能在同步上下文（整体包 async 场景）；flusher 跨两个 asyncio.run 被丢弃（合并场景）；编排型 mock 要自定义 attach 序列；qwenpaw 可导入环境 resolver 会真跑（cwd 无配置 → 判僵尸丢队列）必须 patch；print 管道缓冲 → faulthandler.dump_traceback_later 定位挂死
- **monkeypatch 同测试内多段 patch 栈序污染** → 拆独立测试；返回气泡文案 ≠ 文件内容（断言读文件）

## 遗留

- A-M4 权衡：驱逐历史用 MemorySpace 直查（工厂返回 LLM 文本形态不便结构化渲染）——设计 §2.1 字面偏差，作者拍板记录在 AGENTS.md
- M3：session_reader media 分支为死代码——保留作防御，暂不重构（L1 选项：实现 [media] 占位标注或删分支，未拍板）
- 前端伴生（notice 唤醒后 WebUI 自动刷新）一期不做（设计决策 19），实际使用后观察再定
- list_session 是否显示短 id 列——可选优化，未拍板
