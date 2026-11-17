# MEMORY.md — qwenpaw-plugin-agent-tools

## 一句话定位

agent / 子 agent 会话管理插件：spawn 其他 agent 模板的持久子会话 + 续聊/等待/通知/上下文读取全链路 + 短 id（歧义拒绝+schema 可发现性）+ 簿记身份持久化 + 防误用三态 + schema 瘦身（8 工具，当前 v0.1.11，2026-11-17 首发）。

## 关键档案

- 设计蓝本：`DESIGN.md`（8 工具规格 + 拍板决策 + 宿主锚点）
- 环境事实与硬约束：见 `AGENTS.md`（勿回退清单）
- 架构与用法：见 `README.md`
- 版本细节：见 `CHANGELOG.md`（每批次完整记录）

## 当前状态（v0.1.11）

- 测试 104 全绿（纯 mock + 真单例集成，无 qwenpaw 运行时依赖）；已热装服役
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
