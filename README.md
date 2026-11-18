# qwenpaw-plugin-agent-tools

让 agent 能派出**持久子会话**：派活、等结果、续聊、收完成通知、翻会话上下文，一条链路全通。

## 官方工具哪里不够用

QwenPaw 自带一组 agent 工具（`list_agents` / `chat_with_agent` / `submit_to_agent` / `spawn_subagent`），日常问一句答一句没问题。但它们都是**一次性形态**：

| 官方工具 | 它的形态 | 局限 |
|---|---|---|
| `spawn_subagent` | 一次性 worker | 干完活就结束，不能续聊；只能以当前 agent 身份开 |
| `chat_with_agent` | 前台问答 | 调用方超时放弃时，**对方的会话也会被级联停止** |
| `submit_to_agent` | 后台任务句柄 | 只能轮询查进度，没有「完成时主动叫我」 |
| `chat_with_agent` / `spawn_subagent` | — | 都读不到另一个会话的历史上下文 |

本插件把子会话当成一个**长期存在的对话对象**来管理，而不是一次性调用：

- spawn 出来的会话**一直活着**，随时续聊，还能以其他 agent 为模板开
- `wait` 超时只是「我先不等了」，子会话照常跑完，回头再取
- `notice` 挂上完成通知，子会话跑完会**主动唤醒**你（成功/失败/取消都触发）
- `context_read` / `context_export` 跨会话读写历史——接手别人 spawn 的会话也行

## 8 个工具

| 工具 | 干什么 |
|---|---|
| `agent_tools_list_agent` | 列出可用 agent（spawn 的模板来源） |
| `agent_tools_spawn` | 以某个 agent 为模板开持久子会话，默认后台运行，返回 chat_id |
| `agent_tools_wait` | 前台等子会话跑完，直接拿最终正文；超时不杀会话 |
| `agent_tools_communicate` | 续聊（send）/ 读最新输出（recv）/ 停止（stop）；对方忙则自动排队 |
| `agent_tools_notice` | 挂完成通知，子会话结束时自动唤醒你，正文随通知送达 |
| `agent_tools_list_session` | 列出自己 spawn 过的会话（忘了 chat_id 时用） |
| `agent_tools_context_read` | 读任意会话的上下文（最近几轮、思考块、工具调用都能选） |
| `agent_tools_context_export` | 把整个会话导出成 Markdown 文件，留档复盘 |

所有收 chat_id 的工具都支持完整 UUID 或它的前 8 位简写（`36053fc5` 这种），抄起来省事。

## 场景案例

**派个活，等结果：**

```
spawn(task="分析这份数据", template="demo-agent")   → 返回 chat_id
wait(chat_id)                                        → 直接拿最终正文
```

**活要跑很久，先去干别的：**

```
spawn(task="重构整个模块")          → chat_id
notice(chat_id)                    → 挂上通知
（继续干自己的事）
（子会话完成，通知自动唤醒：「已完成，正文如下……」）
```

**活干完还要接着改：**

```
communicate(chat_id, text="第 3 个函数再优化一下")    → 对方忙则排队，必达
wait(chat_id)                                        → 等新一轮结果
context_read(chat_id, count=5)                       → 不确定改了啥？翻最近几轮
```

**接手一个不是自己 spawn 的会话：**

```
context_read(chat_id, count=10)      → 先看它之前聊了什么
communicate(chat_id, text="继续")    → 接着聊
```

## 安装

```pwsh
qwenpaw plugin install <zip> --force
```

无第三方依赖。要求 QwenPaw 2.3.0+（SDK 1.0.0）。

## 文档

- `DESIGN.md`：设计规格与决策记录
- `CHANGELOG.md`：版本沿革
