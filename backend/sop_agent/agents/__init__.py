"""多 Agent 集合 — 4 个 Agent 子图 + executor 主图节点。

子图约定（详见 PLAN 架构决策）：
- 各子图 State 为主图 MainGraphState 的子集 schema；
- 与父图共享的 reducer 通道在子图内声明为普通 LastValue、
  节点只返回本项贡献，避免继承值重复累积；
- 编译时不传 checkpointer → 自动继承父图 PostgresSaver（命名空间隔离）；
- executor 是主图节点（execute_one_item）：DevTools 单实例约束下
  由 orchestrator 以条件边自循环串行调用，非子图。
"""

from .chat_agent import build_chat_subgraph
from .prd_agent import build_prd_subgraph
from .sop_agent import build_sop_subgraph
from .executor_agent import execute_one_item
from .report_agent import build_report_subgraph

__all__ = [
    "build_chat_subgraph",
    "build_prd_subgraph",
    "build_sop_subgraph",
    "execute_one_item",
    "build_report_subgraph",
]
