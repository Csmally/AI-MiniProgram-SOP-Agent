# 微信小程序 SOP Agent

微信小程序新版本上线前的**新增功能 SOP（标准操作流程）自动化检查 Agent**。用户上传 PRD 需求文档，Agent 自动解析功能信息、生成结构化检查清单，并通过 miniprogram-automator 驱动微信开发者工具执行 UI/交互 + 接口/数据验证。

详细计划见 [PLAN.md](PLAN.md)。

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React 18 + Vite + framer-motion |
| 后端 | FastAPI + LangGraph + LangChain |
| AI | DeepSeek-V4-Pro / DeepSeek-V4-Flash / Qwen3.7-Flash |
| 持久化 | PostgreSQL（LangGraph PostgresSaver） |
| 小程序自动化 | miniprogram-automator（Node sidecar 桥接微信开发者工具） |
| 包管理 | uv (Python) + npm（前端 / sidecar） |

## 前置条件

1. **Python 3.13+** 和 [uv](https://docs.astral.sh/uv/)
2. **Node.js 20+** 和 npm（前端与 sidecar 依赖）
3. **PostgreSQL** 已运行，并创建数据库
4. **DeepSeek API Key**（必需）
5. Qwen API Key（可选，截图分析用）
6. **微信开发者工具**（自动化执行需要；无需手动开自动化端口，由后端懒拉起）
7. （可选）llama.cpp 本地大模型——不依赖云端 API 的替代方案（见下方「本地 LLM」）

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

# 4. 安装 sidecar 依赖（小程序自动化桥）
cd sidecar
npm install
```

## 启动

### sidecar（终端 1，小程序自动化桥）

```bash
cd sidecar
node server.js
```

- 监听 `http://127.0.0.1:9310`（可用 `.env` 的 `AUTOMATOR_SIDECAR_URL` 改）
- 微信开发者工具**不用手动开**：后端首次调用自动化工具时自动懒拉起（首次调用会多花 10~20s）
- 验证：`curl http://127.0.0.1:9310/health` → `{"ok":true,"connected":false}`（未执行过检查前 connected 为 false 属正常）

> ⚠️ 迁移中：`navigate_to` / `switch_tab` / `get_pages` 已接入真实自动化；其余工具（tap / input_text / get_text / element_exists / screenshot）尚为占位实现，调用会返回 `[工具执行失败]`。

### 后端（终端 2）

```bash
uv run python -m sop_agent.main
```

- 服务地址：`http://127.0.0.1:8000`
- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`
- HOST / PORT / DEBUG 全部从 `.env` 读取，改配置不用改启动命令

### 前端（终端 3）

```bash
cd frontend
npm run dev
```

- 浏览器打开：`http://localhost:5173`
- Vite 已配置代理，`/api/*` 自动转发到后端 8000 端口

### 本地 LLM（终端 4，可选）

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

## 使用流程

1. 打开 `http://localhost:5173`，自动加载最近的会话（或新建）
2. 点击「上传 PRD」选择 Markdown 需求文档 → prd_agent 解析出功能列表
3. 点击「生成检查清单」→ sop_agent 生成检查清单（不满意可点「重新生成」）
4. 右侧面板审核/编辑检查项 → 「确认并开始检查」
5. executor Agent 串行逐项检查（SSE 实时进度；微信开发者工具单实例约束）→ report Agent 生成报告
6. 左侧面板可切换/删除历史会话，会话持久化在 PostgreSQL

## 项目结构

```
├── backend/sop_agent/
│   ├── main.py              # FastAPI 入口
│   ├── api/routes.py        # REST API + SSE 流式
│   ├── agents/              # 5 个 Agent 子图（prd/sop/chat/executor/report）
│   ├── core/
│   │   ├── orchestrator.py  # LangGraph 主图（router + 游标串行执行）
│   │   ├── state.py         # MainGraphState
│   │   ├── llm.py           # 模型工厂（DeepSeek/Qwen 任务路由）
│   │   └── config.py        # 配置管理
│   ├── sop/models.py        # Pydantic 数据模型
│   ├── tools/
│   │   ├── automator_tools.py   # 8 个自动化工具（LLM 调用面）
│   │   └── automator_session.py # sidecar HTTP 桥接（懒拉起 + 探活）
├── sidecar/                 # Node sidecar：官方 miniprogram-automator 的 HTTP 封装
│   ├── server.js            # 唯一 DevTools 自动化连接 + 导航/截图等端点
│   └── package.json
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
| sidecar 不可达（端口 9310 连不上） | `cd sidecar && node server.js`；sidecar 缺失时 executor 自动降级桩 |
| 检查项报「尚未连接微信开发者工具」 | 确认 sidecar 在跑 + `.env` 配了 `AUTOMATOR_PROJECT_PATH` / `AUTOMATOR_CLI_PATH`（懒拉起依赖这两项） |
| 懒拉起报 CLI 路径错误 | 检查 `AUTOMATOR_CLI_PATH` 指向 `cli.bat` 真实路径（PITFALLS 9.1/9.2） |
