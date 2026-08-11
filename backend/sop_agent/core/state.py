"""LangGraph AgentState 定义与会话管理。"""

import uuid
from typing import TypedDict, Literal, Optional
from dataclasses import dataclass, field
from enum import Enum


class SessionPhase(str, Enum):
    """会话阶段枚举。"""
    IDLE = "idle"
    PRD_UPLOADED = "prd_uploaded"
    SOP_GENERATED = "sop_generated"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"


class AgentState(TypedDict, total=False):
    """LangGraph 状态图中流转的状态。"""

    session_id: str
    prd_content: str               # PRD 原始 Markdown 内容
    features: list[dict]           # 解析后的功能列表
    check_items: list[dict]        # SOP 检查项
    current_phase: str             # 当前阶段
    approval: Literal["pending", "approved", "rejected"]
    check_results: list[dict]      # 检查执行结果
    report_content: str            # 报告内容 (Markdown)
    messages: list[dict]           # 聊天消息历史 [{"role": "...", "content": "..."}]
    error: Optional[str]           # 错误信息


def create_initial_state(prd_content: str = "") -> AgentState:
    """创建初始会话状态。"""
    session_id = uuid.uuid4().hex[:12]
    welcome_msg = (
        "你好！我是微信小程序 SOP 检查助手。\n\n"
        "请上传新功能的 PRD 需求文档（Markdown 格式），"
        "我会自动解析功能信息并生成 SOP 检查清单。"
    )
    return AgentState(
        session_id=session_id,
        prd_content=prd_content,
        features=[],
        check_items=[],
        current_phase=SessionPhase.IDLE.value,
        approval="pending",
        check_results=[],
        report_content="",
        messages=[{"role": "assistant", "content": welcome_msg}],
        error=None,
    )


@dataclass
class SessionStore:
    """内存中的会话存储（后续可替换为数据库）。"""

    sessions: dict[str, AgentState] = field(default_factory=dict)
    # LangGraph checkpoints: session_id → thread config
    threads: dict[str, dict] = field(default_factory=dict)

    def create(self) -> AgentState:
        state = create_initial_state()
        self.sessions[state["session_id"]] = state
        self.threads[state["session_id"]] = {
            "configurable": {"thread_id": state["session_id"]}
        }
        return state

    def get(self, session_id: str) -> Optional[AgentState]:
        return self.sessions.get(session_id)

    def update(self, session_id: str, state: AgentState) -> None:
        self.sessions[session_id] = state

    def get_thread_config(self, session_id: str) -> Optional[dict]:
        return self.threads.get(session_id)


# 全局会话存储实例
session_store = SessionStore()
