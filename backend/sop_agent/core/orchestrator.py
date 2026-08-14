"""LangGraph 主图编排 — 多 Agent 协作管道。

图结构（所有操作唯一入口 = START 路由器，按 next_action 分发）:

    START ──router──▶
      upload_prd   → parse_prd(子图Agent) → generate_sop(子图Agent) → review_list [interrupt]
      generate_sop → generate_sop(子图Agent) → review_list [interrupt]
      approve/run  → dispatch → fan_out[Send] → execute_item ×N(并行子图Agent)
                     → collect → generate_report(子图Agent) → END
      chat         → chat_agent(子图Agent) → END

关键设计（已在 langgraph 1.2.11 验证）:
- 一切操作走 input-only invoke：fresh run 从 START 执行，自动丢弃旧 pending 中断；
- Send 并行写必须用 reducer 通道（exec_results / agent_progress 用 operator.add）；
- update_state 必须显式 as_node=START（新线程建首个 checkpoint；已有线程
  不报 Ambiguous 且不影响 pending）；
- PostgresSaver 用 psycopg_pool.ConnectionPool（线程安全，SSE worker 线程共享）。
"""

import uuid
from typing import Literal, Optional

from psycopg_pool import ConnectionPool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send, interrupt
from langgraph.checkpoint.postgres import PostgresSaver

from .state import MainGraphState, SessionPhase
from .config import get_settings
from ..agents.prd_agent import build_prd_subgraph
from ..agents.sop_agent import build_sop_subgraph
from ..agents.chat_agent import build_chat_subgraph
from ..agents.executor_agent import build_executor_subgraph
from ..agents.report_agent import build_report_subgraph

from rich import print as rPrint

# ──────────────────────────────────────────────
# 连接池 / 图实例
# ──────────────────────────────────────────────

_pool: Optional[ConnectionPool] = None
_graph: Optional[CompiledStateGraph] = None


def _get_pool() -> ConnectionPool:
    """Postgres 连接池（图执行可能发生在 SSE worker 线程，单 Connection 非线程安全）。"""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=get_settings().DATABASE_URL,
            min_size=1,
            max_size=5,
            kwargs={"autocommit": True, "prepare_threshold": 0},
        )
    return _pool


def close() -> None:
    """关闭连接池与图实例（FastAPI lifespan 调用）。

    显式超时：等待连接归还最多 5s，避免 reload/关机时因 worker 线程
    未结束而无限挂起（进程假死、端口被占）。
    """
    global _pool, _graph
    if _pool is not None:
        try:
            _pool.close(timeout=5)
        except Exception:
            pass  # 强制关闭场景下连接可能无法归还，忽略并退出
        _pool = None
    _graph = None


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
    """执行前：标记本轮 run_id 并进入 running 阶段。"""
    return {
        "run_id": uuid.uuid4().hex[:8],
        "current_phase": SessionPhase.RUNNING.value,
    }


def fan_out(state: MainGraphState) -> list[Send]:
    """按检查项 fan-out：每项一个并行 execute_item Agent。"""
    run_id = state.get("run_id", "")
    return [
        Send("execute_item", {"check_item": item, "batch_id": run_id})
        for item in state.get("check_items", [])
    ]


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

    # 5 个 Agent 子图节点
    workflow.add_node("parse_prd", build_prd_subgraph())
    workflow.add_node("generate_sop", build_sop_subgraph())
    workflow.add_node("chat_agent", build_chat_subgraph())
    workflow.add_node("execute_item", build_executor_subgraph())
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

    # 执行流水线：fan-out → 并行 execute_item → collect → report
    workflow.add_conditional_edges("dispatch", fan_out)
    workflow.add_edge("execute_item", "collect")
    workflow.add_edge("collect", "generate_report")
    workflow.add_edge("generate_report", END)

    saver = PostgresSaver(_get_pool())
    saver.setup()
    return workflow.compile(checkpointer=saver, interrupt_before=["review_list"])


def get_graph() -> CompiledStateGraph:
    """获取（或懒加载创建）编译后的主图。"""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def _thread_config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


# ──────────────────────────────────────────────
# 对外统一操作 API
# ──────────────────────────────────────────────

def invoke_action(session_id: str, action: str, updates: Optional[dict] = None) -> dict:
    """统一操作入口：input-only invoke → START 路由器（fresh run，自动丢弃 pending）。

    返回执行后的最新 checkpoint 状态。
    """
    payload: dict = {"next_action": action}
    if updates:
        payload.update(updates)
    rPrint("[bold green]==========准备入图==========[/bold green]")
    rPrint(payload)
    rPrint("[bold green]==========准备入图==========[/bold green]")
    return get_graph().invoke(payload, _thread_config(session_id))


def stream_action(session_id: str, action: str, updates: Optional[dict] = None):
    """SSE worker 线程消费的生成器：逐事件产出 (kind, data)。

    kind: "updates"（graph.stream 的 {node: writes} chunk）/"done"（最终状态）。
    """
    payload: dict = {"next_action": action}
    rPrint("[bold blue]==========stream_action==========[/bold blue]")
    rPrint(f'action:{action},updates:{updates}')
    rPrint("[bold blue]==========stream_action==========[/bold blue]")
    if updates:
        payload.update(updates)
    for chunk in get_graph().stream(payload, _thread_config(session_id), stream_mode="updates"):
        yield ("updates", chunk)
    final = get_graph().get_state(_thread_config(session_id))
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


def get_session_state(session_id: str) -> Optional[dict]:
    """从 checkpointer 读取会话最新状态（纯读取，不触发任何节点）。"""
    graph = get_graph()
    checkpoint = graph.get_state(_thread_config(session_id))
    # langgraph 1.2 对不存在的线程返回空 StateSnapshot（values={}）而非 None
    if checkpoint is None or not checkpoint.values:
        return None
    return checkpoint.values


def list_sessions() -> list[dict]:
    """列出所有会话，按最近活跃时间排序。

    checkpoints 表无 created_at 列；checkpoint_id 为 uuid6（时间有序），
    字典序即时间序，故用 MAX(checkpoint_id) 作为最近活跃时间。
    """
    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT thread_id, MAX(checkpoint_id) AS last_id FROM checkpoints "
                "WHERE checkpoint_ns = '' GROUP BY thread_id "
                "ORDER BY last_id DESC LIMIT 100"
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
