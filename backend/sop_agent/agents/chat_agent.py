"""对话 Agent（llm-agent）— 聊天答疑，替换原 routes.py 手写的 chat 实现。"""

from typing import Annotated, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.graph.message import add_messages
from langchain_core.messages import AIMessage

from ..core.llm import get_llm


class ChatAgentState(TypedDict):
    """对话 Agent 的状态 — 只声明 messages，其余通道不回写。"""

    messages: Annotated[list, add_messages]


def chat(state: ChatAgentState) -> dict:
    """处理用户提问：带最近 10 条历史，简洁回答。"""
    messages = state.get("messages", [])[-10:]
    history_lines = []
    for m in messages:
        content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
        role = m.get("role") if isinstance(m, dict) else getattr(m, "type", "unknown")
        history_lines.append(f"{'用户' if role in ('human', 'user') else '助手'}: {content[:200]}")
    history = "\n".join(history_lines)

    user_message = messages[-1]
    question = (
        user_message.get("content", "")
        if isinstance(user_message, dict)
        else getattr(user_message, "content", "")
    )

    llm = get_llm("chat")
    prompt = (
        f"你是一个微信小程序 SOP 检查助手。\n\n"
        f"对话历史：\n{history}\n\n"
        f"用户提问：{question}\n\n请简洁回答。"
    )
    response = llm.invoke(prompt)
    reply = response.content.strip()

    return {"messages": [AIMessage(content=reply)]}


def build_chat_subgraph() -> CompiledStateGraph:
    """编译对话 Agent 子图（不传 checkpointer，继承父图）。"""
    workflow = StateGraph(ChatAgentState)
    workflow.add_node("chat", chat)
    workflow.add_edge(START, "chat")
    workflow.add_edge("chat", END)
    return workflow.compile()
