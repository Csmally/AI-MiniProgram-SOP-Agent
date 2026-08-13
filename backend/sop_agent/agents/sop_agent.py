"""SOP 生成 Agent — 根据功能列表生成 UI/API 检查清单。"""

from typing import Optional

from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.graph.state import CompiledStateGraph
from langchain_core.messages import AIMessage

from ..core.llm import get_llm
from ..core.state import SessionPhase


class SOPAgentState(MessagesState):
    """SOP 生成 Agent 的状态（主图状态子集 + messages）。"""

    features: list[dict]
    check_items: list[dict]
    current_phase: str
    approval: str
    error: Optional[str]


def generate_sop(state: SOPAgentState) -> dict:
    """根据功能列表生成 SOP 检查清单。"""
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

    import json
    features_json = json.dumps(features, ensure_ascii=False, indent=2)

    prompt = f"""请根据以下功能列表，生成 SOP 检查清单。

对每个功能，从 UI 和 API 两个角度分别生成检查项。

每个检查项包含：
- id: 唯一标识（用 check-001 格式）
- category: "ui" 或 "api"
- description: 检查项描述
- priority: "critical" / "high" / "medium" / "low"
- check_steps: 具体的检查步骤列表
- expected_result: 预期结果
- status: 固定为 "pending"

以 JSON 数组格式返回。

功能列表：
{features_json}
"""

    try:
        response = llm.invoke(prompt)
        content = response.content.strip()

        check_items = []
        try:
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            check_items = json.loads(content)
            # 确保每个 item 有完整字段
            for item in check_items:
                if "status" not in item:
                    item["status"] = "pending"
                if "screenshots" not in item:
                    item["screenshots"] = []
                if "result_detail" not in item:
                    item["result_detail"] = None
        except json.JSONDecodeError:
            check_items = [{
                "id": "check-001",
                "category": "ui",
                "description": "请检查生成的 JSON 格式",
                "priority": "high",
                "check_steps": ["检查生成结果"],
                "expected_result": "JSON 格式正确",
                "status": "pending",
                "screenshots": [],
                "result_detail": content,
            }]

        ui_count = sum(1 for c in check_items if c.get("category") == "ui")
        api_count = sum(1 for c in check_items if c.get("category") == "api")

        msg = (
            f"已生成 {len(check_items)} 个检查项：\n"
            f"- UI 检查：{ui_count} 项\n"
            f"- API 检查：{api_count} 项\n\n"
            "请在右侧面板审核检查清单，确认无误后点击「开始检查」。"
        )

        return {
            "check_items": check_items,
            "current_phase": SessionPhase.SOP_GENERATED.value,
            "approval": "pending",
            "error": None,
            "messages": [AIMessage(content=msg)],
        }
    except Exception as e:
        return {
            "error": str(e),
            "messages": [AIMessage(content=f"❌ SOP 生成失败：{e}")],
        }


def build_sop_subgraph() -> CompiledStateGraph:
    """编译 SOP 生成 Agent 子图（不传 checkpointer，继承父图）。"""
    workflow = StateGraph(SOPAgentState)
    workflow.add_node("generate_sop", generate_sop)
    workflow.add_edge(START, "generate_sop")
    workflow.add_edge("generate_sop", END)
    return workflow.compile()
