"""minium 工具 MCP 服务包 — 与 sop_agent（后端）平级的顶层包。

服务边界：本包独占 minium 会话（单 DevTools 实例约束），经 FastMCP
把 16 个工具暴露给后端 executor 或任意 MCP 客户端；允许单向依赖
sop_agent.core（config/llm），sop_agent 不反向依赖本包。
"""
