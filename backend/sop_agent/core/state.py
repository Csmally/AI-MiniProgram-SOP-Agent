"""LangGraph 主图状态 — 多 Agent 架构的共享状态契约。

通道语义约定：
- 普通字段（LastValue）：单一写者，每次写入整体覆盖；
- messages：add_messages reducer（按消息 id 去重，子图回写安全）；
- exec_results / agent_progress：operator.add reducer，支持并行 Send
  并发写；跨 run 永久累积，必须用 run_id 过滤隔离（collect 与前端都过滤）。
"""

import enum
import operator
from typing import Annotated

from langgraph.graph import MessagesState
from langchain_core.messages import AIMessage


class SessionPhase(str, enum.Enum):
    IDLE = "idle"
    PRD_UPLOADED = "prd_uploaded"
    SOP_GENERATED = "sop_generated"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"


class MainGraphState(MessagesState):
    """主图状态 — 继承 messages（add_messages reducer），各 Agent 子图 schema 均为其子集。"""

    session_id: str
    user_id: str                                         # 归属用户 id（审计署名用；权限控制不在图内做）
    prd_content: str
    features: list[dict]
    check_items: list[dict]
    check_results: list[dict]                          # LastValue：collect 每次 run 覆盖
    exec_results: Annotated[list, operator.add]        # 并行累积通道，永不清空
    agent_progress: Annotated[list, operator.add]      # Agent 进度事件，永不清空
    report_content: str                                # report_agent 写入
    run_id: str                                        # dispatch 写入；collect/前端按此过滤
    exec_cursor: int                                   # 本轮执行游标（当前待执行检查项下标，串行循环用）
    current_phase: str
    approval: str
    next_action: str                                   # 操作入口路由指令
    error: str | None


def create_initial_state(session_id: str, user_id: str = "") -> dict:
    """创建新会话的初始状态（仅落盘，不触发任何节点）。"""
    return {
        "session_id": session_id,
        "user_id": user_id,
        "messages": [AIMessage(
            content="你好！我是微信小程序 SOP 检查助手。\n\n请上传新功能的 PRD 需求文档（Markdown 格式），我会自动解析功能信息并生成 SOP 检查清单。"
        )],
        "prd_content": "",
        "features": [],
        "check_items": [],
        "check_results": [],
        "exec_results": [],
        "agent_progress": [],
        "report_content": "",
        "run_id": "",
        "exec_cursor": 0,
        "current_phase": SessionPhase.IDLE.value,
        "approval": "pending",
        "next_action": "",
        "error": None,
    }
