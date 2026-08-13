"""SOP 生成 Agent — 根据功能列表生成 UI/API 检查清单。"""

from typing import Optional

from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.graph.state import CompiledStateGraph
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from ..core.llm import get_llm, get_system_prompt
from ..core.state import SessionPhase
from ..sop.models import CheckItemList

from rich import print as rPrint
import json

class SOPAgentState(MessagesState):
    """SOP 生成 Agent 的状态（主图状态子集 + messages）。"""

    features: list[dict]
    check_items: list[dict]
    current_phase: str
    approval: str
    error: Optional[str]


def generate_sop(state: SOPAgentState) -> dict:
    """根据功能列表生成 SOP 检查清单。

    主路径：function_calling 结构化输出（API 级 schema 强制，需关思考模式）；
    降级路径：prompt 约束 + 健壮解析。
    """
    features = state.get("features", [])
    if not features:
        # 没有功能时不推进 phase，只提示
        return {
            "check_items": [],
            "messages": [AIMessage(content="❌ 没有解析到功能，无法生成检查清单，请先上传 PRD。")],
        }

    try:
        llm = get_llm("generate_sop")
    except ValueError as e:
        return {
            "error": str(e),
            "messages": [AIMessage(content=f"❌ SOP 生成失败：{e}")],
        }

    features_json = json.dumps(features, ensure_ascii=False, indent=2)

    prompt = [
        SystemMessage(content=get_system_prompt("generate_sop")),
        HumanMessage(content=f"功能列表：\n{features_json}"),
    ]

    try:
        try:
            structured = llm.with_structured_output(CheckItemList, method="function_calling")
            result = structured.invoke(prompt)
            rPrint("[bold magenta]==========generate_sop==========[/bold magenta]")
            rPrint(result)
            rPrint("[bold magenta]==========generate_sop==========[/bold magenta]")
            check_items = [i.model_dump() for i in result.check_items]
        except Exception:
            check_items = _generate_sop_fallback(llm, prompt)
    except Exception as e:
        return {
            "error": str(e),
            "messages": [AIMessage(content=f"❌ SOP 生成失败：{e}")],
        }

    ui_count = sum(1 for c in check_items if c.get("category") == "ui")
    api_count = sum(1 for c in check_items if c.get("category") == "api")

    msg = (
        f"已生成 {len(check_items)} 个检查项：\n"
        f"- **UI 检查**：{ui_count} 项\n"
        f"- **API 检查**：{api_count} 项\n\n"
        "请在右侧面板审核检查清单，确认无误后点击「开始检查」。"
    )

    return {
        "check_items": check_items,
        "current_phase": SessionPhase.SOP_GENERATED.value,
        "approval": "pending",
        "error": None,
        "messages": [AIMessage(content=msg)],
    }


def _generate_sop_fallback(llm, prompt: list) -> list[dict]:
    """降级路径：prompt 约束输出 JSON + 健壮解析（剥围栏、字段修补）。"""
    import json
    response = llm.invoke(prompt)
    content = response.content.strip()

    check_items = []
    try:
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        check_items = json.loads(content)
        if isinstance(check_items, dict):
            check_items = check_items.get("check_items") or [check_items]
        if not isinstance(check_items, list):
            check_items = [check_items]
    except Exception:
        check_items = []

    patched = []
    for item in check_items:
        if not isinstance(item, dict):
            continue
        patched.append({
            "id": item.get("id") or f"check-{len(patched)+1:03d}",
            "category": item.get("category") if item.get("category") in ("ui", "api") else "ui",
            "description": str(item.get("description", "")),
            "priority": item.get("priority") if item.get("priority") in ("critical", "high", "medium", "low") else "medium",
            "check_steps": item.get("check_steps") or [],
            "expected_result": str(item.get("expected_result", "")),
            "status": "pending",
            "screenshots": [],
            "result_detail": None,
        })
    if not patched:
        patched = [{
            "id": "check-001",
            "category": "ui",
            "description": "请检查生成的 JSON 格式",
            "priority": "high",
            "check_steps": ["检查生成结果"],
            "expected_result": "JSON 格式正确",
            "status": "pending",
            "screenshots": [],
            "result_detail": content[:500],
        }]
    return patched


def build_sop_subgraph() -> CompiledStateGraph:
    """编译 SOP 生成 Agent 子图（不传 checkpointer，继承父图）。"""
    workflow = StateGraph(SOPAgentState)
    workflow.add_node("generate_sop", generate_sop)
    workflow.add_edge(START, "generate_sop")
    workflow.add_edge("generate_sop", END)
    return workflow.compile()
