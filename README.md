# 微信小程序 SOP Agent

微信小程序新版本上线前的**新增功能 SOP（标准操作流程）自动化检查 Agent**。用户上传 PRD 需求文档，Agent 自动解析功能信息、生成结构化检查清单，并通过 minium 驱动微信开发者工具执行 UI/交互 + 接口/数据验证。

详细计划见 [PLAN.md](PLAN.md)。

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React 18 + Vite + framer-motion |
| 后端 | FastAPI + LangGraph + LangChain |
| AI | DeepSeek-V4-Pro / DeepSeek-V4-Flash / Qwen3.7-Flash |
| 持久化 | PostgreSQL（LangGraph PostgresSaver） |
| 小程序自动化 | minium（微信开发者工具） |
| 包管理 | uv (Python) + npm (前端) |

## 前置条件

1. **Python 3.13+** 和 [uv](https://docs.astral.sh/uv/)
2. **Node.js 20+** 和 npm
3. **PostgreSQL** 已运行，并创建数据库
4. **DeepSeek API Key**（必需）
5. Qwen API Key（可选，截图分析用）
6. 微信开发者工具（阶段四 minium 集成需要）

## 安装

```bash
# 1. 安装后端依赖
uv sync

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入：
#   DEEPSEEK_API_KEY=sk-xxx
#   QWEN_API_KEY=sk-xxx
#   DATABASE_URL=postgresql://postgres:password@localhost:5432/sop_agent

# 3. 安装前端依赖
cd frontend
npm install
```

## 启动

### 后端（终端 1）

```bash
uv run python -m sop_agent.main
```

- 服务地址：`http://127.0.0.1:8000`
- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`
- HOST / PORT / DEBUG 全部从 `.env` 读取，改配置不用改启动命令

### 前端（终端 2）

```bash
cd frontend
npm run dev
```

- 浏览器打开：`http://localhost:5173`
- Vite 已配置代理，`/api/*` 自动转发到后端 8000 端口

## 使用流程

1. 打开 `http://localhost:5173`，自动加载最近的会话（或新建）
2. 点击「上传 PRD」选择 Markdown 需求文档
3. 多 Agent 自动协作：prd_agent 解析功能 → sop_agent 生成检查清单（不满意可点「重新生成」）
4. 右侧面板审核/编辑检查项 → 「确认并开始检查」
5. executor Agent 并行逐项检查（SSE 实时进度）→ report Agent 生成报告
6. 左侧面板可切换/删除历史会话，会话持久化在 PostgreSQL

## 项目结构

```
├── backend/sop_agent/
│   ├── main.py              # FastAPI 入口
│   ├── api/routes.py        # REST API + SSE 流式
│   ├── core/
│   │   ├── orchestrator.py  # LangGraph 状态图
│   │   ├── state.py         # AgentState (MessagesState)
│   │   └── config.py        # 配置管理
│   ├── sop/models.py        # Pydantic 数据模型
│   ├── prd/                 # PRD 解析
│   ├── tools/               # minium Tool 封装
│   ├── executor/            # 检查执行器
│   └── report/              # 报告生成
├── frontend/
│   └── src/
│       ├── App.jsx
│       ├── components/      # ChatPanel, SessionList, ChecklistView 等
│       ├── hooks/useSession.js
│       └── api/client.js
├── tests/fixtures/          # 测试用 PRD 样例
├── pyproject.toml
└── .env.example
```

## 测试

```bash
# 后端测试（示例 PRD 解析 + SOP 生成）
uv run python -c "
import requests
r = requests.post('http://127.0.0.1:8000/api/sessions')
sid = r.json()['session_id']
r = requests.post(f'http://127.0.0.1:8000/api/sessions/{sid}/prd',
    files={'file': open('tests/fixtures/sample_prd_user_profile.md', 'rb')})
print(r.json()['message'])
"
```

测试样例见 `tests/fixtures/`：
- `sample_prd_user_profile.md` — 示例 PRD（用户个人中心）
- `sample_prd_user_profile_parsed.json` — 解析结果
- `sample_prd_user_profile_sop.json` — 生成的检查清单

## 常见问题

| 问题 | 解决 |
|------|------|
| 端口 8000 被占用 | `taskkill //F //IM uvicorn.exe` 后重启 |
| 数据库连接失败 | 检查 `.env` 的 `DATABASE_URL`，确认 PostgreSQL 已启动 |
| API Key 无效 | 检查 `.env` 的 `DEEPSEEK_API_KEY` 是否为真实 key |
| 前端请求 500 | 查看后端终端日志定位错误 |
