"""MCP 工具客户端 — executor 在 MCP 模式下经此调用独立 MCP server 的 minium 工具。

设计要点：
- opt-in：settings.MCP_ENABLED=false 时本模块完全旁路（in-process 直连不变）；
- 懒加载 + 缓存：首次 get_tools() 建 MultiServerMCPClient 并拉取工具列表；
- 同步封装：后端 LangGraph 全同步（PITFALLS：避免 Windows 事件循环问题），
  MCP 工具是异步的——优先同步 invoke，NotImplemented 时 asyncio.run 兜底
  （工具调用秒级，loop 创建开销可忽略）；
- 失败即读错误：连接失败 → executor 按 MCP → in-process → 桩 三级降级。
"""

import asyncio
import json
import threading
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from ..core.config import get_settings

_client: MultiServerMCPClient | None = None
_tools: list = []
_tool_map: dict = {}
_ready: bool | None = None   # 探活结果缓存（None=未探活）
_lock = threading.Lock()


def is_enabled() -> bool:
    """MCP 开关（.env MCP_ENABLED=true）。"""
    return get_settings().MCP_ENABLED


def _build_client() -> MultiServerMCPClient:
    return MultiServerMCPClient({
        "minium": {
            "transport": "streamable_http",
            "url": get_settings().MCP_SERVER_URL,
            "timeout": 120,   # 对齐 LLM 超时；工具本身秒级
        },
    })


def get_tools() -> tuple[list, dict]:
    """懒加载 MCP 工具，返回 (tools, name→tool 映射)。失败抛可读 RuntimeError。"""
    global _client, _tools, _tool_map
    if _tool_map:
        return _tools, _tool_map
    with _lock:
        if _tool_map:
            return _tools, _tool_map
        _client = _build_client()
        try:
            # langchain-mcp-adapters 0.3.x 的 get_tools 是协程（inspect 不显示 async）
            _tools = asyncio.run(_client.get_tools())
        except Exception as e:
            _client = None
            raise RuntimeError(
                f"MCP server 连接失败（{get_settings().MCP_SERVER_URL}）: {e}"
            ) from e
        _tool_map = {t.name: t for t in _tools}
        return _tools, _tool_map


def is_ready() -> bool:
    """MCP 模式是否可用：开关开启 + 连接成功 + server 侧 minium 环境可用。

    结果缓存（首次探活后不再逐项打探活请求）；工具调用失败可 invalidate()
    重置，下一次 is_ready 重新探活。
    """
    global _ready
    if not is_enabled():
        return False
    if _ready is not None:
        return _ready
    try:
        get_tools()
        _ready = bool(call_tool("is_minium_available", {}))
    except Exception:
        _ready = False
    return _ready


def invalidate() -> None:
    """重置就绪缓存（server 挂掉/工具失败后调用，让下次 is_ready 重新探活）。"""
    global _ready
    _ready = None


def call_tool(name: str, args: Any) -> Any:
    """同步调用 MCP 工具（返回解包后的原始结果，非 ToolMessage）。"""
    _tools, tool_map = get_tools()
    tool = tool_map[name]
    try:
        result = tool.invoke(args)
    except NotImplementedError:
        result = asyncio.run(tool.ainvoke(args))
    return _unwrap(result)


def _unwrap(result: Any) -> Any:
    """解包 MCP 工具返回：adapters 0.3.x 的 invoke 返回 content block 列表
    [{'type': 'text', 'text': ..., 'id': ...}]，还原真实值——否则 bool()
    恒真（is_minium_available 误判）、LLM 看到协议噪音。JSON 文本反序列化，
    普通文本原样返回；非标准结构原样透传。"""
    if (
        isinstance(result, list) and len(result) == 1
        and isinstance(result[0], dict) and result[0].get("type") == "text"
    ):
        text = result[0].get("text", "")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text
    return result
