"""检查执行 Agent — 每个检查项一个并行 Agent（Send fan-out）。

当前为桩实现（Phase 4 接入 minium Tool + 截图分析后替换 execute_one_item 逻辑）。

子图回写规则（已验证）：
- 与父图共享的 reducer 通道（exec_results / agent_progress）在子图内声明为
  普通 LastValue，节点只返回本项贡献，避免继承值在父图被重复累积；
- 只读上下文用子图专用通道名 batch_id（父图无此通道 → 回写自动丢弃），
  避免并行回写父图 LastValue 通道报 InvalidUpdateError。
"""

import time
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

from rich import print as rPrint

class ExecutorAgentState(TypedDict):
    """检查执行 Agent 的状态 — 每个实例只处理一个检查项。"""

    check_item: dict    # Send payload 传入，子图专用
    batch_id: str       # 本轮 run_id 别名，子图专用只读通道
    exec_results: list  # 普通 LastValue：只返回本项贡献
    agent_progress: list  # 普通 LastValue：只返回本项进度事件


def execute_one_item(state: ExecutorAgentState) -> dict:
    """执行单个检查项（桩实现，Phase 4 替换为 minium 自动化）。"""
    item = state.get("check_item", {})
    batch_id = state.get("batch_id", "")

    rPrint("[bold red]==========execute_one_item==========[/bold red]")
    rPrint(state)
    rPrint("[bold red]==========execute_one_item==========[/bold red]")


    time.sleep(0.5)  # 桩：模拟检查耗时，便于观察并行效果

    result = {
        "check_item_id": item.get("id"),
        "description": item.get("description"),
        "category": item.get("category"),
        "status": "passed",
        "result_detail": "[桩] 检查通过 — minium 集成将在 Phase 4 实现",
        "screenshots": [],
        "run_id": batch_id,
    }

    return {
        "exec_results": [result],
        "agent_progress": [{
            "agent": "executor",
            "item_id": item.get("id"),
            "status": "passed",
            "run_id": batch_id,
        }],
    }


def build_executor_subgraph() -> CompiledStateGraph:
    """编译检查执行 Agent 子图（不传 checkpointer，继承父图）。"""
    workflow = StateGraph(ExecutorAgentState)
    workflow.add_node("execute_one_item", execute_one_item)
    workflow.add_edge(START, "execute_one_item")
    workflow.add_edge("execute_one_item", END)
    return workflow.compile()
