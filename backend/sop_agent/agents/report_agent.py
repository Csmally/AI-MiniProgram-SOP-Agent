"""报告生成 Agent — 汇总检查结果，生成 Markdown 报告。"""

from typing import Annotated, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.graph.message import add_messages
from langchain_core.messages import AIMessage

from ..core.llm import get_llm
from ..core.state import SessionPhase


class ReportAgentState(TypedDict):
    """报告生成 Agent 的状态（主图状态子集）。"""

    check_items: list[dict]
    check_results: list[dict]
    report_content: str
    current_phase: str
    messages: Annotated[list, add_messages]


def generate_report(state: ReportAgentState) -> dict:
    """根据检查结果生成 Markdown 报告。"""
    check_items = state.get("check_items", [])
    check_results = state.get("check_results", [])

    try:
        llm = get_llm("generate_report")
    except ValueError as e:
        return {
            "messages": [AIMessage(content=f"❌ 报告生成失败：{e}")],
        }

    import json
    summary_data = json.dumps({
        "items": check_items,
        "results": check_results,
    }, ensure_ascii=False, indent=2)

    prompt = f"""请根据以下 SOP 检查结果生成一份简明的 Markdown 报告。

包含：
1. 检查概要
2. 各项检查结果
3. 问题汇总（如有失败项）
4. 建议

检查数据：
{summary_data[:6000]}
"""

    try:
        response = llm.invoke(prompt)
        report_content = response.content.strip()

        return {
            "report_content": report_content,
            "current_phase": SessionPhase.COMPLETED.value,
            "messages": [AIMessage(content="报告已生成！请在右侧面板查看。")],
        }
    except Exception as e:
        return {
            "messages": [AIMessage(content=f"❌ 报告生成失败：{e}")],
        }


def build_report_subgraph() -> CompiledStateGraph:
    """编译报告生成 Agent 子图（不传 checkpointer，继承父图）。"""
    workflow = StateGraph(ReportAgentState)
    workflow.add_node("generate_report", generate_report)
    workflow.add_edge(START, "generate_report")
    workflow.add_edge("generate_report", END)
    return workflow.compile()
