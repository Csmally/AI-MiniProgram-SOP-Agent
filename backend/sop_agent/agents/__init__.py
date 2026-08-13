"""多 Agent 子图集合 — 每个文件定义一个独立 Agent 子图。

子图约定（详见 PLAN 架构决策）：
- 各子图 State 为主图 MainGraphState 的子集 schema；
- 与父图共享的 reducer 通道（exec_results / agent_progress）在子图内
  声明为普通 LastValue，节点只返回本项贡献，避免继承值重复累积；
- 编译时不传 checkpointer → 自动继承父图 PostgresSaver（命名空间隔离）。
"""

from .chat_agent import build_chat_subgraph
from .prd_agent import build_prd_subgraph
from .sop_agent import build_sop_subgraph
from .executor_agent import build_executor_subgraph
from .report_agent import build_report_subgraph

__all__ = [
    "build_chat_subgraph",
    "build_prd_subgraph",
    "build_sop_subgraph",
    "build_executor_subgraph",
    "build_report_subgraph",
]
