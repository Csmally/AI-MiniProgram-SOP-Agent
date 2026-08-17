"""对话 Agent（llm-agent）— 聊天答疑，聊天逻辑的唯一权威实现。

/chat（非流式）与 /chat/stream（流式）都经主图调用本 Agent：
- 非流式：节点内 llm.invoke 一次性返回；
- 流式：/chat/stream 的 worker 线程先 register_stream_hook 注册回调，
  节点内 llm.stream 逐 token 推送，token 经回调桥接回 SSE。
"""

import threading
from typing import Callable

from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.graph.state import CompiledStateGraph
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from ..core.llm import get_llm, get_system_prompt


class ChatAgentState(MessagesState):
    """对话 Agent 的状态 — 只继承 messages（add_messages reducer），其余通道不回写。"""


# ──────────────────────────────────────────────
# 流式回调钩子（thread-local）
# ──────────────────────────────────────────────

_stream_local = threading.local()


def register_stream_hook(hook: Callable[[str], None]) -> None:
    """注册流式 token 回调（仅当前线程生效，供 /chat/stream 的 worker 线程使用）。"""
    _stream_local.hook = hook


def unregister_stream_hook() -> None:
    _stream_local.hook = None


def build_chat_prompt(messages: list) -> tuple[str, str]:
    """从消息历史构造 (历史文本, 用户提问)。"""
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
    return history, question


def _build_prompt(history: str, question: str) -> list:
    """构造消息列表：系统提示词来自 core/llm.py 的任务注册表，历史与提问放入 HumanMessage。"""
    return [
        SystemMessage(content=get_system_prompt("chat")),
        HumanMessage(content=f"对话历史：\n{history}\n\n用户提问：{question}"),
    ]


# ──────────────────────────────────────────────
# Agent 节点
# ──────────────────────────────────────────────

def chat(state: ChatAgentState) -> dict:
    """处理用户提问：带最近 10 条历史，简洁回答。"""
    history, question = build_chat_prompt(state.get("messages", [])[-10:])
    llm = get_llm("chat")
    prompt = _build_prompt(history, question)

    hook: Callable[[str], None] | None = getattr(_stream_local, "hook", None)
    if hook is not None:
        # 流式：逐 token 推送，同时累积完整回复（最终以完整消息落盘）
        full_reply = ""
        for chunk in llm.stream(prompt):
            token = chunk.content if hasattr(chunk, "content") else str(chunk)
            if token:
                full_reply += token
                hook(token)
        return {"messages": [AIMessage(content=full_reply)]}

    # 非流式：一次性返回
    response = llm.invoke(prompt)
    return {"messages": [AIMessage(content=response.content.strip())]}


def build_chat_subgraph() -> CompiledStateGraph:
    """编译对话 Agent 子图（不传 checkpointer，继承父图）。"""
    workflow = StateGraph(ChatAgentState)
    workflow.add_node("chat", chat)
    workflow.add_edge(START, "chat")
    workflow.add_edge("chat", END)
    return workflow.compile()
