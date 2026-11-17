# qwenpaw-plugin-agent-tools

面向 **agent / 子 agent 会话**的管理插件，把「spawn → 等待 → 交流 → 通知 → 上下文读取」整条链路接通。

## 功能

| 工具 | 图标 | 用途 |
|------|------|------|
| `agent_tools_spawn` | 🚀 | 以指定 agent 为模板 spawn 持久子会话（默认后台，返回 chat_id） |
| `agent_tools_communicate` | 💬 | 与子会话交互：send 发起新一轮（忙则排队）/ recv 读最新一轮正文 / stop 停止 |
| `agent_tools_wait` | ⏳ | 前台等待回合结束取最终正文（超时**绝不停止**目标会话） |
| `agent_tools_notice` | 🔔 | 完成通知注册（completed/failed/cancelled 全触发，仅会话空闲时投递） |
| `agent_tools_list_agent` | 🧭 | 列可用 agent（spawn 的 template 取值来源） |
| `agent_tools_list_session` | 📋 | 列本插件 spawn 的会话（找回 chat_id，🔔=通知注册中） |
| `agent_tools_context_read` | 📖 | 读会话上下文（正负索引翻页 + think/tool/monologue/驱逐历史开关） |
| `agent_tools_context_export` | 📤 | 全量导出为 Markdown 块时间线文件 |

## 差异化价值（vs 官方工具链）

- 官方 `spawn_subagent` 只能以**当前 agent** 身份 spawn 一次性 worker；本插件能**以其他 agent 为模板**（如 demo-agent）spawn 持久会话
- 官方 `chat_with_agent` 前台超时会**级联停止**目标会话；本插件超时只放弃等待
- 没有「向子 agent 会话续聊」的工具（人工 WebUI 可以，agent 不能）——`communicate send` 补上
- 会话上下文读取/导出（读 Scroll 驱逐历史只限自己）——`context_read/export` 跨会话可读

## 典型流程

```
list_agent → spawn(task, template="demo-agent") → [CHAT_ID]
  → wait(chat_id) 等完成取正文（推荐）
  或 notice(chat_id) 挂通知 → 停手时自动唤醒
  → communicate(chat_id, "send", "继续…") 续聊
  → context_read(chat_id, start=-5) 读最近 5 轮
  → context_export(chat_id, output_path="...") 导出归档
```

## 特性速览（v0.2.0）

- **公共 SDK 架构**：v2.3.0 SDK 1.0.0 迁移完成——`PluginContext.register_tool + ToolSpec` 声明式注册，`on_activate` 生命周期 + `resources.defer` 卸载清理；工具函数本体零改动（换皮不换骨）
- **短 id**：所有收 chat_id 的工具均支持完整 UUID 或其第一段 8 位 hex（前缀匹配；歧义显式拒绝——git commit hash 同款安全模式）。schema 参数描述与 spawn 返回提示均已注明
- **簿记身份持久化**：spawn 时把身份字段（template/depth/parent/spawned_at）写入宿主 `chat.meta["agent_tools"]`（跟随 chats.json 落盘）；宿主重启/热装清簿记后，短 id 首次使用时自动从 chats.json 恢复回填（restored=True，含 parent 链）——list_session「恢复」区可见
- **防误用三态**：①问答回合（spawn/send 调起）send 祖先 → 拦截（内容走正文）；②notice 触发回合 → 放行（通知回合已非问答会话，自由交流）；③send 非祖先 → 正常排队投递
- **身份前缀**：spawn/send 的消息自动携带发起者身份 + chat_id 回访指引——子 agent 可主动用该 chat_id 反向联系发起者

## 安装


```pwsh
qwenpaw plugin install <zip> --force
```

依赖：无第三方运行时依赖（纯宿主内部 API + 公共 SDK）。`qwenpaw_version: 2.3.0 ~ 2.9.0`（SDK 1.0.0）。

## 架构

```
plugin.py          入口：SDK 形态（PluginContext.register_tool + ToolSpec × 8）
                   schema 动态推断（agentscope 推断函数）+ on_activate 生命周期
                   + resources.defer 卸载清理
manager.py         簿记单例：SpawnRecord 多回合 / 队列 / 活跃表 / 通知注册 / TTL
submit.py          宿主适配器：_submit_turn 五步序列（照抄 /console/chat/task 端点）
                   + 消费者（SSE 错误信号 + 终态正文）+ stop 序列
session_reader.py  宿主归一化适配 + 轮/块分类 + 正文/monologue 判定
context.py         正负 idx 映射 + 块时间线 Markdown 渲染
notifier.py        注册制完成通知 + 聚合投递 + console 双投递 + 迁移门
messenger.py       非 console 频道信使（in-process 直调，无 HTTP）
tools/             8 个工具 + common（校验/终态描述/防误用拦截）
```

## 设计文档

`DESIGN.md`（v2.2）：8 工具规格、20 条拍板决策、宿主锚点行号、测试策略、风险表——实现与复审修复的对照全在里面。
