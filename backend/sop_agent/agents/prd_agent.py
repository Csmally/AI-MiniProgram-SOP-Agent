"""PRD 解析 Agent — 解析需求文档，提取功能列表。"""

from typing import Optional

from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.graph.state import CompiledStateGraph
from langchain_core.messages import AIMessage

from ..core.llm import get_llm
from ..core.state import SessionPhase


class PRDAgentState(MessagesState):
    """PRD 解析 Agent 的状态（主图状态子集 + messages）。"""

    prd_content: str
    features: list[dict]
    current_phase: str
    error: Optional[str]


def parse_prd(state: PRDAgentState) -> dict:
    """解析 PRD 文档，提取功能列表。"""
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

    prompt = f"""请分析以下 PRD 需求文档，提取所有新增功能的信息。

对每个功能，提取以下字段：
- name: 功能名称
- description: 功能描述
- affected_pages: 涉及的页面路径列表
- api_endpoints: 相关的 API 接口列表
- ui_elements: 关键的 UI 元素（按钮、输入框、列表等）
- acceptance_criteria: 验收标准列表

以 JSON 数组格式返回，每个元素是一个功能对象。

PRD 内容：
{prd_content[:8000]}
"""

    try:
        import json
        response = llm.invoke(prompt)
        content = response.content.strip()

        features = []
        try:
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            features = json.loads(content)
        except json.JSONDecodeError:
            features = [{"name": "PRD 解析", "description": content, "affected_pages": [], "api_endpoints": [], "ui_elements": [], "acceptance_criteria": []}]

        msg = f"已解析出 {len(features)} 个功能：\n\n" + "\n".join(
            f"- **{f['name']}**: {f.get('description', '')[:100]}" for f in features
        )

        return {
            "features": features,
            "current_phase": SessionPhase.PRD_UPLOADED.value,
            "error": None,
            "messages": [AIMessage(content=msg)],
        }
    except Exception as e:
        return {
            "error": str(e),
            "messages": [AIMessage(content=f"❌ PRD 解析失败：{e}")],
        }


def build_prd_subgraph() -> CompiledStateGraph:
    """编译 PRD 解析 Agent 子图（不传 checkpointer，继承父图）。"""
    workflow = StateGraph(PRDAgentState)
    workflow.add_node("parse_prd", parse_prd)
    workflow.add_edge(START, "parse_prd")
    workflow.add_edge("parse_prd", END)
    return workflow.compile()
