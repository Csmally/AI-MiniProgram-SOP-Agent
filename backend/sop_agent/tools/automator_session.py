"""automator sidecar 会话层 — 与 sidecar HTTP 服务通信的唯一入口抽象层。

设计要点：
- 所有小程序自动化操作经本地 HTTP 调 sidecar（官方 miniprogram-automator
  持有唯一 DevTools 自动化连接，sidecar/ 目录 node server.js 手动启动）；
- is_available() = sidecar /health 可达（executor 据此双模式降级桩）；
- 懒拉起：sidecar 尚未连接 DevTools 时，首个业务调用自动 /launch
  （AUTOMATOR_PROJECT_PATH 配置齐备才拉，否则抛可读错误）；
- 业务失败/网络异常统一转 AutomatorSidecarError 可读文本，工具层不吞异常，
  由 executor 循环转成 ToolMessage 回喂 LLM；
- 请求一律 UTF-8 JSON（PITFALLS 9.4：curl 的 GBK 坑只存在于 Git Bash 测试场景）。
"""

from typing import Any

import httpx

from ..core.config import get_settings

# is_available 探活要快（executor 每次进入前调）；业务请求要宽松
# （sidecar 内部的等待循环——如导航 20s 重发窗口——可能吃掉数秒）；
# launch 最慢（拉起 DevTools + 5s 落定探活，sidecar 侧上限 60s）。
_HEALTH_TIMEOUT = 2.0
_CALL_TIMEOUT = 30.0
_LAUNCH_TIMEOUT = 90.0


class AutomatorSidecarError(RuntimeError):
    """sidecar 调用失败（连接不通或业务错误）。"""


def is_available() -> bool:
    """sidecar 是否可达（executor 双模式降级桩的依据）。"""
    try:
        resp = httpx.get(
            f"{get_settings().AUTOMATOR_SIDECAR_URL}/health",
            timeout=_HEALTH_TIMEOUT,
        )
        return resp.status_code == 200 and resp.json().get("ok")
    except Exception:
        return False


def _post(path: str, body: dict, timeout: float = _CALL_TIMEOUT) -> dict:
    """POST JSON 并解析 {ok, ...} 响应，失败统一转可读异常。"""
    url = f"{get_settings().AUTOMATOR_SIDECAR_URL}{path}"
    try:
        resp = httpx.post(url, json=body, timeout=timeout)
    except httpx.HTTPError as e:
        raise AutomatorSidecarError(f"sidecar 不可达（{url}）: {e}") from e
    try:
        data = resp.json()
    except ValueError:
        raise AutomatorSidecarError(f"sidecar 返回非 JSON（HTTP {resp.status_code}）") from None
    if not data.get("ok"):
        raise AutomatorSidecarError(data.get("error") or f"HTTP {resp.status_code}")
    return data


def _is_not_connected_error(e: AutomatorSidecarError) -> bool:
    """sidecar 尚未连接 DevTools（可懒拉起补救）的错误。"""
    return "尚未连接" in str(e)


def _launch() -> None:
    """懒拉起：按配置调 sidecar /launch 拉起微信开发者工具（失败原样上抛）。"""
    settings = get_settings()
    if not settings.AUTOMATOR_PROJECT_PATH:
        raise AutomatorSidecarError(
            "sidecar 尚未连接微信开发者工具，且 AUTOMATOR_PROJECT_PATH 未配置无法自动拉起"
            "——请配置 AUTOMATOR_PROJECT_PATH / AUTOMATOR_CLI_PATH，或手动调 sidecar 的 /launch"
        )
    _post(
        "/launch",
        {
            "projectPath": settings.AUTOMATOR_PROJECT_PATH,
            "cliPath": settings.AUTOMATOR_CLI_PATH,
            "port": settings.AUTOMATOR_PORT,
        },
        timeout=_LAUNCH_TIMEOUT,
    )


def _call(path: str, **body: Any) -> dict:
    """通用调用：sidecar 未连接 DevTools 时懒拉起后重试一次。"""
    try:
        return _post(path, body)
    except AutomatorSidecarError as e:
        if _is_not_connected_error(e):
            _launch()
            return _post(path, body)
        raise


def navigate(nav_type: str, url: str) -> dict:
    """导航并返回落地页 {path, query}（sidecar 内部已等待页面落定）。"""
    return _call("/navigate", type=nav_type, url=url)["page"]


def get_pages(project_path: str) -> list:
    """已注册页面路径（sidecar 静态读 app.json 含分包，无需 DevTools 连接）。"""
    return _call("/pages", projectPath=project_path)["pages"]
