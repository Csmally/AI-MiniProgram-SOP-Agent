"""minium 工具包 — 微信小程序自动化能力。

服务边界设计：本包只依赖 minium_session 抽象层、与 executor 无耦合。
未来抽成独立 MCP server 时，工具定义与逻辑原样搬走，仅把
「进程内函数调用」换成 MCP transport。
"""

from . import minium_session, minium_tools

__all__ = ["minium_session", "minium_tools"]
