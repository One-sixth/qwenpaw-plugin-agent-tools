# AGENTS.md — qwenpaw-plugin-agent-tools

## 项目定位

agent / 子 agent 会话管理插件（spawn→等待→交流→通知→上下文读取全链路）。设计蓝本 = `DESIGN.md` v2.2（行数以文件为准；头部含 v0.2.0 SDK 迁移注记；拍板决策散见正文 §3/§4）——**改实现前先读它**，勿翻案已拍板决策。

## 环境事实

- QwenPaw 源码：`D:\Data\github_repo\QwenPaw\src\qwenpaw`（宿主 API 签名核对以它为准）
- 工程骨架参照：`D:\Data\github_repo\qwenpaw-plugin-process-tools`
- 宿主 agentscope 2.0.10：`_extract_input_schema` / `_extract_func_description` 在 `agentscope.tool._utils`（v0.2.1 起推断函数**只活在测试链路**做守门基准，运行时 schema 走 schemas.py 固化常量）；旧 ToolChunk 封装已废弃（v0.2.0 起 utils 出 SDK ToolResult）
- 测试：`D:\Software\miniconda3\envs\qwenpaw\python.exe -m pytest tests/ -q`（167 个 = mock/单例 + PluginTestHost 集成 + B2/B3 新用例；测试环境 get_current_context 回落 default；⚠️ 只有 qwenpaw conda 环境装了 agentscope——normal/py315 跑会 ModuleNotFoundError；⚠️ test_sdk_host 的 `_run` 驱动含 sys.modules 快照恢复——TestHost load 会删 sys.path 项目根条目、close 清同目录扁平模块，勿拆）

## 硬约束（复审实锤，勿回退）

1. **stop 序列只允许** `communicate(action="stop")` 与 flusher TOCTOU 补偿调用；消费者内禁止任何对目标会话的停止调用
2. **前台等待超时绝不停止目标会话**（wait 语义总纲）
3. **每次操作现取 workspace**（宿主规矩 never cached pointers）——flusher 每轮循环 / 消费者终态读取 / watch 协程每轮都要现取
4. **消费者必须走 manager.register_task 登记**（卸载钩子统一取消，防热重载孤儿——宿主对裸 create_task 无清扫）
5. **notice 主触发链路**：consume_and_track 写终态后必须调 on_turn_finished（观察丢失不触发）
6. **hist DB blocks 列是 JSON 字符串**（写入端 _to_json）——读取必须 json.loads；sql_query 有 row_cap + _truncated 标记行
7. **导入双模式**：相对导入 + try/except ImportError 回落（tools/ 兄弟模块回落用 `tools.X`）

## 已知权衡（作者拍板记录）

- `context.py` 驱逐历史用 MemorySpace 直查（工厂 `make_recall_history` 返回 LLM 工具文本形态，插件需结构化行做块时间线渲染）——与设计 §2.1 字面有偏差，属技术权衡
- 簿记纯内存，宿主重启/热重载丢失（降级语义已定义：resolver 兜底定位 / wait 无簿记观察模式 / notice 迁移门）

## 维护纪律

- **plugin.py 必须模块级导出 `plugin` 对象**（`plugin = AgentToolsPlugin()`）——热装 probe 检查 `Plugin module must export a 'plugin' object`，漏了直接安装失败（v0.1.0 热装首坑实测）
- **资源创建只在 on_activate**（v0.2.0 SDK 生命周期契约）：register 里只做 ToolSpec 声明；单例预热与 `resources.defer` 卸载清理都在 `context.on_activate` 回调里——register 碰 resources 会 ResourceError
- **schemas.py 就是 schema**（v0.2.1 固化定稿）：工具 description + input_schema 的唯一正式来源是 schemas.py 的 `_TOOL_SCHEMAS` 常量（additionalProperties=False 含内）——改 schema 只改它，改完跑 `python show_tool_schema.py` 刷新 tools_schema.json 快照；docstring / Args 注解只是代码注释，**不做双向锚定**（作者拍板：双向对齐麻烦，勿再建 docstring↔常量一致性测试）
- 改返回文案 → 对照 DESIGN.md §3.3/§3.4 与 CHANGELOG 定稿（chat_id 完整值在 wait 的 `### 会话` 块头 / notice 的 `chat_id=` 行只出现一次）
- 改工具签名 → 同步 plugin.json meta.tools + plugin.py `_TOOLS` 表两处
- 不擅自 git commit（作者自行决定）
