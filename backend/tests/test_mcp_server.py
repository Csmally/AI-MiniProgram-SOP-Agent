"""MCP server 注册与上下文接线测试（fake minium 注入，直接调 server 进程内工具函数）。

不启动真实 HTTP 传输（FastMCP list_tools/get_tool 为协程，asyncio.run 包一层）；
工具行为复用 minium_tools 的实现（.func 裸函数），本文件只测 server 层接线。
"""

import asyncio
from types import SimpleNamespace

import pytest

from mcp_server import server
from mcp_server.tools import minium_tools

SAMPLE_WXML = """<page><view><button id="submit-btn">提交订单</button></view></page>"""

# 14 个执行工具 + 3 个 server 专属（set_run_context/is_minium_available/snapshot_app_state）
EXPECTED_TOOL_NAMES = {
    "navigate_to", "switch_tab", "navigate_back", "get_page_elements", "get_window_size",
    "page_scroll", "scroll_view", "tap", "input_text", "get_text",
    "element_exists", "get_pages", "screenshot", "analyze_screenshot",
    "set_run_context", "is_minium_available", "snapshot_app_state",
}


@pytest.fixture
def server_ctx():
    """注入 server 进程上下文 provider（测试后卸载，避免污染同进程其他测试）。"""
    server.install_context_provider()
    yield
    minium_tools.set_context_provider(None)
    server._server_ctx.update(session_id="", run_id="", item_id="")


def test_all_tools_registered():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert EXPECTED_TOOL_NAMES <= names


def test_set_run_context_feeds_screenshot_dir(fake_session, server_ctx, monkeypatch, tmp_path):
    """进程全局上下文 → provider → _shot_dir：截图落到 session/run/item 目录。"""
    monkeypatch.setattr(minium_tools, "get_settings",
                        lambda: SimpleNamespace(SESSIONS_DIR=str(tmp_path)))
    server.set_run_context("s1", "r1", "i1")
    out = minium_tools.screenshot.func("shot1")
    assert out == "shot1.png"
    assert (tmp_path / "screenshots" / "s1" / "r1" / "i1" / "shot1.png").exists()


def test_registered_tap_is_same_implementation(fake_session, server_ctx):
    """注册的 tap 与 minium_tools 直调同一实现：文本定位点击 fake 元素。"""
    fake_session.page.page_wxml = SAMPLE_WXML
    out = minium_tools.tap.func(selector="", inner_text="提交订单", max_timeout=5)
    assert "已点击" in out
    assert fake_session.page.elements["#submit-btn"].clicked is True


def test_is_minium_available_reflects_environment(fake_session):
    assert server.is_minium_available() is True


def test_is_minium_available_false_when_unavailable(minium_unavailable):
    assert server.is_minium_available() is False
