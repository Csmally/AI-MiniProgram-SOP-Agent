"""LangGraph AgentState — 由 PostgresSaver checkpointer 全权管理。"""

import uuid
from typing import Literal, Optional
from enum import Enum
from langgraph.graph import MessagesState
from langchain_core.messages import AIMessage


class SessionPhase(str, Enum):
    IDLE = "idle"
    PRD_UPLOADED = "prd_uploaded"
    SOP_GENERATED = "sop_generated"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"


class AgentState(MessagesState):
    session_id: str
    prd_content: str
    features: list[dict]
    check_items: list[dict]
    current_phase: str
    approval: Literal["pending", "approved", "rejected"]
    check_results: list[dict]
    report_content: str
    error: Optional[str]


def create_initial_state(prd_content: str = "") -> AgentState:
    session_id = uuid.uuid4().hex[:12]
    return AgentState(
        session_id=session_id,
        prd_content=prd_content,
        features=[],
        check_items=[],
        current_phase=SessionPhase.IDLE.value,
        approval="pending",
        check_results=[],
        report_content="",
        messages=[AIMessage(content="你好！我是微信小程序 SOP 检查助手。\n\n请上传新功能的 PRD 需求文档（Markdown 格式），我会自动解析功能信息并生成 SOP 检查清单。")],
        error=None,
    )
