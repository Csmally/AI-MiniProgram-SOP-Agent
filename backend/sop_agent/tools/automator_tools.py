"""automator 工具集 — 供 executor Agent 调用的微信小程序自动化工具（miniprogram-automator 协议）。

当前状态：逐步接入中 —— navigate_to/switch_tab/get_pages 已接 automator_session，
其余工具体仍为占位 NotImplementedError。工具面（工具名/参数/docstring）
与 minium_tools 完全一致，minium_tools.py 原样保留作 API 参考。

架构：与 executor 无耦合——未来抽成独立 MCP server 时本文件原样搬走。
"""

import threading

from langchain_core.tools import tool

from ..core.config import get_settings
from . import automator_session

# run context（thread-local）：executor 节点设置，screenshot 据此拼路径
_ctx = threading.local()


def set_run_context(session_id: str, run_id: str, item_id: str) -> None:
    _ctx.session_id = session_id
    _ctx.run_id = run_id
    _ctx.item_id = item_id


def clear_run_context() -> None:
    _ctx.session_id = ""
    _ctx.run_id = ""
    _ctx.item_id = ""


# ──────────────────────────────────────────────
# 工具定义（中文 docstring 供 LLM 决策调用）
# 实现待接入：tap 等剩余工具体仍为占位，接入实现后删除 raise 行
# ──────────────────────────────────────────────

def _normalize_path(page_path: str) -> str:
    """剥光所有前导斜杠。路径契约要求以 / 开头（PITFALLS 6.3：无前缀会被
    该 app 的 wx 代理按当前页目录解析），调用方用 "/" + _normalize_path(x)
    保证恰好一个前导斜杠。"""
    return page_path.lstrip("/")


@tool
def navigate_to(page_path: str) -> str:
    """导航到指定普通页面用这个工具，如果是tabBar页面用switch_tab工具。page_path: 页面路径（如 pages/index/index，可带或不带前导斜杠）。失败时返回可读错误文本。"""
    page = automator_session.navigate("navigateTo", "/" + _normalize_path(page_path))
    return f"普通页面导航跳转成功，当前页面: {page['path']}"


@tool
def switch_tab(tab_path: str) -> str:
    """切换到指定 tabBar 页面（只能 tabBar 页）。tab_path: tabBar 页面路径，可带或不带前导斜杠。失败时返回可读错误文本。"""
    page = automator_session.navigate("switchTab", "/" + _normalize_path(tab_path))
    return f"tabBar页面导航跳转成功，当前页面: {page['path']}"


@tool
def tap(selector: str, inner_text: str = "", max_timeout: int = 5) -> str:
    """点击页面元素。selector: 元素选择器（WXSS 选择器或 XPath）；inner_text: 可选，元素文本精确匹配；max_timeout: 等待元素出现的秒数。"""
    raise NotImplementedError("automator 接入中：tap 待实现（参考 minium_tools 同名工具）")


@tool
def input_text(selector: str, text: str, max_timeout: int = 5) -> str:
    """向输入框输入文本。selector: 输入框选择器；text: 要输入的文本；max_timeout: 等待元素出现的秒数。"""
    raise NotImplementedError("automator 接入中：input_text 待实现（参考 minium_tools 同名工具）")


@tool
def get_text(selector: str, max_timeout: int = 5) -> str:
    """获取元素文本内容。selector: 元素选择器；max_timeout: 等待元素出现的秒数。"""
    raise NotImplementedError("automator 接入中：get_text 待实现（参考 minium_tools 同名工具）")


@tool
def element_exists(selector: str, inner_text: str = "", max_timeout: int = 5) -> bool:
    """检查元素是否存在。selector: 元素选择器；inner_text: 可选，元素文本精确匹配；max_timeout: 等待秒数。"""
    raise NotImplementedError("automator 接入中：element_exists 待实现（参考 minium_tools 同名工具）")


@tool
def get_pages() -> list:
    """获取小程序已配置的所有页面路径（用于发现真实页面，导航前先查）。"""
    settings = get_settings()
    if not settings.AUTOMATOR_PROJECT_PATH:
        raise RuntimeError("AUTOMATOR_PROJECT_PATH 未配置，无法读取页面清单")
    return automator_session.get_pages(settings.AUTOMATOR_PROJECT_PATH)


@tool
def screenshot(name: str) -> str:
    """对当前页面截图存档。name: 截图名称（不含扩展名），返回存档文件名。"""
    raise NotImplementedError("automator 接入中：screenshot 待实现（参考 minium_tools 同名工具）")


EXECUTOR_TOOLS = [navigate_to, switch_tab, tap, input_text, get_text, element_exists, get_pages, screenshot]
TOOL_MAP = {t.name: t for t in EXECUTOR_TOOLS}
