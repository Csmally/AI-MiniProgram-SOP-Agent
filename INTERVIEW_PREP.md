# 面试准备文档 — AI 应用开发工程师

> 个人使用，随时更新。所有内容基于本仓库真实代码，面试引用时以代码为准。
> 定位：**自带前端能力的 AI 应用全栈工程师**（不是「会点 AI 的前端」）。

---

## 1. 一句话项目介绍

> 「微信小程序新版本上线前的人工回归太慢，我做了一个多 Agent 系统：上传 PRD 文档，AI 自动解析功能点、生成 SOP 检查清单，然后通过 MCP 协议驱动微信开发者工具真实执行每一项检查（点击、输入、截图验证），最后生成测试报告。全流程支持流式进度、人工审核中断、多用户会话隔离、链路追踪可视化。」

关键词：**LangGraph 多 Agent、MCP、工具调用、SSE 流式、HITL 人工审核、可观测性、多模型路由**

---

## 2. 项目数字速查（面试引用用）

| 数字 | 含义 |
|------|------|
| 5 个 Agent 子图 | prd（解析需求）/ sop（生成清单）/ chat（问答）/ executor（执行检查）/ report（生成报告） |
| 17 个 MCP 工具 | 14 个自动化工具 + set_run_context / is_minium_available / snapshot_app_state |
| 15 轮 | 单个检查项的 LLM 工具循环上限（MAX_TOOL_ITERATIONS） |
| 600 秒 | 单个检查项的墙钟 deadline（ITEM_DEADLINE_SECONDS） |
| 120 秒 / 2 次 | LLM 调用超时 / 重试次数 |
| 15 秒 | SSE 心跳间隔（防代理/网关空闲超时） |
| 3 个模型源 | DeepSeek-V4-Pro（重活）/ Qwen3.7-Flash（轻活）/ 本地 llama.cpp Qwen3.8-27B 多模态（截图分析） |
| ~4700 行 | 后端 Python（sop_agent + mcp_server） |
| ~1400 行 | 前端 React（8 个组件 + 1 个核心 hook） |
| 7 天 | JWT 令牌有效期 |
| 409 | 同一会话并发执行的返回码（每会话互斥锁） |
| 404 | 非本人会话统一返回（防枚举） |

---

## 3. 架构总览

```
┌─────────────┐   REST + SSE   ┌──────────────────────────────────┐
│ React 前端   │ ─────────────▶ │ FastAPI（同步端点线程池 + SSE    │
│ Vite + SSE  │ ◀───────────── │  worker 线程 + asyncio.Queue 桥接）│
└─────────────┘                │  ┌────────────────────────────┐  │
                               │  │ LangGraph 主图（PostgresSaver）│ │
                               │  │  START ─router─▶ parse_prd  │  │
                               │  │              │▶ generate_sop│  │
                               │  │              │▶ chat_agent  │  │
                               │  │              │▶ dispatch →  │  │
                               │  │  execute_agent ⇄ 游标串行循环 │  │
                               │  │  → collect → report → END  │  │
                               │  └────────────────────────────┘  │
                               │  PostgreSQL（checkpoints + 会话    │
                               │  归属 + trace_runs 链路追踪）       │
                               └───────────────┬──────────────────┘
                                               │ streamable-http
                                     ┌─────────▼─────────┐
                                     │ MCP server（独立进程）│
                                     │ FastMCP + 17 工具  │
                                     │ minium 会话单例     │
                                     └─────────┬─────────┘
                                               │ 独占
                                     ┌─────────▼─────────┐
                                     │ 微信开发者工具       │
                                     └───────────────────┘
```

### 用户流程与状态机

```
idle →(上传 PRD)→ prd_uploaded →(生成清单)→ sop_generated →(人工审核 interrupt)
     →(确认)→ running →(逐项执行，SSE 进度)→ completed（报告）
```

---

## 4. 核心难点与解决方案（面试故事点）

每个难点都是面试官会追问的点，准备 1 分钟的展开版本。

### 4.1 微信开发者工具单实例约束 → MCP 进程独占 + 串行游标循环

**问题**：微信开发者工具同时只能有一个自动化会话连接（minium 限制），检查项不能并行执行；且 minium 会话生命周期复杂，与后端进程耦合会互相拖垮。

**方案**：
- 把 minium 工具集拆成**独立 MCP server 进程**（FastMCP），会话单例由 server 进程天然独占；
- executor 只经 MCP 调用工具（`langchain-mcp-adapters` 的 MultiServerMCPClient），单实例约束由「server 进程独占」保证；
- 主图内执行是**串行游标循环**（`exec_cursor` + 条件边自循环），逐项执行；
- 同一套工具可切 stdio 传输，直接给 Claude Desktop / Claude Code 用（一处实现，多种消费）。

**追问应对**：为什么不用任务队列？——当前是单用户规模工具，游标循环 + 会话锁已满足；规模化时应抽 MCP worker 池 + 队列，这是已识别的演进方向。

### 4.2 长链路状态管理 → LangGraph checkpoint + 每会话互斥锁

**问题**：一次完整执行可能 10+ 分钟，中间有 LLM 调用、工具调用、人工审核中断；前端刷新后状态不能丢；同一会话不能并发跑两个图。

**方案**：
- **PostgresSaver checkpointer**：每一步状态落库，`get_state` 纯读恢复，前端刷新后从 checkpoint 重建 UI（前端不做本地状态推断，**服务端 checkpoint 是唯一事实来源**）；
- **HITL 人工审核**：`interrupt_before=["review_list"]` 挂起图，用户审核/编辑清单后经 `update_state(as_node=START)` 写入 approval 再推进；
- **input-only invoke**：所有操作从 START 路由器进入（fresh run 语义），自动丢弃旧 pending 中断，避免 approve 后残留中断干扰；
- **每会话互斥锁**：同一 session 同时只有一个图执行（SSE 端点非阻塞 acquire，冲突立即 409）；
- reducer 通道（`operator.add`）+ `run_id` 过滤，多轮执行累积数据互不串扰。

### 4.3 流式体验 → SSE + worker 线程桥接

**问题**：LLM 调用 30~70 秒，不能让用户干等；图在同步代码里跑，不能阻塞 asyncio 事件循环（尤其 Windows）。

**方案**：
- 同步图在 **worker 线程**执行，事件经 `call_soon_threadsafe` + `asyncio.Queue` 桥接回事件循环，再以 SSE 输出；
- 非 SSE 端点用同步 `def`（FastAPI 自动线程池）；
- **15 秒心跳**（SSE 注释行）防代理/网关空闲断连；
- 客户端断开不中断执行：worker 跑完并落 checkpoint，重进即恢复；
- 事件协议：`phase` / `item`（逐项进度）/ `report` / `done`（最终状态）。
- 前端手写 SSE 解析：`fetch` + ReadableStream + TextDecoder，buffer 拼接跨 chunk 事件（`stream: true` 处理多字节 UTF-8 截断）。

**追问应对**：为什么 SSE 不用 WebSocket？——单向推送场景（服务端→客户端进度），SSE 基于 HTTP：代理/CDN 友好、无需双向帧管理、实现简单。代价是浏览器 EventSource 不支持 POST，所以手写 fetch 流式解析；心跳需自己实现。

### 4.4 可靠降级 → 桩模式 + 探活缓存

**问题**：MCP server 没启动 / minium 环境没配置 / 中途挂掉，整个 run 不能崩。

**方案**（[executor_agent.py](backend/sop_agent/agents/executor_agent.py) 双模式）：
- `is_ready()` 探活（开关 + 连接成功 + server 侧 `is_minium_available`），结果缓存；
- 不可用 → 桩实现（结果标注 `[桩]`），不中断整个 run；
- 工具调用失败 → `invalidate()` 重置缓存，**下一项重新探活**（server 恢复后自动切回真实执行）。

### 4.5 Agent 执行可靠性 → 循环护栏 + 结构化判定 + 历史清理

**问题**：LLM 可能无限循环调用工具、只描述不执行、判定输出格式漂移。

**方案**：
- **循环护栏**：15 轮工具循环上限 + 600 秒墙钟 deadline（到限注入「基于已有证据判定」提示）；
- **催促执行**：LLM 第一轮只输出计划没调用工具时，注入「请直接调用工具执行」；
- **结构化判定**：`with_structured_output(CheckResult, method="function_calling")`，失败降级到 prompt 约束 + 健壮 JSON 解析（`_verdict_fallback`）；
- **历史清理**（`_clean_history`）：DeepSeek 对 tool_call_id 配对严格——未兑现的 tool_calls 补占位 ToolMessage（重复补会 400），切片截断产生的孤儿 ToolMessage 丢弃。

### 4.6 跨检查项上下文 → 状态快照 + 前项结果注入

**问题**：小程序是连续会话，检查项 A 已经导航到的页面，检查项 B 不应重复导航/点击。

**方案**：每项执行前注入 `previous_check_results`（本 run 内前项结果摘要）+ `current_app_state`（MCP `snapshot_app_state` 实时快照），LLM 对照判断哪些步骤已完成，只补做缺失部分。提示词里明令「严禁重复导航」。

### 4.7 多模型路由与成本 → 任务分派 + 本地视觉模型

**方案**（[llm.py](backend/sop_agent/core/llm.py)）：
- **任务路由**：重推理（执行检查）用 DeepSeek-V4-Pro，轻活用 Qwen3.7-Flash，`MODEL_ROUTING` 配置驱动；
- **本地 llama.cpp 多模态模型**（Qwen3.8-27B）专做截图分析：隐私（截图不出本机）、成本、可离线；OpenAI 兼容接口，一个环境变量切换；
- temperature 按任务区分（判定/解析 0.3，聊天 0.7）；
- **DeepSeek V4 思考模式默认关闭**：与强制 tool_choice 互斥（API 报错），且显著拖慢响应——这是实测踩坑后的决策。

### 4.8 多用户与安全 → JWT + 归属表 + 统一 404

**方案**：
- JWT Bearer（7 天），`session_owners` 表记录会话归属；
- 所有会话端点双重校验：认证 + 归属；**非本人会话统一 404**（不暴露存在性，防枚举）；
- 删除会话时级联清理 trace 数据。

### 4.9 可观测性 → 自建链路追踪 + 可视化平台

**方案**（[tracing/handler.py](backend/sop_agent/tracing/handler.py)）：
- LangChain `BaseCallbackHandler` 捕获三类 run：chain（图节点）/ llm（每次调用，含 token 消耗）/ tool（MCP 工具入参出参），写入 PG `trace_runs`；
- **父子层级推断**：configure hook 继承的 run 没有原生 parent_run_id，用实例级 chain run 栈（跨线程 + 锁）推断；
- **tracing 永不伤害业务**：所有写库 try/except 吞异常；
- 配套 Next.js 可视化平台（仓库外）读同一张表，展示完整调用链、每次 LLM 调用的 prompt/token/tool_calls。

---

## 5. 个人角色定位（简历用）

- 独立设计并实现完整系统：后端（FastAPI + LangGraph + MCP）+ 前端（React + SSE）+ 可观测平台（Next.js）
- 侧重前端与产品体验：流式交互、进度可视化、会话管理
- 能力标签：Agent 编排 / MCP / 流式系统 / 多用户生产化 / 全栈交付

---

## 6. 三分钟自我介绍（面试版，可直接练）

> 1. **背景与目标**（30 秒）：前端开发出身，现在转 AI 应用开发。前端经验让我在做 AI 应用时对交互体验和全栈交付有天然优势。
> 2. **项目引入**（60 秒）：小程序上线前的人工回归又慢又容易漏，我独立做了这个多 Agent 自动化检查系统。上传 PRD → AI 解析功能 → 生成 SOP 检查清单 → **真实驱动微信开发者工具执行每一项检查**（导航、点击、输入、截图视觉验证）→ 生成报告。核心技术选型：LangGraph 编排 + MCP 工具协议 + PostgreSQL 状态持久化 + SSE 流式。
> 3. **最难的三个问题**（90 秒）：
>    - **单实例约束**：微信开发者工具只允许一个自动化会话，用 MCP server 进程独占 + 串行游标循环解决；
>    - **长链路可靠性**：一次执行 10+ 分钟、含人工审核中断，用 LangGraph checkpoint 做到任意时刻恢复，并设计了桩降级、循环护栏、结构化判定三层可靠性保障；
>    - **体验**：LLM 调用 30~70 秒，用 worker 线程 + SSE 桥接做实时进度，断线也不丢状态。
> 4. **收尾**（30 秒）：配套做了链路追踪可视化平台，每次 LLM 调用、工具调用、token 消耗全记录。正在补 RAG 和评测体系。这个项目让我确信自己能胜任 AI 应用开发。

---

## 7. 高频追问与参考答案

### 架构选型类

**Q1：为什么用 LangGraph，不用自己写 while 循环调 LLM？**
A：免费获得三样东西：① checkpoint 持久化（断点恢复、中断重放，自研成本极高）；② HITL interrupt 机制（人工审核挂起/恢复）；③ reducer 通道语义（并行累积 vs 覆盖）。自己的循环要处理这些会变成半个框架。代价也诚实说：框架 API 升级有踩坑成本（langgraph 1.2 的行为变化我们记录在 PITFALLS.md），调试比裸循环黑盒。

**Q2：MCP 解决什么问题？为什么不直接 import minium？**
A：① 进程隔离——微信开发者工具单实例约束，minium 会话必须进程独占，拆成独立 server 进程才能保证；② 协议标准化——同一套工具 schema 化后，executor LLM 和其他 MCP 客户端（Claude Desktop）都能消费；③ 关注点分离——工具实现（小程序自动化细节）与 Agent 逻辑（怎么检查）解耦。另外 MCP 是 2024 年底兴起的行业事实标准，选择它也有生态考量。

**Q3：SSE 和 WebSocket 怎么选？**
A：看数据方向。本项目是单向推送（服务端→客户端进度），SSE 基于 HTTP：代理/CDN 无需特殊配置、天然支持断线后语义（配合服务端 checkpoint 恢复）、实现简单。WebSocket 适合双向高频（聊天室、协同编辑）。代价：浏览器 EventSource 只支持 GET，所以用 fetch 手写流解析；心跳自己发（15 秒，防网关空闲超时）。

**Q4：PostgreSQL 里存的是什么？checkpoint 机制大概什么原理？**
A：LangGraph 的 PostgresSaver 把每次 super-step 后的状态序列化到 checkpoints 表，thread_id 对应会话。每次 invoke 从最新 checkpoint 恢复状态再执行，节点级增量写。所以进程重启、前端刷新都不丢状态。我们还用 session_owners 表做归属，trace_runs 表做调用链。

**Q5：为什么不用 RAG？（预期必问，诚实版）**
A：诚实回答：当前场景的核心不是知识检索，是流程编排 + 工具调用——PRD 是用户上传的、检查对象是小程序本身，没有「企业知识库」这个输入源，chat agent 只是辅助。RAG 是重要但此处非核心的能力。但我知道这是 AI 应用最重要的模式之一，正在补：计划把项目文档/PITFALLS 语料 + pgvector 做成知识库问答。**（如果面试前已补上 RAG，此答案改为讲 RAG 实现细节）**

**Q6：怎么评估 Agent 质量？**
A：两层。线上：自建链路追踪记录每次 LLM 调用（prompt、tool_calls、token）、每次工具调用、最终判定，人工 review 失败项归因。执行层：判定走结构化输出 + fallback 解析，保证格式稳定。已识别的不足：还没有自动评测集（LLM-as-judge、检查清单覆盖率评分），在改进计划里。

**Q7：为什么前端没用 TypeScript？**
A：独立项目快速验证期，JS 迭代最快；后端有 Pydantic 强类型兜底数据契约。但承认：组件和 hook 复杂度上来后（SSE 事件协议、会话状态）TS 的收益已经明显，迁移在计划内，工作量不大。**（说完要能举一个 TS 具体收益的例子：事件类型定义、hook 返回值契约）**

**Q8：JWT 存 localStorage，XSS 怎么办？**
A：知道权衡：localStorage 有 XSS 读取风险。本项目无第三方脚本注入面（无富文本渲染、无 CDN 脚本），风险可控。生产化方案：httpOnly cookie + CSRF 防护，或短时 access token + refresh token。能说清楚「为什么当前可接受、生产会怎么改」就过关。

### 工程细节类

**Q9：DeepSeek 踩过什么坑？**
A：① 思考模式与强制 tool_choice 互斥——API 直接报错，且拖慢响应，统一 `thinking=disabled`；② 工具返回必须是文本 content——list（get_pages）/ bool（element_exists）直接返回会 400，统一 `str()` 包装；③ tool_call_id 配对严格——已兑现的调用重复补 ToolMessage 会 400，历史清理要精确区分未兑现/已兑现。

**Q10：Windows 上 asyncio 有什么坑？**
A：事件循环策略差异（ProactorEventLoop）、开发模式 reload 时后台线程未结束导致进程假死。解决方案：LLM 调用显式 timeout（否则 SSE worker 线程永不结束）、图同步跑在 worker 线程、连接池线程安全共享。这些记录在 PITFALLS.md。

**Q11：SSE 断线了怎么办？进度丢吗？**
A：不丢。客户端断开时 worker 线程继续跑完并落 checkpoint（CancelledError 直通），用户重进页面从 checkpoint 读最终状态重建 UI。SSE 是「展示层」，checkpoint 是「事实层」。未做的是同一条流的自动 resume，接受这个取舍。

**Q12：为什么所有操作都从 START 路由器进入（input-only invoke）？**
A：统一入口保证 fresh run 语义——每次 invoke 从 START 重新路由，自动丢弃旧 pending 中断。否则 approve 之后残留的 interrupt 状态会干扰下一次操作，出现 Ambiguous update 之类的问题。这是踩坑后的设计，langgraph 1.2 验证过。

**Q13：reducer 通道是什么？为什么 exec_results 永不清空？**
A：LangGraph 状态通道语义：普通字段 LastValue（覆盖写），`messages` 用 add_messages，`exec_results`/`agent_progress` 用 `operator.add`（累积写，支持循环内增量）。跨 run 永久累积，靠 `run_id` 过滤隔离——collect 汇总和前端统计都按 run_id 过滤，避免多轮执行数据串扰。

**Q14：人工审核中断（interrupt）是怎么实现的？**
A：`interrupt_before=["review_list"]` 让图在 review_list 前挂起；用户编辑清单走 `update_state(as_node=START)` 纯落盘不触发节点；确认后以 `next_action=approve` 从 START 重入，router 分发到 dispatch → 执行循环。

**Q15：一个检查项的执行流程具体怎么跑的？**
A：① 注入任务负载（检查步骤 + 预期结果 + 前项结果 + 小程序状态快照）；② LLM 循环：每次一个工具调用（工具 schema 由 MCP 动态拉取）→ 观察结果 → 下一步，上限 15 轮/600 秒；③ 导航前必须先 get_pages 发现真实路径、操作前必须先 get_page_elements 拿真实元素清单（提示词里禁止猜 selector）；④ 交互后主动验证（get_text/element_exists/screenshot），截图分析走本地视觉模型；⑤ 最终结构化判定 passed/failed + 理由。

**Q16：LLM 只说不做（不调工具）怎么办？**
A：第一轮没调用工具时注入「请直接调用工具执行检查步骤，不要只描述计划」再试；定位失败时工具返回候选元素清单，提示词要求换目标而非盲目重试同一 selector；到轮次上限仍有未兑现 tool_calls 时，补占位 ToolMessage 保消息序列合法后强制判定。

### 开放问题类（展示思考深度）

**Q17：并发 100 个用户同时跑检查会怎样？**
A：诚实答：当前每会话锁保证正确性，不同会话可并行（Postgres 连接池线程安全）；但瓶颈在微信开发者工具单实例——真实执行只能串行，规模化需要多实例 DevTools + MCP worker 池 + 任务队列，当前系统是单实例设计，这是明确的演进路径。

**Q18：如果让你重新做，会改什么？**
A：① 前端第一天就用 TS；② 评测体系（evals）从第一版就建——生成清单的覆盖率评分、判定的回归集；③ RAG 能力预留给知识库问答；④ 部署容器化 + 日志聚合。选型上 LangGraph + MCP + PG 的组合会保留。

**Q19：token 成本怎么控制？**
A：任务路由（重活/轻活分模型）、本地模型跑视觉分析（零 API 成本）、执行历史清理（判定只保留最近 12 条消息，不把全量历史塞给 LLM）、循环轮次上限。追踪里记录了每次调用的 token，可以精确算单次检查成本。

---

## 8. 已知弱点与改进路线（诚实项，被问到要主动说）

| 缺口 | 现状 | 计划 |
|------|------|------|
| RAG | 无向量检索（依赖里无 embedding/向量库） | pgvector + 项目文档知识库，chat agent 升级 |
| 评测体系 | 有追踪无自动评测 | LLM-as-judge + 清单覆盖率评分 + 回归集 |
| 前端 TS | 全 JS | 迁移（~1400 行，工作量小） |
| 前端测试 | 无 | SSE 解析器 + useSession hook 的 Vitest 单测 |
| 部署 | 本地跑 | Docker + 文档化 |
| 规模化 | 单 DevTools 实例 | worker 池 + 队列 |

> 面试策略：不等面试官发现，主动提 1~2 个自己在补的项（建议提 RAG 和 evals），
> 并说出具体方案——这展示的是成长性，不是缺陷。

---

## 9. 理论基础速查清单（面试前过一遍）

- **Embedding**：文本向量化原理（预训练语言模型隐层输出）、相似度度量（余弦/点积/欧氏）、为什么归一化
- **RAG**：chunk 策略（定长/语义）、检索召回率问题、rerank、上下文组装、幻觉与 cite 出处
- **Context window**：token 与字数的换算、窗口耗尽策略（截断/摘要/滑窗）
- **采样参数**：temperature / top-p / top-k 的含义与选择
- **幻觉**：成因（训练数据噪声、缺乏 grounding）、缓解（RAG、工具调用、结构化输出、提示约束）
- **RAG vs 微调**：什么场景选哪个（动态知识 vs 风格/格式固化）
- **Agent 模式**：ReAct 循环、Plan-and-Execute、工具选择、失败重试
- **Python**：asyncio 事件循环机制、GIL、FastAPI 依赖注入、Pydantic v2
- **MCP**：协议分层（host/client/server）、传输（stdio/streamable-http）、与 function calling 的关系

---

## 10. 面试前 Checklist

- [ ] 3 分钟自我介绍练到不卡壳（第 6 节）
- [ ] 每个数字（第 2 节）都答得上来出处
- [ ] Q1~Q8 全部过一遍，Q9~Q16 对着代码再看一眼
- [ ] 弱点表（第 8 节）里 RAG / evals 有具体计划可讲
- [ ] 理论基础清单（第 9 节）无盲区
- [ ] 能画出第 3 节的架构图（白板或纸）
- [ ] 准备好 demo：如果面试带电脑，本地四终端起服务走一遍完整流程
- [ ] 简历上的每个字都能解释（包括前端细节：SSE 解析 buffer 逻辑、401 广播登出）
