"""minium 工具集 — 供 executor Agent 调用的微信小程序自动化工具。

架构：工具只依赖 minium_session 抽象层，与 executor 无耦合——
未来抽成独立 MCP server 时本文件原样搬走。
"""

import threading
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from ..core.config import get_settings
from . import minium_session

from rich import print as rPrint

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


def _run(action) -> Any:
    return minium_session.execute(action, run_id=getattr(_ctx, "run_id", ""))


# ──────────────────────────────────────────────
# 工具定义（中文 docstring 供 LLM 决策调用）
# ──────────────────────────────────────────────

def _normalize_path(page_path: str) -> str:
    """剥光所有前导斜杠。原生导航 API 的路径契约要求以 / 开头（PITFALLS 6.3），
    调用方用 "/" + _normalize_path(x) 保证恰好一个前导斜杠。"""
    return page_path.lstrip("/")


@tool
def navigate_to(page_path: str) -> str:
    """导航到指定普通页面用这个工具，如果是tabBar页面用switch_tab工具。page_path: 页面路径（如 pages/index/index，可带或不带前导斜杠）。失败时返回可读错误文本。"""
    def act(s):
        s.app.navigate_to("/" + _normalize_path(page_path), None)
        return 'navigate_to跳转成功'

    res = _run(act)

    rPrint(f"[bold red]==========工具调用结果realy-navigate_to==========[/bold red]")
    rPrint(res)
    rPrint(f"[bold red]==========工具调用结果realy-navigate_to==========[/bold red]")

    return res


@tool
def switch_tab(tab_path: str) -> str:
    """切换到指定 tabBar 页面（只能 tabBar 页）。tab_path: tabBar 页面路径，可带或不带前导斜杠。失败时返回可读错误文本。"""
    def act(s):
        # 归一化：无论 LLM 传 /pages/x、pages/x 还是 //pages/x，保证恰好一个前导斜杠
        s.app.switch_tab("/" + _normalize_path(tab_path))
        return 'switch_tab跳转成功'

    res = _run(act)

    rPrint(f"[bold red]==========工具调用结果-navigate_to==========[/bold red]")
    rPrint(res)
    rPrint(f"[bold red]==========工具调用结果-navigate_to==========[/bold red]")

    return res


@tool
def tap(selector: str, inner_text: str = "", max_timeout: int = 5) -> str:
    """点击页面元素。selector: 元素选择器（WXSS 选择器或 XPath）；inner_text: 可选，元素文本精确匹配；max_timeout: 等待元素出现的秒数。"""
    def act(s):
        el = s.page.get_element(selector, inner_text=inner_text, max_timeout=max_timeout)
        el.click()
        return f"已点击 {selector}"

    return _run(act)


@tool
def input_text(selector: str, text: str, max_timeout: int = 5) -> str:
    """向输入框输入文本。selector: 输入框选择器；text: 要输入的文本；max_timeout: 等待元素出现的秒数。"""
    def act(s):
        el = s.page.get_element(selector, max_timeout=max_timeout)
        el.input(text)
        return f"已向 {selector} 输入 {text!r}"

    return _run(act)


@tool
def get_text(selector: str, max_timeout: int = 5) -> str:
    """获取元素文本内容。selector: 元素选择器；max_timeout: 等待元素出现的秒数。"""
    def act(s):
        el = s.page.get_element(selector, max_timeout=max_timeout)
        text = getattr(el, "inner_text", None)
        if text is None:
            text = el.text  # 版本差异兜底
        return str(text)

    return _run(act)


@tool
def element_exists(selector: str, inner_text: str = "", max_timeout: int = 5) -> bool:
    """检查元素是否存在。selector: 元素选择器；inner_text: 可选，元素文本精确匹配；max_timeout: 等待秒数。"""
    def act(s):
        # minium 1.6: element_is_exists 在 App.CurrentPage（即 mini.page）对象上，
        # 不在 App 本体上（App 上调用报 AttributeError）
        return bool(s.page.element_is_exists(
            selector=selector,
            max_timeout=max_timeout,
            inner_text=inner_text or None,
        ))

    return _run(act)


@tool
def get_pages() -> list:
    """获取小程序已配置的所有页面路径（用于发现真实页面，导航前先查）。"""
    def act(s):
        return list(s.app.get_all_pages_path())
    
    res = _run(act)

    rPrint(f"[bold blue]==========工具调用结果-get_pages==========[/bold blue]")
    rPrint(res)
    rPrint(f"[bold blue]==========工具调用结果-get_pages==========[/bold blue]")

    return res


@tool
def screenshot(name: str) -> str:
    """对当前页面截图存档。name: 截图名称（不含扩展名），返回存档文件名。"""
    def act(s):
        session_id = getattr(_ctx, "session_id", "unknown")
        run_id = getattr(_ctx, "run_id", "unknown")
        item_id = getattr(_ctx, "item_id", "unknown")
        base = Path(get_settings().SESSIONS_DIR) / "screenshots" / session_id / run_id / item_id
        base.mkdir(parents=True, exist_ok=True)
        abs_path = base / f"{name}.png"
        _capture(s, str(abs_path))
        return abs_path.name

    return _run(act)


def _capture(session: Any, abs_path: str) -> None:
    """截图落盘（minium 1.6: app.screen_shot；版本差异兜底：keyword → bytes）。"""
    app = session.app
    try:
        app.screen_shot(save_path=abs_path)
    except TypeError:
        data = app.screen_shot()
        if isinstance(data, (bytes, bytearray)):
            Path(abs_path).write_bytes(bytes(data))
        else:
            raise


EXECUTOR_TOOLS = [navigate_to, switch_tab, tap, input_text, get_text, element_exists, get_pages, screenshot]
TOOL_MAP = {t.name: t for t in EXECUTOR_TOOLS}
