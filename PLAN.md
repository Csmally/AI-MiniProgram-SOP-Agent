# 微信小程序 SOP_Agent

> 创建时间：2026-08-12 | 最后更新：2026-08-13

## 背景

构建一个基于 **FastAPI + React** 的 **Web 聊天界面 AI Agent**，用于在微信小程序每次新版本发布前，**自动执行新增功能的 SOP（标准操作流程）检查**。用户在 Web 聊天界面中上传 PRD 需求文档（Markdown），Agent 自动解析功能信息、生成结构化检查清单，并通过 minium 驱动微信开发者工具执行 UI/交互 + 接口/数据验证，实时回传进度和结果。

**目标用户：** 需要在新版本上线前验证新增功能的 QA 测试工程师和开发人员。

## 需求汇总

| 维度 | 决策 |
|------|------|
| SOP 检查范围 | UI/交互检查 + 接口/数据检查 |
| 运行方式 | Web 聊天界面 |
| 后端框架 | FastAPI (Python) |
| 前端框架 | React (Vite 构建) |
| 检查依据 | PRD 需求文档（Markdown 格式） |
| 小程序交互方式 | 微信开发者工具自动化（minium） |
| AI 能力 | DeepSeek API（结构化输出 + 流式对话） |
| 持久化 | PostgreSQL（LangGraph PostgresSaver） |

## 架构总览

```
┌──────────────────────────────────────────────────┐
│                React 前端 (Vite)                  │
│                                                   │
│  ┌─────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ 会话列表 │ │ 聊天面板 │ │ 检查清单/进度/报告 │  │
│  └─────────┘ └──────────┘ └──────────────────┘  │
└────────────────────┬─────────────────────────────┘
                     │ REST API + SSE 流式
┌────────────────────▼─────────────────────────────┐
│              FastAPI 后端                          │
│                                                    │
│  ┌─────▼──────────────▼───────────────────────┐   │
│  │    LangGraph 主图（多 Agent 子图管道）      │   │
│  │  router → prd/sop/chat/executor/report      │   │
│  │  Agent 子图 + 人工审核中断 + Send 并行执行   │   │
│  └──────────────────────────────────────────────┘   │
└──────────────────────┬─────────────────────────────┘
                       │
                    PostgreSQL
              (PostgresSaver checkpointer
                + langgraph_checkpoint 表)
```

### 数据流程（多 Agent 子图 + LangGraph 主图驱动）

```
用户上传PRD(Markdown)
       ↓
主图 START ──router──▶ 所有操作唯一入口（按 next_action 分发）
       │
       ├─ upload_prd → prd_agent(子图) 解析功能 → END（phase=prd_uploaded）
       ├─ generate_sop → sop_agent(子图) 生成清单 → review_list [Human-in-the-loop 中断]
       ├─ approve/run → dispatch → fan_out[Send] → execute_item ×N（并行执行 Agent 子图）
       │               → collect 汇总 → report_agent(子图) 生成报告
       └─ chat → chat_agent(子图) 对话答疑
```

- **5 个 Agent 子图**：prd_agent / sop_agent / chat_agent / executor_agent（每检查项一实例）/ report_agent，各自独立 State schema、LLM 与提示词
- **操作模式**：REST = 触发 action + 读 checkpoint；phase 唯一来源是后端 checkpoint，前端不本地推断
- **SSE 进度**：`POST /run/stream` 流式推送各 Agent 实时进度（同步图在 worker 线程执行）

## 项目目录结构

```
ai-miniprogram-sop-agent/
├── backend/
│   └── sop_agent/
│       ├── main.py                # FastAPI 入口
│       ├── api/routes.py          # REST API + SSE 流式（action 触发 + checkpoint 读取）
│       ├── agents/                # 5 个 Agent 子图（chat/prd/sop/executor/report）
│       ├── core/
│       │   ├── orchestrator.py    # 主图编排（router + Send fan-out + 统一操作 API）
│       │   ├── state.py           # MainGraphState（reducer 通道）
│       │   ├── llm.py             # 模型工厂（任务路由）
│       │   └── config.py          # 配置管理（.env 驱动）
│       ├── sop/models.py          # Pydantic 数据模型
│       ├── prd/                   # （预留）PRD 解析
│       ├── tools/                 # （预留）minium Tool 封装
│       ├── executor/              # （预留）检查执行器
│       └── report/                # （预留）报告生成
├── frontend/
│   ├── src/
│   │   ├── App.jsx                # 根组件
│   │   ├── components/
│   │   │   ├── SessionList.jsx    # 历史会话列表
│   │   │   ├── ChatPanel.jsx      # 聊天面板（流式）
│   │   │   ├── Modal.jsx          # 定制弹窗
│   │   │   ├── ChecklistView.jsx  # 检查清单
│   │   │   ├── ProgressPanel.jsx  # 执行进度
│   │   │   └── ReportView.jsx     # 报告展示
│   │   ├── hooks/useSession.js    # 会话状态 Hook
│   │   └── api/client.js          # REST 封装
│   ├── package.json
│   └── vite.config.js
├── tests/fixtures/                # 测试用 PRD 样例
├── pyproject.toml
├── .env                           # 环境变量（不提交）
└── .env.example
```

## 模型策略（三模型混合）

| 角色 | 模型 | 用途 |
|------|------|------|
| **重活推理** | DeepSeek-V4-Pro | 结构化输出、深度推理、Tool Calling |
| **轻活对话** | DeepSeek-V4-Flash | 通用对话、格式化汇总、快速响应 |
| **截图分析** | Qwen3.7-Flash | UI 截图视觉分析、元素对比、布局校验 |

### 各节点模型分配

| LangGraph 节点 | 使用模型 |
|----------------|----------|
| `parse_prd` | DeepSeek-V4-Pro |
| `generate_sop` | DeepSeek-V4-Pro |
| 对话 (chat) | DeepSeek-V4-Flash |
| `execute_checks` (规划) | DeepSeek-V4-Pro |
| `execute_checks` (截图分析) | Qwen3.7-Flash |
| `generate_report` | DeepSeek-V4-Flash |

## 后端 API 设计

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/sessions` | 创建新会话 |
| `GET` | `/api/sessions` | 列出所有会话 |
| `GET` | `/api/sessions/{id}` | 获取会话完整状态 |
| `DELETE` | `/api/sessions/{id}` | 删除会话 |
| `POST` | `/api/sessions/{id}/prd` | 上传 PRD 文件 |
| `POST` | `/api/sessions/{id}/generate` | 生成 SOP 检查清单 |
| `POST` | `/api/sessions/{id}/approve` | 审核通过 |
| `GET/PUT/DELETE/POST` | `/api/sessions/{id}/check-items` | 检查项 CRUD |
| `POST` | `/api/sessions/{id}/run` | 开始执行检查 |
| `POST` | `/api/sessions/{id}/run/stream` | SSE 流式执行（Agent 实时进度） |
| `GET` | `/api/sessions/{id}/report` | 获取报告 |
| `POST` | `/api/sessions/{id}/chat` | AI 对话 |
| `POST` | `/api/sessions/{id}/chat/stream` | AI 对话（SSE 流式） |

## 实施进度

| 阶段 | 状态 | 内容 |
|------|------|------|
| 阶段一：后端基础 | ✅ 完成 | FastAPI + LangGraph + DeepSeek 集成 |
| 阶段二：PRD + SOP | ✅ 完成 | PRD 解析、SOP 生成（真实数据测试通过） |
| 阶段三：前端 | ✅ 完成 | React 聊天界面、暗色科技风 UI、流式对话、会话管理、PostgreSQL 持久化 |
| 阶段四：检查执行 | 🟡 进行中 | ✅ 多 Agent 架构重构（5 个 Agent 子图 + Send 并行执行 + SSE 进度）；⬜ minium Tool 自动化检查（executor_agent 仍为桩）；⬜ 聊天滚动摘要记忆 |
| 阶段五：报告收尾 | ⬜ 未开始 | 报告生成、README、生产部署 |

## 技术决策记录

| 决策 | 选择 | 原因 |
|------|------|------|
| Agent 编排 | LangGraph 主图 + Agent 子图 | 主图管道保留确定性流程 + HITL；5 个环节各为独立 Agent 子图 |
| 操作入口 | 统一 action 路由 | `invoke({next_action, ...}, config)` 从 START 分发，自动丢弃旧 pending；phase 唯一来源 = checkpoint |
| 并行执行 | Send API fan-out | 每检查项一个 executor Agent 并行（reducer 通道 + run_id 过滤隔离） |
| 状态定义 | MainGraphState + 子集 schema | `add_messages` / `operator.add` reducer；子图共享通道声明 LastValue 只回本项贡献 |
| 持久化 | LangGraph PostgresSaver + psycopg_pool | ConnectionPool 线程安全（SSE worker 线程共享） |
| 图执行 | 同步 `graph.invoke()/stream()` | 节点改同步（`llm.invoke()`），避免 Windows 事件循环问题；SSE 时图在 worker 线程跑 |
| REST 并发 | 非 SSE 端点用同步 `def` | FastAPI 自动线程池执行——含 LLM 的同步图调用（30~70s）不阻塞事件循环；SSE 端点保持 async + worker 线程 |
| 会话排序 | `MAX(checkpoint_id)` | checkpoints 表无 created_at；checkpoint_id 为 uuid6（时间有序） |
| 聊天流式 | SSE (Server-Sent Events) | 简单单向流，`llm.astream()` 逐 token 推送；执行进度同走 SSE |
| 弹窗 | 自定义 Modal + framer-motion | 不用系统 alert/confirm |
| UI 风格 | 暗色科技风 + 渐变 | 用户要求"科技感" |

## 待办

- [ ] 阶段四：minium 集成（微信开发者工具自动化，替换 executor_agent 桩逻辑）
- [ ] 阶段五：报告生成、README、生产部署
- [x] 多 Agent 架构重构（5 个 Agent 子图 + Send 并行 + SSE 进度）
- [ ] 聊天记忆优化：chat_agent 目前只带最近 10 条消息，超过即失忆。方案：记忆采用滚动摘要（最近 K 条原文 + 更早内容压缩为摘要，用 Flash 模型）。详见 2026-08-13 讨论。

## 风险与假设

| 假设 | 风险 | 缓解措施 |
|------|------|----------|
| 微信开发者工具已安装 | 用户环境可能未配置 | README 提供安装指引；启动时检测并提示 |
| minium 兼容目标小程序 | API 兼容性问题 | 早期验证；提供手动检查兜底 |
| DeepSeek API Key 可用 | 无 API 访问权限 | .env 配置；支持自定义 base_url |
| PRD 有一定 Markdown 结构化 | 格式不统一 | DeepSeek 作为非结构化 PRD 兜底解析 |
| Qwen3.7-Flash 截图分析准确性 | 复杂 UI 截图可能误判 | 结合 minium 元素级结构化验证 |
| 单机运行（localhost） | 多人协作需求 | 架构预留；后续可加用户认证 |
