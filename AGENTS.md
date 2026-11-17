# AGENTS.md — qwenpaw-plugin-agent-tools

## 项目定位

agent / 子 agent 会话管理插件（spawn→等待→交流→通知→上下文读取全链路）。设计蓝本 = `DESIGN.md` v2.2（474 行，20 条拍板决策）——**改实现前先读它**，勿翻案已拍板决策。

## 环境事实

- QwenPaw 源码：`D:\Data\github_repo\QwenPaw\src\qwenpaw`（宿主 API 签名核对以它为准）
- 工程骨架参照：`D:\Data\github_repo\qwenpaw-plugin-process-tools`
- 宿主 agentscope 2.0.10：ToolChunk 在 `agentscope.tool` 顶层、ToolResultState 在 `agentscope.tool._response`、TextBlock 在 `agentscope.message`
- 测试：`D:\Software\miniconda3\envs\qwenpaw\python.exe -m pytest tests/ -q`（101 个，纯 mock + 真单例集成，无 qwenpaw 运行时依赖；测试环境 get_current_context 回落 default；⚠️ 只有 qwenpaw conda 环境装了 agentscope——normal/py315 跑会 ModuleNotFoundError）

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
- 改返回文案 → 对照 DESIGN.md §3.9 定稿（决策 16：chat_id 完整值只在 [CHAT_ID] 行出现一次）
- 改工具签名 → 同步 plugin.json meta.tools + plugin.py register_tool 两处
- 不擅自 git commit（作者自行决定）
