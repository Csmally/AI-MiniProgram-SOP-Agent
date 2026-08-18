# 开发坑点记录（PITFALLS）

> 本项目开发中踩过的坑、根因与解决方案。**每解决一个新坑就往这里补一条**，避免重复踩。
> 格式：现象 → 根因 → 方案。最后更新：2026-08-18。

## 目录

1. [DeepSeek API](#1-deepseek-api)
2. [LangGraph](#2-langgraph)
3. [Windows / uvicorn / 进程](#3-windows--uvicorn--进程)
4. [FastAPI 并发模型](#4-fastapi-并发模型)
5. [前端状态管理](#5-前端状态管理)
6. [minium（微信小程序自动化）](#6-minium微信小程序自动化)
7. [结构化输出](#7-结构化输出)
8. [测试与调试](#8-测试与调试)
9. [automator / sidecar（小程序自动化桥）](#9-automator--sidecar小程序自动化桥)

---

## 1. DeepSeek API

### 1.1 思考模式与强制 tool_choice 互斥
- **现象**：`with_structured_output(method="function_calling")` 报 400 `Thinking mode does not support this tool_choice`
- **根因**：DeepSeek V4 默认开启思考模式，思考模式下 API 拒绝 `tool_choice` 强制指定（GitHub issue #1376）
- **方案**：请求体加 `extra_body={"thinking": {"type": "disabled"}}` 关闭思考。注意参数形状是**顶层 `thinking` 字段**（不是 V3.1 的 `chat_template_kwargs`，那个会被 SDK TypeError 或 API 无视）。我们的 `get_llm` 已全局关闭（副作用：响应快 4 倍、temperature 等采样参数恢复生效）

### 1.2 json_schema response_format 未开放
- **现象**：`with_structured_output(method="json_schema")` 报 400 `This response_format type is unavailable now`
- **方案**：用 `method="function_calling"`（关思考后可用），schema 由 function 参数强制，等效

### 1.3 json_mode 要求 prompt 含 "json" 一词
- **现象**：`method="json_mode"` 报 400 `Prompt must contain the word 'json'`
- **方案**：系统提示词里保留 "JSON" 字样即可；但 json_mode 只保语法不保 schema（模型可能自创中文键名），不如 function_calling

### 1.4 没有独立官方 SDK
- **现象**：想「换 deepseek 的包」绕过 API 限制
- **根因**：DeepSeek 官方方案就是 OpenAI SDK + `base_url`（无独立 Python SDK）；API 层限制换任何 SDK 都绕不过
- **方案**：维持 ChatOpenAI 封装；`init_chat_model` 需额外装 `langchain` + `langchain-deepseek`，当前无收益

### 1.5 工具消息序列完整性（400 高频来源）
- **现象**：agent 循环里报 400 `Messages with role 'tool' must be a response to a preceding message with 'tool_calls'`
- **根因**（两个变体）：
  a. 历史切片切断 `AIMessage(tool_calls)` 与其 `ToolMessage` 的配对 → 孤儿 tool 消息；
  b. 给**已兑现**的 tool_call 重复补占位 ToolMessage（重复 tool_call_id）
- **方案**：清理历史时①只给「未兑现」的 tool_call 补占位（用已存在 ToolMessage 的 id 集合判断）；②窗口开头是孤儿 ToolMessage 则丢弃；③ToolMessage 必须紧跟其 AIMessage。见 `executor_agent._clean_history`（有回归测试）

### 1.6 LLM 请求无超时会挂死整个服务
- **现象**：后端「假死」——health 连 TCP 层都不应答、CLOSE_WAIT 堆积、DB 连接数为 0
- **根因**：ChatOpenAI 默认无超时；DeepSeek 请求挂起 → SSE worker 线程永不结束 → reload/关机时进程等它收尾关不掉
- **方案**：`get_llm` 统一 `timeout=120` + `max_retries=2`；连接池关闭加 `timeout=5` 保护（`orchestrator.close`）

### 1.7 工具返回非字符串类型 → 400 deserialize 失败
- **现象**：agent 循环里报 400 `invalid type: string "pages/loginPage/index", expected struct ChatCompletionRequestContentBlock`
- **根因**：工具返回 list（get_pages 页面路径数组）或 bool（element_exists）时 ToolMessage.content 保持原类型；langchain-openai 把 list 内容当 content blocks 数组序列化，但元素是裸字符串，DeepSeek 反序列化失败
- **方案**：执行循环里工具结果统一 `str()` 转文本再进 ToolMessage（旧的截断版天然带 str()，去掉截断后这个副作用消失了才暴露）

## 2. LangGraph

### 2.1 新会话创建跑图导致 phase 错乱
- **现象**：新建会话后刷新页面，「生成检查清单」按钮错误出现
- **根因**：旧实现 `save_new_session` 用 `graph.invoke` 落盘初始状态——真的跑了图，空 PRD 节点把 phase 推进到 `prd_uploaded` 持久化
- **方案**：创建会话只落盘不跑图：`graph.update_state(config, state, as_node=START)`

### 2.2 update_state 必须显式 as_node=START
- **现象**：已有线程（尤其带 pending 中断）上调 update_state 报 `Ambiguous update, specify as_node`
- **根因**：缺省时 langgraph 推断「最后写者」，多写者时歧义
- **方案**：所有外部状态写入统一 `as_node=START`——START 的 input writers 可透传任意通道字段，且不影响 pending 中断。其他节点名会按 writers 过滤字段（静默丢数据，不报错！）

### 2.3 Send 并行写必须用 reducer 通道
- **现象**：并行 fan-out 时 `InvalidUpdateError: Can receive only one value per step`
- **方案**：并发写的通道用 `Annotated[list, operator.add]`；`add_messages` 按消息 id 去重天然安全

### 2.4 子图共享 reducer 通道的重复累积
- **现象**：子图输出回写父图时，继承值被父图 reducer 再套一次 → 数据重复
- **方案**：与父图共享的 reducer 通道在**子图 schema 里声明为普通 LastValue**，节点只返回本项贡献

### 2.5 子图不得声明与父图共享的 LastValue 通道（并行场景）
- **现象**：并行子图回写共享 LastValue 通道报 `InvalidUpdateError`
- **方案**：只读上下文用子图专用通道名（如 `batch_id`），父图无此通道 → 回写自动丢弃

### 2.6 删除线程后 get_state 返回空快照而非 None
- **现象**：删除会话后 GET 仍返回 200（空状态对象）
- **根因**：langgraph 1.2 对不存在的线程返回 `StateSnapshot(values={})`，不是 None
- **方案**：`get_session_state` 判空加 `not checkpoint.values`

### 2.7 checkpoints 表没有 created_at 列
- **现象**：会话列表按 `MAX(created_at)` 排序报 `UndefinedColumn`
- **方案**：`checkpoint_id` 是 uuid6（时间有序），用 `MAX(checkpoint_id)` 排序

### 2.8 条件边 Literal 注解不能含 'END'
- **现象**：router 返回类型注解里写 'END' 编译报 unknown target
- **方案**：Literal 只注解真实节点名，END 以值返回

### 2.9 fresh run 会丢弃 pending 中断
- **根因**：`invoke(input={dict})` 永远从 START 全新执行；只有 `invoke(None)` 才恢复中断
- **利用**：本项目「路由器模式」——所有操作走 input-only invoke，pending 自动丢弃，review_list 是纯屏障节点。**代价**：将来引入 interrupt/resume 时，「审核/执行中断期间的其他操作」会踩掉 pending，需要加守卫

### 2.10 主图状态漏通道 → 子图输出静默丢失
- **现象**：report_agent 执行了（消息说报告已生成）但 `report_content` 为空
- **根因**：MainGraphState 漏声明 `report_content`，子图回写被丢弃（仅警告不报错）
- **方案**：新增子图输出字段时**先检查父图 schema**；E2E 断言字段非空能兜住这类问题

## 3. Windows / uvicorn / 进程

### 3.1 TaskStop 杀不掉子进程（幽灵端口）
- **现象**：后台任务已停止，端口仍被占用；`tasklist` 查不到 netstat 显示的 PID；请求仍被旧代码响应
- **根因**：Windows 下杀掉 bash 包装进程后，python/uvicorn 子进程存活；socket 归属显示在已死父进程上
- **方案**：停任务后必须 `netstat -ano | grep :PORT` 验证，残留用 `taskkill //F //PID`；可用 PowerShell `Get-NetTCPConnection -LocalPort PORT` 找真实 OwningProcess

### 3.2 reload 模式 + 长 SSE 流 = 进程假死
- **现象**：改文件触发 reload 后旧进程关不掉、端口被占、请求无响应（health 超时）
- **根因**：旧进程 lifespan 关闭时等待未结束的 SSE worker（LLM 挂起时永远等不到）
- **方案**：LLM 超时（1.6）+ 池关闭超时（2.6 防复发）；开发期建议 `DEBUG=false` 手动重启

### 3.3 端口占用报 10013 而非 10048
- **现象**：`WinError 10013 以一种访问权限不允许的方式做了一个访问套接字的尝试`
- **根因**：端口被其他进程独占（Windows 下绑定失败的可能表现）
- **方案**：先查谁占了 8000，杀掉再启动

### 3.4 同步图执行不能在 async 端点直接调用
- **现象**：上传 PRD 期间 `/api/sessions` 一直 pending、health 也超时（事件循环被 LLM 调用堵死 30~70s）
- **方案**：非 SSE 端点全部用同步 `def`（FastAPI 自动线程池执行）；SSE 端点 async + worker 线程 + `asyncio.Queue`/`call_soon_threadsafe` 桥接。**规则：async 端点里不出现任何同步阻塞调用（DB/LLM/图）**

### 3.5 每会话互斥锁（同 session 并发执行）
- **现象**：同一 session 两个并发请求跑图 → checkpoint 竞争、消息丢失
- **方案**：`_session_locks` 按 session_id 分锁；SSE 端点非阻塞 acquire（409），def 端点阻塞 acquire（超时 5 分钟 409）——读-改-写全程持锁

## 4. FastAPI 并发模型

### 4.1 UploadFile 在同步 def 端点里的读取
- **现象**：`await file.read()` 在 def 端点不可用
- **方案**：同步端点用 `file.file.read()`

### 4.2 SSE 心跳格式
- **方案**：聊天流用注释行 `: ping\n\n`（前端解析器忽略非 data: 行）；执行流用 `event: ping\ndata: {}\n\n`（前端跳过 `{}`）。15s 间隔防代理超时

## 5. 前端状态管理

### 5.1 phase 双写脱节（最大坑源）
- **现象**：刷新后按钮/面板状态与后端不一致
- **根因**：前端本地 setPhase 与后端 checkpoint phase 各写各的
- **方案**：**phase 唯一来源 = 后端 checkpoint**。前端操作成功后 `load()` 回刷，删除所有本地硬编码 setPhase

### 5.2 删除当前会话 UI 不切换
- **现象**：删除当前选中会话后 ChatPanel/RightPanel 还显示旧内容
- **方案**：deleteSession 判断 `sid === sessionId` → 自动加载剩余最新会话 / 清空状态

### 5.3 刷新后报告统计归零
- **现象**：刷新页面后 通过/失败 卡片显示 0、通过率 N/A
- **方案**：`load()` 恢复时从 `check_results` 重新统计（与 SSE done 事件同一算法）

### 5.4 重复按钮
- **现象**：「开始检查」（ChatPanel）与「确认并开始检查」（ChecklistView）同阶段同时出现、功能相同
- **方案**：职责划分——ChecklistView 按钮管「审核确认+首次执行」（sop_generated），ChatPanel 按钮改为「重新检查」（completed 阶段重跑）

## 6. minium（微信小程序自动化）

### 6.1 服务端口必须手动开启
- **现象**：连接报 `IDE service port disabled`（CLI 的交互确认 `y` 在工具已运行时无效）
- **方案**：开发者工具 → 设置 → 安全设置 → 服务端口 → 开启。联调前置步骤写进 README

### 6.2 API 位置与文档直觉不符（1.6.0 实测）
- **现象**：`'App' object has no attribute 'element_is_exists'`；`'App' object has no attribute 'screenshot'`
- **根因**：`element_is_exists` 定义在 `App.CurrentPage`（即 `mini.page`）上，**不在 App 本体**；截图 API 是 `app.screen_shot(save_path=...)`（不是 `screenshot`）
- **方案**：改工具前先 `grep` 安装版源码确认定义所在的类；`get_all_pages_path()` 在 App 上可用于页面发现

### 6.3 minium 原生导航的路径契约（2026-08-18 修正结论）
- **现象**：`app.navigate_to/switch_tab` 抛 `MiniCommandError: Uncaught [object Object]`，曾误判为 hookNavigation 与运行时冲突
- **根因（修正）**：是**参数契约**，不是 hook 冲突——①两个 API 的页面路径都必须 `/` 开头（无前缀会被该 app 的 wx 代理按当前页目录解析 → 页面不存在）；②`navigate_to` 只能导航普通页、`switch_tab` 只能导航 tabBar 页，类型错配直接报错
- **方案**：工具层归一化 `"/" + path.lstrip("/")` 保证恰好一个前导斜杠；工具 docstring 明确告知 LLM 按页面类型选工具。曾用 evaluate 直调 wx API 绕过（见 6.7/6.8），确认契约后已回归原生方法（evaluate 方案已删除）

### 6.4 页面路由的 .html 后缀与前导斜杠（2026-08-18 修正：.html 是误判）
- **现象**：部分导航返回失败/不可读异常
- **误判**：曾认为是路由带 `.html` 后缀 + 不接受前导斜杠
- **修正**：真实路由无 `.html` 后缀（`get_all_pages_path` 返回的就是注册路由）；真正的坑是 6.3 的路径契约——**带前导斜杠的原生调用直接可用**，`.html` 兜底逻辑已随 evaluate 方案一起删除

### 6.5 ⚠️ 未解决：元素查询对 Taro 类运行时超时
- **现象**：`page.element_is_exists/get_element/get_text` 对任何选择器（page/view/input/button）都超时（`receive from remote timeout`）或返回 False；渲染器是默认 webview（非 Skyline）
- **现状**：导航（6.3 方案）与截图（`app.screen_shot`）在真实环境已验证可用；元素查询是 minium 页面同步引擎与该 app 运行时的深水区兼容问题，待专项排查
- **备选方向**：① `app.evaluate` 直查 DOM（webview 渲染下 document.querySelector 可用）；② xpath 选择器；③ 绕过选择器断言直接走 **Qwen 视觉分析**（截图判定，本就是下一步计划，天然规避此问题）

### 6.4 单 DevTools 实例约束
- **根因**：一个开发者工具同时只能一个自动化会话
- **方案**：执行从并行 fan-out 改为**串行循环**（exec_cursor 游标 + 条件边自循环）；minium_session 全局锁串行化跨会话操作；连接类异常自动废弃会话下次重建

### 6.5 工具异常要转成可见文本回喂 agent
- **经验**：agent 循环里工具抛异常不直接中断，转成 `"[工具执行失败: name] {e}"` 的 ToolMessage——LLM 能据此调整策略；这些文本也成了真机联调时的「调试日志」（本次两个 bug 都是从判定详情里看出来的）

### 6.6 空 run_id 未命中缓存 → 每次工具调用重建会话
- **现象**：每个工具调用都重新连接/拉起微信开发者工具，单项执行极慢、DevTools 被反复连断
- **根因**：`execute()` 调 `get_session()` 没透传 run_id（默认 `""`），缓存命中条件 `run_id and run_id == _session_run_id` 对空串永远为假 → 每次 `_dispose_locked()` + `minium.Minium()` 重建
- **方案**：命中条件放宽为 `not run_id or run_id == _session_run_id`（空值复用现有）；`execute` 透传 run_id；`minium_tools._run` 从线程本地 `_ctx` 带当前 run_id。生命周期：同 run 复用、新 run 重建一次、连接类异常自动重建

### 6.7 evaluate 同步返回 ok ≠ 导航成功（wx 异步回调被忽略）
- **现象**：navigate_to 工具返回「已导航到 X」，但开发者工具页面纹丝不动
- **根因**：wx.navigateTo/switchTab 是异步 API，成功/失败都走回调；evaluate 函数体同步 `return 'ok'` 只代表调用已发出。url 未注册（如真实路由缺 .html 后缀）或误用 navigateTo 跳 tabBar 页时，失败发生在 fail 回调里——不接回调就静默「成功」，连 `.html` 重试逻辑都被架空（第一候选永远“成功”）
- **方案**：派发时接 success/fail 回调写全局标记 → sleep 等落定 → 二次 evaluate 读标记；fail 的 errMsg 文本回喂 agent（可换路径 / 换 switch_tab 工具重试）

### 6.8 相对路径解析规则 + tabBar 限制（真机探针验证）
- **现象**：导航 fail errMsg 为 `page "pages/chatPage/pages/giftPage/index" is not found`——传 `pages/giftPage/index`，运行时却按 `pages/chatPage/` 前缀解析；换路径后报 `can not navigateTo a tabbar page`
- **根因**：①该 app 的自定义 wx 代理把**无前缀相对 url 按当前页目录**解析（不是微信原生的根目录解析）；②目标页是 tabBar 页，navigateTo 被微信规则拒绝，必须 switchTab（本项目 4 个页面全是 tabBar 页）
- **结局（2026-08-18）**：两条根因最终定位为 minium 原生 API 的路径契约（见 6.3），工具已回归原生方法。本条的探针方法保留价值：`backend/probe_nav.py` 可用于真机复测与后续元素查询排查

## 7. 结构化输出

### 7.1 with_structured_output 不支持裸 list
- **现象**：`with_structured_output(list[Feature])` 报错
- **方案**：容器模型包裹（`FeatureList(features: list[Feature])` / `CheckItemList` / `CheckResult`）

### 7.2 prompt 约束 JSON 的失败模式
- **现象**（改造前）：围栏、前后废话、截断、字段漂移、中文键名、思考内容混入——概率性失败且难兜
- **方案**：function_calling 结构化输出（API 级 schema 强制）为主路径；prompt + 健壮解析（剥围栏/字段修补/容器兜底）为降级路径。两层都失败才兜底单条

## 8. 测试与调试

### 8.0 配置面漂移：.env.example 与 config.py 脱节
- **现象**：.env.example 里是旧版变量（LLM_PROVIDERS/LLM_TASK_*），config.py 实际读取的变量（DATABASE_URL/MINIUM_* 等）反而缺失——新环境照抄模板会配错
- **方案**：config.py 新增/改名配置变量时**同步更新 .env.example**；可用 grep 对比校验：
  `grep -oE '"[A-Z_]+"' core/config.py` vs `grep -oE '^[A-Z_]+' .env.example`

### 8.1 环境相关测试的隔离
- **坑**：`.env` 填了真实 minium 配置后，单元测试会意外连上真实 DevTools（副作用 + 慢）
- **方案**：fake_session fixture monkeypatch `is_available`/`execute`；环境检测逻辑单独测（monkeypatch settings 属性 + sys.modules）；真实 LLM 用例无 API key 时 skip

### 8.2 LLM 行为的随机性会让集成测试时好时坏
- **坑**：agent 第一轮可能只输出计划不调工具就停下 → 测试断言工具调用数失败
- **方案**：循环加 nudge（无工具调用且总调用为 0 时催促「请直接调用工具」）；测试断言放宽到「app 或 page 任一有调用记录」

### 8.3 E2E 前先杀干净旧实例
- **坑**：残留旧代码实例响应请求 → 测试「通过」但测的不是新代码（本项目至少踩了 3 次）
- **方案**：每次 E2E 前 `netstat` 验证端口归属 + 确认进程启动时间；后台服务统一用 `DEBUG=false` 手动管理

## 9. automator / sidecar（小程序自动化桥）

> 2026-08-18 起弃用 minium（元素查询对 Taro 运行时超时无解，见 6.5），改官方 miniprogram-automator（Node）：
> sidecar/ 目录 HTTP 服务持官方包 + 唯一 DevTools 自动化连接，Python 后端走 httpx 调用。

### 9.1 automator.launch 官方实现在 Windows 新 Node 上 spawn cli.bat 报 EINVAL
- **现象**：`automator.launch({cliPath: "E:/.../cli.bat"})` 报 `Failed to launch wechat web devTools, please make sure cliPath is correctly specified`；最小复现 `spawn("cli.bat")` 直接抛 `spawn EINVAL`（Node v24 实测）
- **根因**：Windows 下 Node 的 child_process 不能直接执行 .bat（CreateProcess 不认批处理），官方 Launcher 裸 spawn 且不支持 shell 选项
- **方案**：sidecar 不用 automator.launch，自实现 launch（见 9.2/9.3）：spawn DevTools 自带 node.exe 跑 cli.js + 轮询 `automator.connect({wsEndpoint})`

### 9.2 经 cmd.exe 执行 cli.bat 有中文路径 GBK 坑
- **现象**：`spawn(comspec, ['/c', 'E:/微信web开发者工具/cli.bat', ...])` 报「系统找不到指定的路径」（stderr 是 GBK，显示为乱码 `ϵͳ�Ҳ���ָ����·����`）；bash 手动 `cmd //c` 同样命令却通
- **根因**：cmd.exe 解析 Unicode 参数时按控制台代码页（GBK）转换，中文路径经 Node spawn → cmd 传递被转坏
- **方案**：绕开 bat 和 cmd 两层——cli.bat 本体只是 `"%~dp0.\node.exe" "%~dp0.\cli.js" %*` 的包装，直接 `spawn(cli目录/node.exe, [cli目录/cli.js, ...])`，编码雷全消

### 9.3 CLI auto 命令 fire-and-forget，stderr 是 GBK
- **现象**：`cli auto --project <路径> --auto-port <端口>` 很快 EXIT 0（DevTools 后台继续跑），stdout 为空；stderr 有进度输出但 GBK 乱码
- **根因**：CLI 只负责拉起/复用 IDE 实例并开启自动化端口，自身不驻留；stderr 按系统代码页输出。DevTools 已在跑时会复用实例（输出 `IDE may already started ... trying to connect`）
- **方案**：spawn 后不看退出码判成败，轮询连 `ws://127.0.0.1:<port>`（`automator.connect` 成功即 launch 成功）；stderr 仅捕获用于超时报错兜底（乱码不影响判断）

### 9.4 Git Bash 的 curl 发中文请求体是 GBK 编码
- **现象**：curl 发含中文路径的 JSON（如 cliPath）到 sidecar，服务端按 UTF-8 解析出乱码（`微信web开发者工具` → `΢��web�����߹���`）→ 路径不存在 → 间接报 `spawn EINVAL`；node fetch / httpx 发同样内容完全正常
- **根因**：Git Bash 下 curl 的命令行参数按 Windows 控制台代码页（GBK）发出，不是 UTF-8
- **方案**：**测试工具陷阱，非代码问题**——验证 sidecar 一律用 node fetch 或 httpx（真实客户端就是 httpx，天然 UTF-8）。调试时看到「路径不存在/找不到」类报错，先怀疑编码再看路径

### 9.5 导航类型错配会抛未捕获异常并断开自动化通道
- **现象**：`switchTab` 跳非 tabBar 页（或 `navigateTo` 跳 tabBar 页）报 `Uncaught [object Object]`，随后整个自动化连接 `Connection closed`（后续所有调用全挂）
- **根因**：该 app 的 wx 代理对契约违例直接抛未捕获异常，小程序 JS 上下文崩溃 → IDE 关闭自动化通道；自动化端口本身还活着（重连可用），但连接已死
- **方案**：sidecar `/navigate` 调 wx 前按 app.json 预校验（switchTab↔tabBar 列表、navigateTo↔已注册页面、类型↔页面类别），契约违例挡在调用前返回友好文本；底层 ws 断开由 `watchDisconnect` 检测 + `ensureMini` 下次调用自动重连兜底

### 9.6 导航的固定等待不靠谱：首访慢加载 + 初始化吞调用
- **现象**：① automator 的 changeRoute 固定 sleep 3s 后读落地页，Taro 页面首访加载超 3s → 返回旧页（导航实际成功了，只是读早了）；② launch 后第一次导航调用被 app 静默吞掉（初始化未就绪，无 success 也无 fail 回调），轮询 15s 页面纹丝不动，但稍后重试立即成功
- **根因**：① 固定等待没有落地确认；② app 初始化窗口内的 wx 调用静默丢失
- **方案**：sidecar 自实现 `navigateAndWait`：每 5s 重发一次调用 + 轮询 currentPage 直到落地目标页（20s 总超时）；`navigateTo` 重发前查 `pageStack`——目标已在栈中（加载中）则只等不重复 push，避免页面栈堆重复页
