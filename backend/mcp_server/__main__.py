"""MCP server 启动入口：uv run python -m mcp_server。"""

import sys

# 强制 UTF-8 输出（必须在 import server/rich 之前）：
# Windows 控制台/管道可能是 GBK（cp936），页面 WXML 里的 emoji 等字符会让
# rich 调试打印抛 UnicodeEncodeError，且被 snapshot 容错误捕为「元素清单不可用」。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .server import main

if __name__ == "__main__":
    main()
