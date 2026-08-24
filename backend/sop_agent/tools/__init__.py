"""后端工具接入层 — 只有 MCP 客户端。

minium 工具本体已抽成独立 MCP 服务（backend/mcp_server 包，与 sop_agent
平级）；后端经 mcp_client 远程调用，不 import 任何 minium 相关代码。
"""

from . import mcp_client

__all__ = ["mcp_client"]
