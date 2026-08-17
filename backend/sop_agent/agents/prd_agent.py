"""PRD 解析 Agent — 解析需求文档，提取功能列表。"""

from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.graph.state import CompiledStateGraph
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from ..core.llm import get_llm, get_system_prompt
from ..core.state import SessionPhase
from ..sop.models import FeatureList

import json

class PRDAgentState(MessagesState):
    """PRD 解析 Agent 的状态（主图状态子集 + messages）。"""

    prd_content: str
    features: list[dict]
    current_phase: str
    error: str | None


def parse_prd(state: PRDAgentState) -> dict:
    """解析 PRD 文档，提取功能列表。

    主路径：function_calling 结构化输出（API 级 schema 强制，需关思考模式）；
    降级路径：prompt 约束 + 健壮解析（provider 偶发拒绝 function calling 时）。
    """
    prd_content = state.get("prd_content", "")
    if not prd_content:
        # 空内容不推进 phase，只记录错误
        return {
            "features": [],
            "error": "PRD 内容为空",
            "messages": [AIMessage(content="❌ PRD 内容为空，请上传有效的需求文档。")],
        }

    try:
        llm = get_llm("parse_prd")
    except ValueError as e:
        return {
            "error": str(e),
            "messages": [AIMessage(content=f"❌ PRD 解析失败：{e}")],
        }

    prompt = [
        SystemMessage(content=get_system_prompt("parse_prd")),
        HumanMessage(content=f"PRD 内容：\n{prd_content[:8000]}"),
    ]

    try:
        try:
            structured = llm.with_structured_output(FeatureList, method="function_calling")
            result = structured.invoke(prompt)
            features = [f.model_dump() for f in result.features]
        except Exception:
            features = _parse_prd_fallback(llm, prompt)
    except Exception as e:
        return {
            "error": str(e),
            "messages": [AIMessage(content=f"❌ PRD 解析失败：{e}")],
        }

    msg = f"已解析出 {len(features)} 个功能：\n\n" + "\n".join(
        f"- **{f.get('name', '未命名')}**: {f.get('description', '')[:100]}" for f in features
    )

    return {
        "features": features,
        "current_phase": SessionPhase.PRD_UPLOADED.value,
        "error": None,
        "messages": [AIMessage(content=msg)],
    }


def _parse_prd_fallback(llm, prompt: list) -> list[dict]:
    """降级路径：prompt 约束输出 JSON + 健壮解析（剥围栏、字段修补）。"""
    response = llm.invoke(prompt)
    content = response.content.strip()

    features = []
    try:
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        features = json.loads(content)
        if isinstance(features, dict):
            # 模型可能输出 {"features": [...]} 或单个功能对象
            features = features.get("features") or [features]
        if not isinstance(features, list):
            features = [features]
    except Exception:
        features = []

    patched = []
    for f in features:
        if not isinstance(f, dict):
            continue
        patched.append({
            "name": f.get("name", "未命名功能"),
            "description": str(f.get("description", "")),
            "affected_pages": f.get("affected_pages") or [],
            "api_endpoints": f.get("api_endpoints") or [],
            "ui_elements": f.get("ui_elements") or [],
            "acceptance_criteria": f.get("acceptance_criteria") or [],
        })
    if not patched:
        patched = [{
            "name": "PRD 解析",
            "description": content[:500],
            "affected_pages": [], "api_endpoints": [],
            "ui_elements": [], "acceptance_criteria": [],
        }]
    return patched


def build_prd_subgraph() -> CompiledStateGraph:
    """编译 PRD 解析 Agent 子图（不传 checkpointer，继承父图）。"""
    workflow = StateGraph(PRDAgentState)
    workflow.add_node("parse_prd", parse_prd)
    workflow.add_edge(START, "parse_prd")
    workflow.add_edge("parse_prd", END)
    return workflow.compile()
