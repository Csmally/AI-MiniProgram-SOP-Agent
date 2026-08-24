"""LangGraph 主图编排 — 多 Agent 协作管道。

图结构（所有操作唯一入口 = START 路由器，按 next_action 分发）:

    START ──router──▶
      upload_prd   → parse_prd(子图Agent) → END（phase=prd_uploaded）
      generate_sop → generate_sop(子图Agent) → review_list [interrupt]
      approve/run  → dispatch → execute_agent（串行循环，游标驱动）
                     → collect → generate_report(子图Agent) → END
      chat         → chat_agent(子图Agent) → END

关键设计（已在 langgraph 1.2.11 验证）:
- 一切操作走 input-only invoke：fresh run 从 START 执行，自动丢弃旧 pending 中断；
- 执行串行化：微信开发者工具单实例约束，execute_agent 以条件边自循环逐项执行；
- reducer 通道（exec_results / agent_progress 用 operator.add）支持循环内增量写；
- update_state 必须显式 as_node=START（新线程建首个 checkpoint；已有线程
  不报 Ambiguous 且不影响 pending）；
- PostgresSaver 用 psycopg_pool.ConnectionPool（线程安全，SSE worker 线程共享）。
"""

import uuid
from typing import Literal

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from langgraph.checkpoint.postgres import PostgresSaver

from .state import MainGraphState, SessionPhase
from .db import get_pool, close_pool
from ..agents.prd_agent import build_prd_subgraph
from ..agents.sop_agent import build_sop_subgraph
from ..agents.chat_agent import build_chat_subgraph
from ..agents.executor_agent import build_executor_subgraph
from ..agents.report_agent import build_report_subgraph

from rich import print as rPrint

# ──────────────────────────────────────────────
# 图实例
# ──────────────────────────────────────────────

_graph: CompiledStateGraph | None = None


def close() -> None:
    """关闭连接池与图实例（FastAPI lifespan 调用）。"""
    global _graph
    close_pool()
    _graph = None
    # 注：minium 会话由 MCP server 进程独占，后端进程无需 dispose


# ──────────────────────────────────────────────
# 主图节点
# ──────────────────────────────────────────────

def router(state: MainGraphState) -> Literal["parse_prd", "generate_sop", "chat_agent", "dispatch"]:
    """操作入口路由。注意：Literal 只注解真实节点名，不能含 'END'。"""
    action = state.get("next_action", "chat")
    return {
        "upload_prd": "parse_prd",
        "generate_sop": "generate_sop",
        "approve": "dispatch",
        "run": "dispatch",
        "chat": "chat_agent",
    }.get(action, "chat_agent")


def review_list(state: MainGraphState) -> dict:
    """人工审核屏障（HITL）：永不自行推进，前进只来自 START 路由器。"""
    interrupt("请审核检查清单，确认或拒绝。")
    return {}


def dispatch(state: MainGraphState) -> dict:
    """执行前：标记本轮 run_id、进入 running 阶段、游标归零。"""
    return {
        "run_id": uuid.uuid4().hex[:8],
        "current_phase": SessionPhase.RUNNING.value,
        "exec_cursor": 0,
    }


def should_continue(state: MainGraphState) -> Literal["execute_agent", "collect"]:
    """执行游标未到头 → 继续下一项（串行，DevTools 单实例约束）；到头 → collect 汇总。"""
    cursor = state.get("exec_cursor", 0)
    total = len(state.get("check_items", []))
    return "execute_agent" if cursor < total else "collect"


def collect(state: MainGraphState) -> dict:
    """汇总本轮 run 的执行结果（按 run_id 过滤累积通道，隔离陈旧数据）。"""
    run_id = state.get("run_id", "")
    results = [r for r in state.get("exec_results", []) if r.get("run_id") == run_id]
    return {"check_results": results}


# ──────────────────────────────────────────────
# 图构建
# ──────────────────────────────────────────────

def build_graph() -> CompiledStateGraph:
    """构建并编译主图（Postgres checkpointer + 连接池）。"""
    workflow = StateGraph(MainGraphState)

    # 5 个 Agent 子图节点（executor 为单节点子图；串行循环见 should_continue）
    workflow.add_node("parse_prd", build_prd_subgraph())
    workflow.add_node("generate_sop", build_sop_subgraph())
    workflow.add_node("chat_agent", build_chat_subgraph())
    workflow.add_node("execute_agent", build_executor_subgraph())
    workflow.add_node("generate_report", build_report_subgraph())
    # 主图控制节点
    workflow.add_node("review_list", review_list)
    workflow.add_node("dispatch", dispatch)
    workflow.add_node("collect", collect)

    # 路由边
    workflow.add_conditional_edges(
        START, router,
        {"parse_prd": "parse_prd", "generate_sop": "generate_sop",
         "chat_agent": "chat_agent", "dispatch": "dispatch"},
    )
    # 两步交互：upload_prd 只解析（phase 停在 prd_uploaded），
    # 用户点「生成检查清单」才触发 generate_sop → 审核中断
    workflow.add_edge("parse_prd", END)
    workflow.add_edge("generate_sop", "review_list")
    workflow.add_edge("review_list", END)
    workflow.add_edge("chat_agent", END)

    # 执行流水线：dispatch → 串行循环 execute_agent（游标驱动）→ collect → report
    workflow.add_edge("dispatch", "execute_agent")
    workflow.add_conditional_edges(
        "execute_agent", should_continue,
        {"execute_agent": "execute_agent", "collect": "collect"},
    )
    workflow.add_edge("collect", "generate_report")
    workflow.add_edge("generate_report", END)

    saver = PostgresSaver(get_pool())
    saver.setup()
    return workflow.compile(checkpointer=saver, interrupt_before=["review_list"])


def get_graph() -> CompiledStateGraph:
    """获取（或懒加载创建）编译后的主图。"""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def _thread_config(session_id: str, user_id: int | None = None) -> dict:
    """图执行 config：thread_id 定位会话；user_id 放进 metadata——
    langgraph 会把 config.metadata 透传给回调 handler，为未来 LangSmith 式
    调用链追溯平台提供 session/user 归属（thread_id 本身已在回调 metadata 中）。"""
    cfg: dict = {"configurable": {"thread_id": session_id}}
    if user_id is not None:
        cfg["metadata"] = {"session_id": session_id, "user_id": user_id}
    return cfg


# ──────────────────────────────────────────────
# 对外统一操作 API
# ──────────────────────────────────────────────

def invoke_action(session_id: str, action: str, updates: dict | None = None,
                  user_id: int | None = None) -> dict:
    """统一操作入口：input-only invoke → START 路由器（fresh run，自动丢弃 pending）。

    返回执行后的最新 checkpoint 状态。
    """
    payload: dict = {"next_action": action}
    if updates:
        payload.update(updates)
    rPrint("[bold green]==========准备入图==========[/bold green]")
    rPrint(payload)
    rPrint("[bold green]==========准备入图==========[/bold green]")
    return get_graph().invoke(payload, _thread_config(session_id, user_id))


def stream_action(session_id: str, action: str, updates: dict | None = None,
                  user_id: int | None = None):
    """SSE worker 线程消费的生成器：逐事件产出 (kind, data)。

    kind: "updates"（graph.stream 的 {node: writes} chunk）/"done"（最终状态）。
    """
    payload: dict = {"next_action": action}
    if updates:
        payload.update(updates)
    rPrint("[bold blue]==========stream_action==========[/bold blue]")
    rPrint(f'action:{payload},updates:{payload}')
    rPrint("[bold blue]==========stream_action==========[/bold blue]")
    for chunk in get_graph().stream(payload, _thread_config(session_id, user_id), stream_mode="updates"):
        yield ("updates", chunk)
    final = get_graph().get_state(_thread_config(session_id, user_id))
    yield ("done", final.values if final else None)


def update_state(session_id: str, values: dict) -> dict:
    """持久化式更新（不触发节点执行）。

    必须显式 as_node=START：新线程创建首个 checkpoint；已有线程（含中断态）
    走 START 的 input writers，任意通道字段可写且不影响 pending 中断。
    """
    graph = get_graph()
    graph.update_state(_thread_config(session_id), values, as_node=START)
    st = graph.get_state(_thread_config(session_id))
    return st.values if st else {}


def get_session_state(session_id: str) -> dict | None:
    """从 checkpointer 读取会话最新状态（纯读取，不触发任何节点）。"""
    graph = get_graph()
    checkpoint = graph.get_state(_thread_config(session_id))
    # langgraph 1.2 对不存在的线程返回空 StateSnapshot（values={}）而非 None
    if checkpoint is None or not checkpoint.values:
        return None
    return checkpoint.values


def list_sessions(user_id: int) -> list[dict]:
    """列出指定用户的会话，按最近活跃时间排序。

    归属由 session_owners 映射表 JOIN 过滤（无主旧会话对任何用户不可见）；
    checkpoints 表无 created_at 列，checkpoint_id 为 uuid6（时间有序），
    字典序即时间序，故用 MAX(checkpoint_id) 作为最近活跃时间。
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT o.session_id, MAX(c.checkpoint_id) AS last_id "
                "FROM session_owners o "
                "JOIN checkpoints c ON c.thread_id = o.session_id AND c.checkpoint_ns = '' "
                "WHERE o.user_id = %s "
                "GROUP BY o.session_id "
                "ORDER BY last_id DESC LIMIT 100",
                (user_id,),
            )
            rows = cur.fetchall()
    result = []
    for tid, _ in rows:
        state = get_session_state(tid)
        if state:
            result.append({
                "session_id": tid,
                "current_phase": state.get("current_phase", "idle"),
                "features_count": len(state.get("features", [])),
                "check_items_count": len(state.get("check_items", [])),
            })
    return result


def delete_session(session_id: str) -> bool:
    """删除会话的 checkpoint 数据。"""
    graph = get_graph()
    saver = graph.checkpointer
    try:
        saver.delete_thread(session_id)
        return True
    except Exception:
        return False
