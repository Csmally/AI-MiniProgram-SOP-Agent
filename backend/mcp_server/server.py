"""minium 工具 MCP Server — 把 minium_tools 的 14 个自动化工具暴露为 MCP 工具。

架构：
- 工具实现单一事实来源在 minium_tools.py——注册其 @tool 的 .func 裸函数，
  MCP server 不重复实现任何工具逻辑；
- MCP server 进程独占 minium 会话单例 + 全局锁（单 DevTools 实例约束跨进程成立）；
- run context（session/run/item）存在进程全局（MCP 工具调用落在不同线程，
  线程局部变量无法跨调用传递），通过 minium_tools.set_context_provider 注入；
- 视觉模型（analyze_screenshot）由 server 进程内的 get_llm 直调，读同一份 .env。

启动（terminal 4）：
    uv run python -m mcp_server
传输：默认 streamable-http（后端 langchain-mcp-adapters 连接，端口 MCP_SERVER_PORT）；
MCP_TRANSPORT=stdio 可切 stdio 给 Claude Desktop / Claude Code 直接使用。
"""

import threading

from fastmcp import FastMCP
from fastmcp.tools.function_tool import FunctionTool

from .tools import minium_session, minium_tools

mcp = FastMCP("minium-sop-tools")

# ──────────────────────────────────────────────
# 进程全局 run context
# ──────────────────────────────────────────────
_ctx_lock = threading.Lock()
_server_ctx = {"session_id": "", "run_id": "", "item_id": "", "user_id": ""}


def install_context_provider() -> None:
    """把进程全局上下文接进 minium_tools 的 provider 注入点（main 启动时调用；
    测试中显式调用/卸载，避免污染同进程的其他测试）。"""
    minium_tools.set_context_provider(lambda: dict(_server_ctx))


def set_run_context(session_id: str, run_id: str, item_id: str, user_id: str = "") -> str:
    """设置当前检查执行的上下文（session/run/item/user）：screenshot 据此拼存档路径，
    run_id 变化时 minium 会话按 run 隔离重建；user_id 供调用链追溯标记归属。
    由后端 executor 每项开始前代码直调（不经 LLM），后端不可见工具 id 细节。"""
    with _ctx_lock:
        _server_ctx.update(
            session_id=session_id, run_id=run_id, item_id=item_id, user_id=user_id)
    return "ok"


def is_minium_available() -> bool:
    """minium 环境是否可用（MINIUM_ENABLED 且项目路径与 DevTools 路径齐备）。
    后端 executor 据此决定走 MCP 真实执行或降级桩。"""
    return minium_session.is_available()


# ──────────────────────────────────────────────
# 工具注册：minium_tools 的 @tool 裸函数 → MCP 工具
# ──────────────────────────────────────────────

def _register(fn, name: str, description: str | None = None) -> None:
    """注册 langchain @tool 为 MCP 工具（.func 是装饰下的裸函数；描述用原中文 docstring）。"""
    tool_fn = getattr(fn, "func", fn)
    mcp.add_tool(FunctionTool.from_function(
        tool_fn, name=name, description=description or fn.description,
    ))


def _register_all() -> None:
    for t in minium_tools.EXECUTOR_TOOLS:
        _register(t, t.name)
    mcp.add_tool(set_run_context)
    mcp.add_tool(is_minium_available)
    # 跨检查项执行上下文快照（executor 每项执行前经 MCP 获取，LLM 亦可调用）
    _register(
        minium_tools.snapshot_app_state,
        "snapshot_app_state",
        "获取小程序当前状态快照（当前页面路径/已配置页面/当前页元素清单），"
        "供跨检查项执行上下文使用。",
    )


_register_all()


def main() -> None:
    """MCP server 入口（uv run python -m mcp_server）。"""
    import os

    from sop_agent.core.config import get_settings

    install_context_provider()
    settings = get_settings()
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport="streamable-http",
            host="127.0.0.1",
            port=settings.MCP_SERVER_PORT,
        )
