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
| 小程序自动化 | minium（MCP server 进程独占会话，直连微信开发者工具） |
| 包管理 | uv (Python) + npm（前端） |

## 前置条件

1. **Python 3.13+** 和 [uv](https://docs.astral.sh/uv/)
2. **Node.js 20+** 和 npm（前端依赖）
3. **PostgreSQL** 已运行，并创建数据库
4. **DeepSeek API Key**（必需）
5. Qwen API Key（可选，截图分析用）
6. **微信开发者工具**（自动化执行需要；需手动开启服务端口：设置 → 安全设置 → 服务端口）
7. **MCP server 已启动**（执行检查前必须先起 `uv run python -m mcp_server`，否则检查项自动降级桩；见下方「MCP server」）
8. （可选）llama.cpp 本地大模型——不依赖云端 API 的替代方案（见下方「本地 LLM」）

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
#   MINIUM_ENABLED=true
#   MINIUM_PROJECT_PATH=C:/path/to/miniprogram/project
#   MINIUM_DEV_TOOL_PATH=C:/Program Files (x86)/Tencent/微信web开发者工具/cli.bat

# 3. 安装前端依赖
cd frontend
npm install
```

## 启动

> 启动前确认微信开发者工具已打开目标项目，并开启自动化服务端口（设置 → 安全设置 → 服务端口），否则检查项会报连接错误。

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

### 本地 LLM（终端 3，可选）

本地部署 llama.cpp 视觉大模型（Qwen3.8-27B 多模态），替代云端 DeepSeek/Qwen API：

```bash
# 在 llama.cpp 构建目录下执行（或用 llama-server 的绝对路径）
./llama-server -m E:\llm_models_llamacpp\Qwen3.8-27B-Q3_K_M.gguf \
  --mmproj E:\llm_models_llamacpp\mmproj-BF16.gguf \
  -ngl 999 -c 16384 -fa on --cache-type-k q8_0 --cache-type-v q8_0 \
  -np 1 -b 1024 -ub 256 --temp 0.6 --top-p 0.95 --top-k 20 --jinja --reasoning off
```

- 服务地址：`http://127.0.0.1:8080/v1`（OpenAI 兼容接口）
- 后端切到本地模型：`.env` 打开开关即可，**所有模型任务**（重活推理/对话/截图分析）统一走本地：

  ```bash
  LOCAL_LLM_ENABLED=true
  # 以下三项默认值已适配 8080 端口，无需配置；llama-server 不校验 API key
  # LOCAL_LLM_URL=http://127.0.0.1:8080/v1
  # LOCAL_LLM_MODEL=Qwen3.8-27B-Q3_K_M.gguf
  # LOCAL_LLM_API_KEY=local-any-value
  ```

- 用回云端 API：`LOCAL_LLM_ENABLED=false`（或删除该行）——恢复原有 DEEPSEEK_*/QWEN_* 任务路由

### MCP server（终端 4，执行检查前必须）

minium 工具集跑成独立 MCP server（FastMCP，streamable-http），executor **只经 MCP**
调用工具（单 DevTools 实例约束由 server 进程独占保证；server 不可用时自动降级桩模式）：

```bash
uv run python -m mcp_server
# .env 同步开启：
#   MCP_ENABLED=true            # executor 走 MCP 调用
#   MCP_SERVER_URL=http://127.0.0.1:8765/mcp   # 后端连接地址（默认）
#   MCP_SERVER_PORT=8765        # server 监听端口（默认）
```

- 同一套工具也可给任意 MCP 客户端使用：`MCP_TRANSPORT=stdio uv run python -m mcp_server`
  （Claude Desktop / Claude Code 直接接入做小程序自动化）
- 工具集：14 个执行工具 + set_run_context / is_minium_available / snapshot_app_state
  （server 专属），实现单一事实来源在 `backend/mcp_server/tools/minium_tools.py`

## 使用流程

1. 打开 `http://localhost:5173`，**注册/登录账号**（JWT 令牌，7 天有效；会话按用户隔离，互不可见）
2. 登录后自动加载本人最近的会话（或新建）
3. 点击「上传 PRD」选择 Markdown 需求文档 → prd_agent 解析出功能列表
4. 点击「生成检查清单」→ sop_agent 生成检查清单（不满意可点「重新生成」）
5. 右侧面板审核/编辑检查项 → 「确认并开始检查」
6. executor Agent 经 MCP server 串行逐项检查（SSE 实时进度；微信开发者工具单实例约束）→ report Agent 生成报告
7. 左侧面板可切换/删除历史会话，会话持久化在 PostgreSQL

## 项目结构

```
├── backend/sop_agent/          # 后端（FastAPI + LangGraph）
│   ├── main.py              # FastAPI 入口
│   ├── api/routes.py        # REST API + SSE 流式
│   ├── agents/              # 5 个 Agent 子图（prd/sop/chat/executor/report）
│   ├── core/
│   │   ├── orchestrator.py  # LangGraph 主图（router + 游标串行执行）
│   │   ├── state.py         # MainGraphState
│   │   ├── llm.py           # 模型工厂（DeepSeek/Qwen 任务路由）
│   │   └── config.py        # 配置管理
│   ├── sop/models.py        # Pydantic 数据模型
│   └── tools/
│       └── mcp_client.py    # MCP 工具客户端（executor 经此调用工具）
├── backend/mcp_server/         # MCP 服务（与 sop_agent 平级，独立进程）
│   ├── server.py            # FastMCP 注册 17 个工具 + 入口
│   └── tools/
│       ├── minium_tools.py     # 14 个自动化工具（工具实现唯一来源）
│       └── minium_session.py   # minium 会话单例（全局锁 + 自动重建）
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
# 后端测试（登录 → 建会话 → 上传示例 PRD 解析）
uv run python -c "
import requests
base = 'http://127.0.0.1:8000'
login = requests.post(base + '/api/auth/register',
    json={'username': 'demo_user', 'password': 'demo123456'}).json()
headers = {'Authorization': 'Bearer ' + login['token']}
r = requests.post(base + '/api/sessions', headers=headers)
sid = r.json()['session_id']
r = requests.post(base + f'/api/sessions/{sid}/prd',
    headers=headers,
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
| 检查项带 `[桩]` 前缀 | MCP server 未启动，或 minium 环境未配置（`MINIUM_ENABLED` / 两项路径），executor 自动降级桩 |
| 连接报「IDE service port disabled」 | 开发者工具 → 设置 → 安全设置 → 服务端口 → 开启（PITFALLS 6.1） |
| 元素查询超时 | 开发者工具「详情 → 本地设置 → 调试基础库」降到 3.16.1 及以下（PITFALLS 6.5） |
