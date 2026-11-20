# 更新日志


## 0.2.5（2026-11-20）

wait / notice 支持多会话：`chat_id` 可传单个或多个，一次调用同时等待 / 注册多个子会话。

- **chat_id 列表归一**（`tools/common.py: normalize_chat_id_list`）：兼容真 list / 单个 str / LLM 常误传的 JSON 字符串数组（`'["a","b"]'` → `json.loads`，参照 tool-wait_agent_task 容错写法）；逐项 strip 去引号、去重保序；空项/空列表/非 str 非 list 报错
- **wait 多会话（作者拍板）**：任一 id 不存在/无效 → **提前失败、不对任何会话发起等待**，逐条返回「chat_id 'xxx' 不存在，请检查拼写」；全部有效 → 并发 `asyncio.gather(return_exceptions=True)`（单块异常隔离为该块错误文本，不整单抛错），**全部完成（或各自超时）后才返回**，每块前缀 `### 会话 {chat_id}`、每块正文独立截断落盘
- **notice 文案极简化（作者拍板）**：固定 5 句——`完成通知已注册` / `完成通知已取消` / `目标会话未注册完成通知`（已终态或空闲无回合）/ `目标会话未找到，注册失败`（注册时找不到/id 无效/workspace 未运行）/ `目标会话未找到，取消失败`（取消时先校验存在性，找不到）；监视兜底、幂等、撤回等不单独措辞；**多会话每块** `chat_id=<解析后完整 id>` + 换行 + 文案，块间空行，uuid 只在 chat_id 行出现一次；部分成功不整单失败，全部失败仍保持块格式（is_error=True）；多块异常隔离（单块异常转该块错误文本，不整单抛错）
- **单会话**：len==1 仍走原逻辑；wait 单会话输出逐字节不变；notice 单会话文案按作者要求精简（消息内不再重复 uuid、取消文案去掉待发条数）
- **schema**：wait / notice 的 `chat_id` → `anyOf: [string, array<string>]`；description 补多会话说明；plugin.json meta.tools 描述同步
- **版本**：0.2.4 → 0.2.5（0.2.4 已热装生效，新特性单开）
- 测试：pytest **167 全绿**（131 + 36：normalize 7 + wait 多会话 4 + 单会话格式不变 1 + notice 多会话 2 + 复审修复 3 + 边角 4 + 取消文案 1 + 极简文案 5 + 取消不存在 2 + 复审补齐 7）


## 0.2.4（2026-11-20）

notice/wait/recv 正文截断 → 全文落盘 + 文件指引（作者拍板：模仿 QwenPaw 大 tool_result 机制，让 wait 截断终于有恢复指引、并保证 recv 加限制后旧 recv 指针仍成立）+ 上限分档（notice 4000 / wait 9000 / recv 9000）。

- **背景**：此前 wait 截断只盖 `…` 省略号、无任何恢复指引，超上限部分不可达；notice 截断尾部指向 `communicate(recv)`（recv 当时无上限 → 承诺成立），但本批要给 recv 也加 9000 上限，旧 recv 指针会变成空头承诺——故统一改为落盘文件指引：wait 补上指针、recv 指针保持有效
- **上限分档（作者拍板）**：`NOTIFY_BODY_LIMIT` = 4000（通知可能多条聚合同气泡，收小）/ `WAIT_BODY_LIMIT` = 9000（单目标等待，可放宽）/ `RECV_BODY_LIMIT` = 9000（recv 与 wait 同档；recv 此前无截断、全量返回）
- **机制（QwenPaw `ToolResultPruningMiddleware`/`ToolResultPruner` 同款）**：正文超上限时，全文经宿主 `save_text_output`（`qwenpaw.agents.tools.utils:112`，调用时轻量 import）写入**读尾部的那个会话**的 workspace `tool_results` 目录，文件命名 `agent-tools-{uuid}.txt`（与宿主自有 `tool-result-{uuid}.txt` 可区分）；子目录名读 agent.json `running.light_context_config.tool_result_pruning_config.tool_results_cache` 配置与宿主对齐（本机值 `tool_results`），失败回落默认；recv 落盘的是完整输出文本（含标题行与分节头）
- **用户隔离检查（作者要求）**：宿主 tool_results 目录是 workspace 维度（`{workspace_dir}/tool_results`，`runtime/builder.py:1291`），本机部署 workspace 按 agent 隔离 → 用户隔离；文件落在读尾部的会话的 workspace——wait/recv 侧 = 调用方会话（`get_current_context` → agent_id → `profile.workspace_dir`），notice 侧 = 唤醒方会话（`NoticeRecord.wake_agent_id` → `profile.workspace_dir`），不拿错
- **尾部文案**（标记 + 两换行 + 一行）：`<<<TRUNCATED>>>正文已截断（上限 {limit} 字符）。全文：{path}（共 N 行），从第 {M} 行开始续读（包含被截断的那行）`——M = 切点所在行号（该行含被截断的半行，从该行起读会完整重取该行）；不点名工具/参数（作者拍板：只说从哪行续读）；落盘失败降级 `<<<TRUNCATED>>>正文已截断（上限 {limit} 字符）`
- **notice 通知头改版（作者拍板）**：`[agent 会话通知] chat_id=xxx（template=label）` → 三行 `[Agent会话通知]` / `agent_id={target_agent_id}` / `chat_id={target_chat_id}`——agent 开头大写去空格、字段名 template→agent_id（取 `NoticeRecord.target_agent_id` 真实 agent id，弃用展示用 `template_label`）、中间空格改换行
- **命名统一（作者拍板）**：list_agent 列名 `id`→`agent_id`、spawn 参数 `template`→`agent_id`（schemas.py 属性名 + 函数签名 + docstring/报错/提示 + list_session 显示 `template=`→`agent_id=` 全同步）；内部 `SpawnRecord.template` 字段**已移除**、统一用 `agent_id`（持久化写入改 `agent_id` 键、读取回落 `template` 键兼容 v0.1.8 起落盘的旧会话）；spawn 内部父会话变量 `agent_id`→`parent_agent_id`、`template_id`→`target_agent_id` 避撞名；顺带移除死字段 `NoticeRecord.template_label`（通知头改版后只写不读，删字段定义 + notice.py 赋值两处）
- **utils 新增**：`get_agent_workspace_dir`（load_config().agents.profiles）/ `get_tool_results_dir`（agent.json 配置 + 回落）/ `save_truncated_body`（宿主 save_text_output + 异常兜底 None）；`truncate_tail` 扩展 file_path/start_line/total_lines 参数
- **已知局限**：每次截断生成新 uuid 文件（wait 超时 / recv 重复调用会重复落盘）；落盘目录与宿主共用 `tool_results`，随宿主 `offload_retention_days`（本机 5 天）经 `cleanup_expired` 按 mtime 清理（`offloader.py` glob `*.txt`），故尾部文件短期可用、超期路径 404
- 测试：pytest **131 全绿**（122 + 9：truncate_tail 带/不带指针格式、get_tool_results_dir 配置读/回落、save_truncated_body 真落盘 + 空输入、wait/notice/recv 截断全链路集成——文件内容一致 + 尾部指针 + recv 短正文原样透传）


## 0.2.3（2026-11-18）

notice 气泡 busy 重试刷屏修复（作者报 bug：wait + notice 并用、父会话继续干活期间气泡反复弹）。

- **根因（notifier._deliver 双投递顺序颠倒）**：照抄 process-tools 时把「唤醒 → 气泡」写成了「气泡 → 唤醒」——console 路径**先无条件 `console_push_store.append` 再 `_submit_turn`**，父会话忙（is_new_run=False）时气泡已发出、批次放回队首，flusher 每 3s 重试一次就 append 一次 → 每 3 秒弹一次气泡，直到父会话停手唤醒成功才停
- **修复（对齐 process-tools「唤醒成功才发气泡」语义）**：`_deliver` console 路径改为先 `_submit_turn`：busy（is_new_run=False）→ 返回 False 忙等重试、**不发气泡**；成功 → 才 append 聚合气泡 + detach；提交异常（真失败）→ 尽力发气泡（通知文本至少可见）+ 不重试。气泡逻辑抽 `_push_bubble()` 辅助（process-tools 同款），模块 docstring 双投递语义同步
- 测试：pytest **122 全绿**（120 + 2 回归：busy 重试期间气泡零重复 + 最终成功仅 1 条聚合气泡；唤醒异常真失败仍发 1 条且不重试——test_regression_fixes）


## 0.2.2（2026-11-18）

context_export 导出路径修复（作者另一会话实测发现）。

- **相对路径挂宿主 cwd bug（根因 tools/context_export.py:136）**：旧版 `Path(output_path)` 相对路径挂**宿主进程 cwd**（实测落 `D:\Software\miniconda3\envs\qwenpaw\Scripts\`），而 schema 写「相对工作区」→ 文档与实现不符，导出文件「消失」。修复：utils 新增 `get_workspace_dir()`（try-import 内核 `qwenpaw.config.context.get_tool_base_dir`，三级回落「项目目录→agent 工作区→WORKING_DIR」，无 qwenpaw 环境回落空串；file-tools v1.8.3 / process-tools v0.6.8 同款 wrapper）+ `resolve_workspace_path()`（相对挂基准、绝对/`~` 照用；normpath+abspath 纯字符串运算，无网络盘 resolve 阻塞风险）
- **成功消息回显绝对路径**：`已导出：…（绝对路径；可见区 N 轮…）`——调用方不再需要拼路径找文件
- **文档面**：schemas.py（唯一 schema 来源）output_path description 改「相对路径挂工具基准目录（项目目录→agent 工作区）」；tools_schema.json 快照重生成（--check 实测一致）；docstring 与 OUTPUT_PATH_ERROR 提示同步
- 测试：pytest **120 全绿**（117 + 3：export 相对路径挂基准目录落盘 + 成功消息含绝对路径（集成，test_regression_fixes）；resolve_workspace_path 相对挂基准/绝对照用（单元，test_utils））；宿主进程内实测（internal-debug REPL）：回合外无 contextvar 时 get_tool_base_dir 回落 WORKING_DIR（`~/.qwenpaw`）而非宿主 cwd——极端路径也不再「消失」，回合内走 contextvar 与 file-tools 27 工具同语义
- 真机验收：另一会话实测通过（作者确认修好）——相对路径落基准目录、成功消息回显绝对路径（热装不刷新旧会话，工具绑定是启动时快照）




## 0.2.1（2026-11-18）

三项 SDK 深化（作者拍板：A1 schema 手写 / B2 ToolContext 注入 / B3 task 托管；B1 流式 wait 明确不做）——全对内重构，对外 schema/行为零漂移。

- **A1 schema 固化（schemas.py）**：8 工具 description + input_schema 从「register 时 agentscope 推断」切换为固化常量（v0.2.0 运行时推断结果原样固化，v0.1.11 盲测文案零漂移，additionalProperties=False 含内）——**运行时零 agentscope 私有依赖**，schema 不再「猜」。**schemas.py = 唯一正式 schema 来源**（LLM 可见，中频修改，改 schema 只改它）；docstring / Args 注解降级为代码注释（低频，不参与 schema 生成，**不做双向锚定**——作者拍板：双向对齐麻烦）；v0.1.9 短 id 提示锁定对象随之下迁（test_persistence.py 锁常量不再锁 docstring）；`show_tool_schema.py` 改造为常量导出器（--check 比对模式），tools_schema.json 快照升级为含 additionalProperties 的完整 LLM 可见形态
- **B2 ToolContext 注入**：`plugin._wrap` 每次调用把 SDK ToolContext set 进 `utils._CALL_CTX` contextvar（finally reset）→ `get_current_context()` 优先读注入（agent_id=workspace_id、session_id 与内核 contextvar 同源——`_workspace.py` peek_current_agent_id 实锤），无注入（TestHost/直调）回落内核路径；**工具函数零改动**，13 处既有 mock 不受影响。⚠️ 缺口注记：ToolContext 无 user_id 字段，注入路径下 user_id 仍从内核 API 补齐（notice 唤醒四元组/spawn parent 簿记消费，公共面无对应物）
- **B3 task 托管**：`manager.spawn_managed()`（resources 注入优先走 `ResourceScope.task`——卸载自动清理 + 异常兜底 + 完成后 Registration 自动回收无累积；未注入回落裸 create_task）——notifier/communicate 两处 flusher 创建点迁移；`_activate` 注入 `manager.resources`；`_shutdown_tasks` 手工清单保留兜底（custody.stop_task done→return 幂等实锤，双重清理安全）；消费者/监视协程不动（复审修复热点区）
- 文档与清单（同日）：`qwenpaw_version.max` 2.9.0→2.99.0（防官方恢复上界检查后被窄区间拒载）；README 重写——使用者视角（官方工具对比表 / 8 工具一句话用途 / 4 个场景案例 / 短 id 与安装），删架构段与内部机制速览（内部原理归 DESIGN.md 与 CHANGELOG）
- 测试：pytest **117 全绿**（112 + B2 三用例 + B3 两用例 + B3 异常传播契约用例，-1 test_schemas 双向锁定用例（作者拍板解耦）+ 短 id 锁定迁移至常量参数级检查；conftest reset 增加 resources 归位防线）；体检 50 findings = 40 private-import（运行时 27 全为 SDK 无公共对应物保留项 + tests 13 白盒访问）+ 10 unmanaged-resource，较 v0.2.0 基线 +2（utils 注入路径 user_id 补齐 import + test_utils 白盒访问 _CALL_CTX），性质不变
- **四路对抗复审 + 动态测试（同日，作者拍板「全面复审确保无 bug」）**：A 路 schema 面（AST 键集合严格相等/快照零漂移/--check 实测）+ B 路 B2 注入链路（21 项动态断言：异常/取消/并发隔离/嵌套 token/热重载全部 PASS；workspace_id==agent_id 宿主源码实锤 + 10 消费点等价逐一核对）+ C 路 B3 托管生命周期（4 实验实锤双重清理无竞态/stop_task 三态幂等/异常传播+coro 关闭）+ D 路边界测试（46 项 schema 校验矩阵 + contextvar 并发 + 双重清理并发 + user_id 四路兜底 + --check 漂移检测三类篡改全 exit 1）。**零 HIGH**；复审修复 5 处：① MEDIUM 监视协程路径 on_turn_finished 补 try/except（B3 引入的卸载窗口抛出路径，与 submit.py 消费者防护对齐——C 路实验 B3b 实锤 watch task 带未处理异常死亡）② MEDIUM 短 id 锁定 blob → chat_id 参数级检查（消假绿路径）③ wait timeout anyOf 补 null（与 spawn 对齐，LLM 传 null 不再被 schema 拒——handler float(None) TypeError 本就回落 900）+ description 注明 null=用默认 ④ spawn_managed 异常传播契约用例（BrokenScope 锁定「原样抛出+无半初始化状态」）⑤ reset_manager 隐式契约注释。**不采纳注记**（记录在案）：communicate.action 无 enum（运行时拦截已清晰）、context_export output_path 软必填（既有设计与 communicate.text 同构）、flusher 正文终线兜底（内部已有宽 except，swap 段崩溃概率低，避免扩大改动面）、spawn_managed coro close 防御（宿主 resources.py:164-166 已实测 close，隐式契约注释代替）、B 路 LOW-1/2（理论边界/有界驻留）。四路一致结论：**可 commit**


## 0.2.0（2026-11-18）

v2.3.0 SDK 迁移（作者拍板试点）：工具注册面换皮为公共 SDK，内部实现零改动。

- **plugin.json**：新增 `"sdk": {"version": "1.0.0", "capabilities": ["tools"]}`；`qwenpaw_version.min` 2.2.0→2.3.0（SDK 仅 2.3.0+ 支持）
- **plugin.py 重写**：`register(context: PluginContext)` 以 `ToolSpec` 注册 8 工具（`context.register_tool`）；schema 与 description 在 register 时经 agentscope `_extract_input_schema`（直接返回 parameters 层，实测）+ `_extract_func_description`（docstring）动态推断——v0.1.11 盲测过的瘦身文案**零漂移**，不手写平行 JSON；仅补 `additionalProperties: False`（额外键在 jsonschema 校验层拦截，报 ToolInputError）
- **handler 适配**：SDK 以 `(ToolContext, arguments dict)` 调用——`_wrap` 解包回原签名 kwargs（`**dict(arguments)`），8 个工具函数本体**零改动**；sync/async handler 均兼容（list_agent/list_session 为同步 def）
- **结果封装**：utils `make_error`/`make_success` 从 agentscope ToolChunk 改为 SDK `ToolResult`（`ToolResult.text(text, is_error=)`）——字段兼容（`content[0].text` 同名），104 个既有测试断言全透明
- **生命周期**：资源创建必须进 `context.on_activate(callback)`（register 里碰 resources 会 ResourceError「Create resources in on_activate(), not register()」实测）——单例预热 + `context.resources.defer(_shutdown_tasks)`（SDK 无 startup/shutdown 生命周期相位，defer 在卸载时逆序执行，设计 §7 语义不变）
- **TestHost 集成测试**（tests/test_sdk_host.py，+7）：真实加载链路——load→8 工具注册→call_tool（list_agent/list_session）→错误语义（spawn 空 task）→额外键 ToolInputError→sync handler→reload→unload quiescent
- **TestHost 双副作用实锤（测试基座修复）**：
  1. `host.load()` 后插件目录被移出 sys.path（loader 导入清理连坐同值条目）
  2. host close 后 sys.modules 中从插件目录导入的扁平模块被清除——本插件目录即项目根，tests 以项目根导入的 manager/notifier/submit/tools.* **全部连坐** → 后续测试函数内 import 触发重新导入 → 新模块对象 → monkeypatch 打在旧对象上失效 → test_spawn/test_submit/test_utils 顺序性污染（7 failed）
  修复：`_run` 驱动层 sys.modules 快照恢复——finally 补插 ROOT + 清 `plugin_*` 前缀模块 + 回填被清的旧模块对象（C 扩展禁止二次初始化，不删 host 期间新增的 numpy 等条目）
- **体检基线**：`python -m qwenpaw.plugins.check` → 48 findings（38 private-import error + 10 unmanaged-resource warning）——private-import 全部为宿主内部 API 依赖（_submit_turn 照抄官方端点/console_push 气泡/MemorySpace/agentscope_msg_to_message 等，SDK 公共面无对应物，**换皮不换骨**设计注记）；加载器不跑 compliance，运行时不受影响；`--runtime` 被 `report.ok` 门禁挡住（error findings 存在即跳过）——故 TestHost 验证走测试文件
- 测试：pytest **111 全绿**（104 原有零改动通过 + 7 TestHost 集成）；tools_schema.json 快照刷新（v0.1.9 旧文案 → v0.1.11 现行 + description 提取与运行时一致）
- **真机热装验收（REPL 直调层，2026-11-18）**：热装 active/enabled=True；宿主内实锤 `instance.sdk_context = PluginContext`（loader.py:1794 SDK 分支生效，generation 3）；8/8 工具 ownership 正确登记（无 TestHost 观察到的 owner=None 警告）；REPL 直调 invoke 全链路 success（jsonschema→handler→ToolChunk 流式：running chunk 携正文 + success 收尾）；spawn 真提交成功——子会话 chat 创建 + `chat.meta["agent_tools"]` 持久化落盘（v0.1.8 特性在 SDK 版继续工作，文件级实锤）；wait/recv/stop/list_session 真机响应与文案全对（空正文防御分支/stop 序列/簿记终态「已完成（1）」）；子会话正文空系 REPL 无 contextvar 的 agent 路由特殊性（chats.json agent=None），非迁移回归——真实对话链路待新会话快照自然验收
- 验收补记（同日）：新会话自然验收完成——spawn→wait 正文回收（「验收测试 OK」）/ list_session 簿记 / notice 全链路唤醒送达 / spawn_managed 托管路径实锤（task name 前缀 qwenpaw-plugin-agent-tools:direct_probe）/ 分裂窗口收敛（8/8 SDK descriptor，本会话绑定 gen4）
- 待办：无（自然验收已闭环）


## 0.1.11（2026-11-18）

工具 schema 瘦身（作者拍板）：省 token、易懂、不说明内部工作原理。

- **盲测驱动**：3 个无上下文子 agent 纸面问答（工具卡+场景题）零试错全对——改后 schema 核心语义全部传达成功；盲测同时抓回 5 个微调：count 上限数值 50 补回 / spawn 前台补「直接返回最终正文」/ notice 补「每回合」触发 / output_path 补「相对工作区」/ export desc 删「全量」（与默认 false 开关打架）
- **删除内部工作原理**：spawn「人工可在 WebUI 继续」「自动加身份前缀」、task/text 同删、list_session「chats.json meta 恢复/[restored]/v0.1.8」整段（-114 字符）、communicate recv「不含思考与工具调用」、wait timeout「超时返回仍运行中提示」
- **去内部术语**：「Scroll 驱逐轮」→「被压缩的历史轮」（context_read/context_export 共 3 处）
- **消断链**：context_read 的 idx 解释段并入 start 参数（原「见上」引用段在 docstring 重组后失去锚点）；notice ⚠️ 投递时机段并入第一段压缩保留（E1 教训的行为契约不丢）
- **量化**：LLM 可见 schema 1388→1095 字符（-21%）；list_session 单工具 167→63（-62%）
- **意外发现（知识库勘误）**：知识库《QwenPaw工具Schema生成机制》的「\n\n 截断规则（只取第一段）」已过时——实测 agentscope `_extract_func_description` 已改 short+long 全拼接，docstring 全部正文段（含第二段）都进 Agent 视野；方法论部分（改后回读 schema 验证）保留
- 测试：pytest 104 全绿（纯 docstring 文案变更，行为零改动；schema 回归锁定用例通过——短 id 提示全保留）
- 热装方式备注：项目尚未建 git 仓，zip 改用顶层条目打包（Get-ChildItem 排除 __pycache__/.pytest_cache）+ 固定 D:\Temp 路径（$env:TEMP 在宿主与 shell 解析不一致致首次安装 Path not found）

## 0.1.10（2026-11-17）

防误用祖先链 gap 修复（通知回合反向 send 测试发现，作者拍板）。

- **缺口（真机实测）**：子 agent 在被调起回合中反向 send 主会话**漏拦**——`check_misuse` 的祖先链判定依赖 `_lookup_agent` 从插件簿记反查 target 的 agent_id，而主会话/官方会话**不在簿记** → 反查返回空串 → `(agent_id="", session_id)` 恒不命中 parent 元组 → 放行。讽刺点：send 流程里 `locate_chat` 明明已拿到 target 完整四元组（含 agent_id），却只传了 session_id
- **修复**：`check_misuse` 增加 `target_agent_id` 显式参数（调用点从 locate 结果直传，`communicate.py` send 路径）；传 None 时回落簿记反查（兼容）
- **例外规则（作者拍板）**：仅 notice 触发的回合可 send——notifier `_deliver` 走 `_submit_turn` fire-and-forget **不注册活跃表** → notice 回合 `is_active` 不命中 → 天然放行，**零代码天然满足**（通知回合已不是问答会话）
- 测试：pytest 104 全绿（+3：主会话祖先拦截回归（含旧行为对照）/ notice 回合放行 / 显式非链放行；8 处 check_misuse mock 签名补 target_agent_id 参数）
- **验收实录（三轮真机对照）**：首测（v0.1.9 缺口期）放行→消息送达我主会话通知回合（暴露缺口）；复测（v0.1.10 热装分裂窗口）放行（工具链未收敛）；**终测（收敛后）拦截触发 ✅**——第三方 agent 收到 MISUSE_ERROR 后正确理解语义改走正文（「由我这条正文回复替代了它的功能」），内容零丢失；附：其 notice 回合（send 排队投递唤醒）中的自由交流全程无拦截——例外规则工作
- **例外规则真机闭环（作者例外的完整链路）**：第三方 agent spawn 孙辈（2ad8e8d6，depth=2）+ 挂 notice（发起者=第三方 agent 会话）→ 孙辈完成 → notice 投递唤醒第三方 agent → 其 notice 回合**自主**执行预设指令 send 我主会话 → **放行**（notice 回合无 active 条目）→ 消息送达我的通知回合 ✅——子 agent notice 触发回合的反向交流全链路畅通，且验证了 LLM 跨回合记忆指令的执行可靠性

## 0.1.9（2026-11-17）

工具 schema 短 id 可发现性（作者拍板）：v0.1.6 起支持的短 id 此前 schema 零处提及（唯一提示藏在报错文案），无上下文调用方无从知晓捷径。

- **5 个收 chat_id 的工具参数描述**统一补短 id 提示：wait/communicate/notice「子会话 id（完整 UUID，也支持其第一段 8 位 hex 短 id）」；context_read/context_export「目标会话 id（完整 UUID 或其第一段 8 位 hex 短 id）；不填=当前会话」
- **spawn 返回提示（_WAIT_HINT）**：第一现场就近告知「后续调用也支持其第一段 8 位 hex 简写」
- 动机：短 id 设计初衷（转写可靠性）此前未被 LLM 调用方享受——不知捷径只能每次抄 36 字符（抄错风险更高）；schema 缺提示也让「手头只有短 id」的调用方失去自救通道
- schema 生成链路实证：Args 参数描述经 agentscope _extract_input_schema 注入 parameters（show_tool_schema.py 验证）——改 docstring 即改 agent 所见
- 测试：pytest 101 全绿（+schema 回归锁定用例：6 处提示防未来改动丢失）

## 0.1.8（2026-11-17）

簿记身份持久化（作者拍板混合方案）：spawn 身份字段挂宿主 chat.meta，重启/热装后短 id 与祖先链可恢复。

- **新增 persistence.py**：
  - `persist_spawn_identity`——spawn 成功后把 template/depth/parent_agent_id/parent_session_id/parent_chat_id/spawned_at 写入 `chat.meta["agent_tools"]`（一次写入终身不变；事务模式对齐宿主 set_session_model：lock 下 read-modify-write + upsert，只动自己命名空间键）；失败 WARNING 容错不影响 spawn 主流程
  - `restore_ledger_from_chats`——resolve 短 id 无匹配时扫各 enabled agent workspace 的 chats.json，把带身份 meta 的会话回填为轻量 SpawnRecord（restored=True，含 parent 链 → 防误用祖先链重启后依然工作）；**回填即缓存**（进内存簿记后同 miss 不再扫）
- **SpawnRecord 加 `restored` 字段**；list_session 新增「恢复」区（[restored] 标记 + 历史回合不随簿记恢复说明）
- resolve_chat_id_input：短 id 无匹配 → 自动触发恢复 → 重解析（「无匹配」字符串判定，耦合自家文案）
- **「易变账」留内存**：turns/queues/active/notices 不持久化（回合历史本就在会话文件里）
- 宿主侧依据：ChatSpec.meta Dict[str,Any] 官方自定义字段（runtime_context/pawapp 命名空间先例）+ workspace.chat_manager 入口 + chats.json 单文件持久化（原子写）
- 测试：pytest 100 全绿（+9 persistence 用例：meta 构造/事务合并保兄弟键/缺失容错/回填字段/跳过已有/resolve 全链路触发/load_config 异常/缺文件）
- **验收实录（真机）**：spawn 14c9e0c7 → chats.json meta 六字段落盘（**spawn 自动写入路径实际生效**——分裂收敛后 wrapper 指新代码）；清簿记 → 短 id wait → 两条会话自动回填（restored=True/身份全对/parent 链）；recv 恢复后短 id 交互正常；list_session 恢复区显示
- **修补**：恢复回填用 meta.spawned_at 还原 created_at（初版漏还原导致 list_session 显示回填时刻而非真实 spawn 时间）

## 0.1.7（2026-11-17）

v0.1.6 生效验证实战 hotfix：短 id 链路真实环境全炸，实测抓出 + 修复 + 集成测试补盲。

- **修复（HIGH）**：`resolve_chat_id_input` 取簿记 known_ids 写成 `get_manager().all_records`（漏调用括号，method 被当 property 迭代）→ `'method' object is not iterable`——**所有走 check_chat_id 的工具（wait/communicate/notice/context_read/context_export）在真实环境必炸**，仅 spawn 与 list_session 幸存
- **测试漏网根因**：短 id 用例直接调纯函数 `resolve_chat_reference`（传手动构造 ids），包装函数 `resolve_chat_id_input`（utils→manager 单例接线）零集成覆盖；mock 场景该接线不执行，故 90 绿假阴性。**补集成测试**（真 manager 单例全链路：UUID/短 id 命中/歧义/session 拒绝四路径）→ 91 绿
- 顺手修正 `tools/common.py` check_chat_id 过时 docstring（「一步自愈」→ 短 id 前缀匹配）
- **实测验证（修复热装后）**：短 id wait 全链路命中（147272af→completed+正文）、session_id 拒绝文案完整、身份前缀完整版（UUID×1/回访指引/「对方会读取到」语义，第三方 agent 正文自述印证）、resolver 兜底（无簿记完整 UUID 定位成功）、list_session 展示
- **热装模块分裂实锤**（已知「热装清簿记」§7 的深层机制变体）：热装 reload 新建模块对象替换 sys.modules（非原地），工具 wrapper 绑定的旧模块实例存活——顶层 import 绑定旧 manager 单例（spawn 写入）vs 函数体内动态 import 命中新模块（resolve 读取）→ 簿记写旧读新分裂、短 id 永远无匹配。**新会话启动后统一消失**；会话内可用迁移簿记救急

## 0.1.6（2026-11-17）

短 id 支持（2026-11-17 作者拍板）。

- **新增**：短 id 输入 = 长 chat_id 的第一段（**严格 8 位 hex**， SHORT_ID_RE 校验）——在簿记已知 chat_id 内前缀匹配：命中 1 条解析为完整 id；命中 ≥2 条显式歧义报错「存在多个重复，请给完整的长 id」（git commit hash 同款安全模式，禁止静默取第一个——32 bit 空间生日碰撞：7.7 万条记录 50% 概率撞对）；0 条报无匹配 + 引导 list_session
- 匹配范围 = 插件簿记（插件工具的管理对象）；大小写不敏感；完整 UUID 输入优先于短 id 分支
- 测试：pytest 90 全绿（+短 id 全分支用例）

## 0.1.5（2026-11-17）

主键形态收紧（2026-11-17 作者拍板）。

- **改版**：`resolve_chat_reference` 移除 session_id 一步自愈——工具入口只认 chat_id（UUID）；session_id 形态输入（含簿记内有记录的 sub-）一律拒绝并给专门提示帮助自纠。理由：自愈依赖插件簿记，官方会话/主会话不在簿记恒拒绝，形态语义混乱
- 保留：`get_record_by_session`（内部用途：spawn 深度链 / 祖先链反查，非输入路径）
- 测试：pytest 89 全绿（-2 自愈用例 +1 拒绝用例，覆盖三种 session 形态）

## 0.1.4（2026-11-17）

第二轮三路复审（R-A 全量代码 / R-B 并发时序 / R-C 实测）发现核实与修复（作者要求：核实再修——逐条代码核对 + asyncio 动态复现实验）。

- **核实结论**：14 项实质发现 → 13 项属实、1 项半误报（R-B②后半句「后续 send 静默丢弃滞留消息」被实验证伪——滞留消息反而被后续 send 救回；「孤立滞留」前半句属实）
- **修复**：
  - B-② stop await 窗口内排队的消息孤立滞留（flusher 已取消未 done 拒绝重启）→ stop 完成后复查队列非空：等旧 flusher 退出 + 重启投递
  - B-③ 消费者无条件 remove_active 误删后续回合条目（T1 body-read 让出窗口内 flusher 注册 T2 覆盖同键）→ ActiveEntry 绑定回合 + remove_active_if_turn 身份守卫移除
  - B-① FIFO「已取未提」窄窗（popleft→attach 让出窗口内新 send 直提超车倒挂）→ SessionQueue.in_flight 标记（popleft 置位 / 成功·放回·异常三路清位 / send 入口一并排队）
  - A-M1 notice 投递后陈旧记录（completion_sent=True 滞留 24h）拦截再注册 → 重复注册检查补 not completion_sent
  - A-M2 send 直提被抢占「未送达，稍后重试」依赖 LLM 重试 → 入队复用 flusher 必达（与 flusher 放回队首对称）
  - 卫生项：闸①日志计数 +1 多计与措辞、空会话「0~-1」→「无可用轮」、AGENTS.md 测试数、notifier join 空格、escape_ctrl 死代码删除
- **新增**：resolve_parent_chat_id 匹配循环异常防御（WARNING + 降级）
- 测试：pytest 91 全绿（+4 回归闸：stop 窗口投递 / 移除守卫 / 投递后再注册 / 被抢占入队必达）

## 0.1.3（2026-11-17）

前缀反查修正（作者质疑驱动：降级分支不该存在）。

- **修正**：`resolve_parent_chat_id` 取值字段名错误（`chat.chat_id` → `chat.id`，ChatSpec 主键实锤）——原实现恒取空导致 v0.1.2 全部走短文案
- **语义重定位**：反查失败不是设计内场景——发起者三类（主会话/task session/插件子会话）全在宿主 chats 表（269 条数据实锤 + 宿主内真跑 resolver 双样本通过），正常路径恒成功；反查异常 → WARNING 日志留痕 + 短文案保底（消息不丢优先，不因回访地址缺失阻塞消息）
- 测试：pytest 87 全绿（降级测试改 monkeypatch resolver 异常）

## 0.1.2（2026-11-17）

身份前缀改版（2026-11-17 作者拍板）。

- **改版**：前缀文案重写——UUID 只出现一次（回访指引用「这个 chat_id」就近指代）；新增回访指引（子会话可主动联系发起者）；「对方会读取到」统一 spawn（直接返回）与 send 排队（wait/recv 拉取）两种场景语义；删「父Agent可见」「主会话」等场景限定词；删「的子agent会话」标注
- **新增**：`resolve_parent_chat_id`（宿主 resolver 反查发起者 chat_id，查不到降级无回访指引短文案）
- 保留：agent_id 段、直角引号「」
- 测试：pytest 87 全绿（+1 降级路径）

## 0.1.1（2026-11-17）

复审修复批次（三路无上下文复审 R-A/R-B/R-C + 作者拍板）。

- **修复**：notice 主触发链路通电（get_notifier 导入缺失，NameError 被宽 except 吞掉）；`get_current_context` 解包错位 ×2；`_truncated` 截断补 warning 日志；export `include_compressed=false` 变量复用导致驱逐内容仍被渲染（计数独立 + 渲染双守卫）；export 计数改轻量 SELECT COUNT；stop 残留死队列（cancel_flag）被 send 复用致消息静默丢失（入口清理 + 闸① 日志）；give_up 先 drop 后气泡（关误伤窗口）；消费者取消判定改 `task.cancelling()`（宿主吞 CancelledError 场景防假 completed/假 notice）
- **行为**：send 排队条件扩为「running 或队列非空」（FIFO 保序，防 busy→idle 过渡窗口超车）；flusher 连续 10 次提交失败放弃（仅空闲→投递→失败计次，running/被抢占不计，成功清零）→ drop_queue + sticky 气泡提示开发者 + ERROR 日志；export 元信息四态（0 轮 / N 轮未导出 / N 轮已含 / 未启用 / 读取失败）；8 工具 meta category runtime→agents
- **语义注记**：hist.conversation_history 为全量日志，驱逐区与可见区内容重叠是预期行为（作者拍板，无需除重）
- 测试：pytest 86 全绿（+18：HIGH 回归 / FIFO 竞态 / 异常触发 notice / 超限放弃 ×3 / blocks JSON 串 / 截断日志 / 解包 / 死队列 / export 守卫 ×3 / 取消守卫）

## 0.1.0（2026-11-17）

首发版本。

- 8 工具：spawn / communicate / wait / notice / list_agent / list_session / context_read / context_export
- 内部函数路线：`_submit_turn` 五步序列照抄 `/console/chat/task` 端点，无 HTTP loopback
- 簿记：SpawnRecord 多回合模型（wait/notice/最新状态跟随最新 TurnRecord）、TTL 24h + 500 条 LRU 惰性清理（running 顺延跳过）
- 排队 flusher：TOCTOU 双闸（取队首前复查 + 提交后补偿 stop）、提交异常回滚队首、被抢占放回重试、每轮现取 workspace
- 防误用：祖先链收窄版拦截（只拦 send 且目标在祖先链）+ 活性复核自愈 + MAX_SPAWN_DEPTH=5
- 通知：completed/failed/cancelled 全触发、聚合投递（3s 窗口 swap-and-clear）、console 双投递（气泡 + 唤醒）、非 console 信使 in-process 直调、迁移门防假完成、watch_task 可取消 + 24h 安全阀
- 上下文：正负 idx（-E~v_hi 连续时间线）、驱逐历史块展开（blocks JSON 解析 + row_cap 绕开 + _truncated 过滤）、tool_call 函数形式不截断、tool_result 所见即所得
- 任务登记表：消费者/监视协程全部登记，卸载钩子统一取消（防热重载孤儿）
- 测试：pytest 68 全绿；三路无上下文对抗复审（设计对照 / 宿主签名核对 / 并发生命周期）1+2 HIGH 全修复
