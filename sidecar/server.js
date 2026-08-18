/**
 * sop-agent sidecar — miniprogram-automator 的 HTTP 封装。
 *
 * 后端 FastAPI 通过本地 HTTP 调用本服务；本服务持有唯一的微信开发者工具
 * 自动化连接（全局单例 miniProgram，自动化端口只允许一个客户端）。
 * 手动启动：node server.js（默认 127.0.0.1:9310，可用 SIDECAR_PORT 覆盖）。
 *
 * 协议约定：请求/响应均为 JSON；成功 {"ok": true, ...}，失败 {"ok": false, "error": "..."}。
 */
'use strict'

const http = require('http')
const fs = require('fs')
const path = require('path')
const cp = require('child_process')
const automator = require('miniprogram-automator')

const PORT = 9310
const HOST = '127.0.0.1'

// 全局单例：与微信开发者工具的唯一自动化连接
let mini = null
let miniInfo = null // 当前连接的来源信息（同 lastConnect；断连时清空，重连后恢复）
let lastConnect = null // 最近一次连接参数（自动重连依据）

// ──────────────────────────────────────────────
// 工具函数
// ──────────────────────────────────────────────

function readJson(req) {
  return new Promise((resolve, reject) => {
    let body = ''
    req.on('data', (chunk) => (body += chunk))
    req.on('end', () => {
      if (!body) return resolve({})
      try {
        resolve(JSON.parse(body))
      } catch (e) {
        reject(new Error('请求体不是合法 JSON: ' + e.message))
      }
    })
    req.on('error', reject)
  })
}

function sendJson(res, status, obj) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' })
  res.end(JSON.stringify(obj))
}

function ok(res, data = {}) {
  sendJson(res, 200, { ok: true, ...data })
}

function fail(res, status, err) {
  sendJson(res, status, { ok: false, error: String((err && err.message) || err) })
}

// 包装 async handler：异常统一转 500 JSON（错误文本会透传到后端 ToolMessage 回喂 LLM）
function route(handler) {
  return async (req, res) => {
    try {
      await handler(req, res)
    } catch (e) {
      console.error('[sidecar] 端点异常:', e && e.stack ? e.stack : e)
      fail(res, 500, e)
    }
  }
}

// ──────────────────────────────────────────────
// 连接管理
// ──────────────────────────────────────────────

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// 自己实现 launch：automator.launch 在 Windows 上直接 spawn cli.bat 会 EINVAL
// （Node 不能直接执行 .bat）；经 cmd.exe 转一道又有中文路径 GBK 编码坑
// （实测报「系统找不到指定的路径」）。cli.bat 本体只是 node.exe + cli.js 的包装，
// 所以直接用 DevTools 自带的 node.exe 跑 cli.js。
// CLI 是 fire-and-forget（automator 官方也是 spawn 后不交互、只轮询 ws 端口），
// stderr 捕获用于报错。
async function launchDevTools({ cliPath, projectPath, port = 9420, account = '', timeout = 60000 }) {
  if (!cliPath) throw new Error('launch 模式需要 cliPath（cli.bat 路径）')
  const args = ['auto', '--project', projectPath, '--auto-port', String(port)]
  if (account) args.push('--auto-account', account)

  const dir = path.dirname(cliPath)
  const nodeExe = path.join(dir, 'node.exe')
  const cliJs = path.join(dir, 'cli.js')
  let cmd, cmdArgs
  if (process.platform === 'win32' && fs.existsSync(nodeExe) && fs.existsSync(cliJs)) {
    cmd = nodeExe
    cmdArgs = [cliJs, ...args]
  } else {
    cmd = cliPath // Mac 等平台 cli 是可直接执行的二进制
    cmdArgs = args
  }

  let stderr = ''
  const child = cp.spawn(cmd, cmdArgs, { stdio: ['ignore', 'ignore', 'pipe'] })
  child.stderr.on('data', (chunk) => (stderr += chunk))
  child.on('error', (e) => {
    // spawn 同步失败（路径不存在等）：让轮询循环提前终止
    stderr += `[spawn 失败] ${e.message}`
  })

  // 轮询连接自动化端口，直到成功或超时
  const wsEndpoint = `ws://127.0.0.1:${port}`
  const deadline = Date.now() + timeout
  let m = null
  while (Date.now() < deadline && !m) {
    try {
      m = await automator.connect({ wsEndpoint })
    } catch (e) {
      // DevTools 还没起来，继续等
      await sleep(1000)
    }
  }
  if (!m) {
    const detail = stderr.trim() ? `，CLI 输出: ${stderr.trim()}` : ''
    throw new Error(`连接 ${wsEndpoint} 超时（${timeout}ms）${detail}`)
  }

  // 二阶段稳定：IDE 重武装自动化服务会干掉刚建立的连接（automator 官方
  // launch 靠连接后 sleep 5s 规避此竞态），等落定后探活，死了就重连
  for (let attempt = 0; ; attempt++) {
    await sleep(attempt === 0 ? 5000 : 2000)
    if (Date.now() > deadline) {
      throw new Error(`连接 ${wsEndpoint} 未稳定（重武装竞态持续到超时）${stderr.trim() ? `，CLI 输出: ${stderr.trim()}` : ''}`)
    }
    try {
      await m.currentPage() // 探活
      return m
    } catch (e) {
      m = null
      try {
        m = await automator.connect({ wsEndpoint })
      } catch (e2) {
        m = null
      }
    }
  }
}

async function connectOrLaunch(body) {
  // 换连接前先断开旧连接（自动化端口只允许一个客户端）
  if (mini) {
    await mini.disconnect()
    mini = null
  }
  const { wsEndpoint, projectPath, cliPath, port, account } = body
  if (wsEndpoint) {
    // 连已开启自动化端口的 DevTools（设置 → 安全设置 → 服务端口）
    mini = await automator.connect({ wsEndpoint })
    lastConnect = { wsEndpoint }
  } else if (projectPath) {
    // 自动拉起 DevTools（launch 自实现，见 launchDevTools）
    lastConnect = {
      cliPath,
      projectPath,
      port: port || 9420,
      account: account || '',
    }
    mini = await launchDevTools(lastConnect)
  } else {
    throw new Error('需要 wsEndpoint（连接已开启自动化的 DevTools）或 projectPath（自动拉起 DevTools）')
  }
  miniInfo = lastConnect
  watchDisconnect(mini)
  return miniInfo
}

// 监听底层 ws 断开：置空单例，下次调用走 ensureMini 自动重连
function watchDisconnect(m) {
  const ws = m.connection && m.connection.transport && m.connection.transport.ws
  if (!ws) return
  ws.on('close', () => {
    if (mini === m) {
      mini = null
      miniInfo = null
      console.error('[sidecar] DevTools 自动化连接已断开（下次调用自动重连）')
    }
  })
}

async function ensureMini() {
  if (mini) return mini
  if (!lastConnect) throw new Error('尚未连接微信开发者工具，请先调用 /launch 或 /connect')
  console.error('[sidecar] 自动重连 DevTools ...')
  if (lastConnect.wsEndpoint) {
    mini = await automator.connect({ wsEndpoint: lastConnect.wsEndpoint })
  } else {
    mini = await launchDevTools(lastConnect)
  }
  miniInfo = lastConnect
  watchDisconnect(mini)
  return mini
}

// app.json 信息缓存（projectPath → {mtimeMs, info}），mtime 变化自动失效
const appJsonCache = new Map()

function getPageInfo(projectPath) {
  if (!projectPath) return null
  const appJsonPath = path.join(projectPath, 'app.json')
  let mtimeMs = 0
  try {
    mtimeMs = fs.statSync(appJsonPath).mtimeMs
  } catch (e) {
    return null // app.json 不存在
  }
  const cached = appJsonCache.get(projectPath)
  if (cached && cached.mtimeMs === mtimeMs) return cached.info
  const app = JSON.parse(fs.readFileSync(appJsonPath, 'utf8'))
  const pages = app.pages || []
  const subPages = (app.subPackages || app.subpackages || []).flatMap(
    (pkg) => (pkg.pages || []).map((p) => `${pkg.root}/${p}`)
  )
  const info = {
    pages: [...pages, ...subPages],
    tabBarPages: ((app.tabBar && app.tabBar.list) || []).map((i) => i.pagePath),
  }
  appJsonCache.set(projectPath, { mtimeMs, info })
  return info
}

// 导航类型↔页面类别预校验：契约违例挡在调用前（PITFALLS 9.5）
function validateNavigation(projectPath, type, url) {
  const info = getPageInfo(projectPath)
  if (!info) return // 无 app.json 信息（纯 connect 模式）时跳过校验
  const clean = url.split('?')[0].replace(/^\/+/, '')
  const isTab = info.tabBarPages.includes(clean)
  if (type === 'switchTab' && !isTab) {
    throw new Error(`switchTab 只能跳 tabBar 页面，${clean} 不在其中（tabBar: ${info.tabBarPages.join(', ')}）`)
  }
  if (type === 'navigateTo' && isTab) {
    throw new Error(`${clean} 是 tabBar 页面，navigateTo 不能跳，请改用 switchTab`)
  }
  if (type === 'navigateTo' && !info.pages.includes(clean)) {
    throw new Error(`${clean} 不是已注册页面（已注册: ${info.pages.join(', ')}）`)
  }
}

// 导航 + 重发 + 轮询落地：不用 automator 的 changeRoute（固定 3s 等待，
// Taro 页面首访加载超 3s 会读到旧页；且 app 初始化未就绪时会静默吞掉
// 导航调用——见 PITFALLS 9.6），改为「每 5s 重发一次 + 轮询落地」。
// navigateTo 重发前查页面栈：目标已在栈中（加载中）则只等不重复 push。
async function navigateAndWait(mini, type, url, timeout = 20000) {
  const target = url.split('?')[0].replace(/^\/+/, '')
  const deadline = Date.now() + timeout
  let lastPath = ''
  let first = true
  while (Date.now() < deadline) {
    if (first || type === 'switchTab') {
      await mini.callWxMethod(type, { url })
    } else {
      const stack = await mini.pageStack()
      if (!stack.some((p) => p.path === target)) {
        await mini.callWxMethod(type, { url })
      }
    }
    first = false
    const attemptDeadline = Math.min(deadline, Date.now() + 5000)
    while (Date.now() < attemptDeadline) {
      const page = await mini.currentPage()
      lastPath = page.path
      if (page.path === target) return page
      await sleep(500)
    }
  }
  throw new Error(`导航超时：目标 ${target}，${timeout}ms 后当前页仍在 ${lastPath}`)
}

// ──────────────────────────────────────────────
// 路由
// ──────────────────────────────────────────────

const routes = {
  // 健康检查：服务可达 + 是否已连 DevTools（backend 的 is_available 依据）
  'GET /health': route(async (req, res) => {
    ok(res, { connected: !!mini, info: miniInfo })
  }),

  // 连接已开启自动化端口的 DevTools
  'POST /connect': route(async (req, res) => {
    const info = await connectOrLaunch(await readJson(req))
    ok(res, { info })
  }),

  // 自动拉起 DevTools
  'POST /launch': route(async (req, res) => {
    const info = await connectOrLaunch(await readJson(req))
    ok(res, { info })
  }),

  // 断开自动化连接（不退出 DevTools；automator 的 close() 会 App.exit，这里刻意不用）
  'POST /close': route(async (req, res) => {
    if (mini) await mini.disconnect().catch(() => {})
    mini = null
    miniInfo = null
    lastConnect = null
    ok(res)
  }),

  // 当前页面（path + query）
  'GET /currentPage': route(async (req, res) => {
    const mini = await ensureMini()
    const page = await mini.currentPage()
    ok(res, { page: { path: page.path, query: page.query } })
  }),

  // 页面导航：navigateTo（普通页）/ switchTab（tabBar 页），返回落地页
  'POST /navigate': route(async (req, res) => {
    const { type, url, projectPath } = await readJson(req)
    if (!type || !url) throw new Error('缺少 type（navigateTo/switchTab）或 url 参数')
    if (type !== 'navigateTo' && type !== 'switchTab') {
      throw new Error(`不支持的导航类型: ${type}（支持 navigateTo/switchTab）`)
    }
    // 类型↔页面类别预校验：契约违例挡在调用前，避免 app 抛未捕获异常断掉自动化通道
    validateNavigation(projectPath || (miniInfo && miniInfo.projectPath), type, url)
    const mini = await ensureMini()
    const page = await navigateAndWait(mini, type, url)
    ok(res, { page: { path: page.path, query: page.query } })
  }),

  // 截图：path 为绝对路径（后端拼好存档路径）
  'POST /screenshot': route(async (req, res) => {
    const { path: filePath } = await readJson(req)
    if (!filePath) throw new Error('缺少 path 参数（截图存档绝对路径）')
    const mini = await ensureMini()
    await mini.screenshot({ path: filePath })
    ok(res, { path: filePath })
  }),

  // 已注册页面路径：静态读 app.json（含分包）。automator 无此 API——
  // pageStack/currentPage 只能看到「已打开」的页面；全量清单只能读
  // DevTools 注册页面的数据源 app.json（projectPath 由后端传入）
  'POST /pages': route(async (req, res) => {
    const { projectPath } = await readJson(req)
    if (!projectPath) throw new Error('缺少 projectPath 参数')
    const info = getPageInfo(projectPath)
    if (!info) throw new Error(`app.json 不存在: ${path.join(projectPath, 'app.json')}`)
    ok(res, { pages: info.pages })
  }),
}

// ──────────────────────────────────────────────
// 启动
// ──────────────────────────────────────────────

const server = http.createServer(async (req, res) => {
  const key = `${req.method} ${req.url.split('?')[0]}`
  const handler = routes[key]
  if (!handler) return sendJson(res, 404, { ok: false, error: `未知端点: ${key}` })
  await handler(req, res)
})

server.listen(PORT, HOST, () => {
  console.log(`[sop-agent sidecar] listening on http://${HOST}:${PORT}`)
  console.log('  POST /launch  {projectPath, cliPath, port?}     自动拉起微信开发者工具')
  console.log('  POST /connect {wsEndpoint}                      连接已开自动化端口的 DevTools')
  console.log('  GET  /health                                    健康检查（backend is_available 依据）')
  console.log('  GET  /currentPage                               当前页面 path/query')
  console.log('  POST /navigate {type, url}                      导航（navigateTo/switchTab），返回落地页')
  console.log('  POST /screenshot {path}                         截图存档')
  console.log('  POST /pages {projectPath}                       已注册页面路径（读 app.json）')
})
