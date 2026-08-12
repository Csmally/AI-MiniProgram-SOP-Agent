"""LangGraph StateGraph — SOP 检查流程编排。

图结构:
    parse_prd → generate_sop → review_list → execute_checks → generate_report
                   ↑ 用户拒绝则回到这里   ↑ Human-in-the-loop 中断
"""

from typing import Literal
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import interrupt
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage
from psycopg import connect

from .state import AgentState, SessionPhase
from .config import get_settings


# ──────────────────────────────────────────────
# 模型工厂
# ──────────────────────────────────────────────

def _get_llm(task: str) -> ChatOpenAI:
    """根据任务类型获取对应的 ChatOpenAI 实例。"""
    settings = get_settings()
    model_key = settings.MODEL_ROUTING.get(task, "deepseek-v4-pro")
    llm_config = settings.get_llm_config(model_key)
    api_key = llm_config.get("api_key", "")

    if not api_key:
        raise ValueError(
            f"缺少 API Key（任务: {task}, 模型: {model_key}）。\n"
            f"请在 .env 文件中设置对应的 API Key。"
        )

    return ChatOpenAI(
        model=llm_config.get("model", model_key),
        base_url=llm_config.get("base_url", "https://api.deepseek.com"),
        api_key=api_key,
        temperature=0.3 if task != "chat" else 0.7,
        max_tokens=4096,
    )


def _handle_node_error(node_name: str, error: Exception) -> dict:
    """统一处理节点中的异常，返回友好的错误信息。"""
    msg = f"❌ {node_name} 执行失败：{error}"
    return {
        "messages": [AIMessage(content=msg)],
        "error": str(error),
    }


# ──────────────────────────────────────────────
# 图节点
# ──────────────────────────────────────────────

def parse_prd(state: AgentState) -> dict:
    """解析 PRD 文档，提取功能列表。"""
    prd_content = state.get("prd_content", "")
    if not prd_content:
        return {
            "features": [],
            "current_phase": SessionPhase.PRD_UPLOADED.value,
            "error": "PRD 内容为空",
        }

    try:
        llm = _get_llm("parse_prd")
    except ValueError as e:
        return _handle_node_error("PRD 解析", e)

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
        response = llm.invoke(prompt)
        content = response.content.strip()

        # 尝试解析 JSON
        import json
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
            "messages": [AIMessage(content=msg)],
            "error": None,
        }
    except Exception as e:
        return _handle_node_error("PRD 解析", e)


def generate_sop(state: AgentState) -> dict:
    """根据功能列表生成 SOP 检查清单。"""
    features = state.get("features", [])
    if not features:
        return {
            "check_items": [],
            "current_phase": SessionPhase.PRD_UPLOADED.value,
        }

    llm = _get_llm("generate_sop")

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

    response = llm.invoke(prompt)
    content = response.content.strip()

    check_items = []
    try:
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        check_items = json.loads(content)
        # 确保每个 item 有 status
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
        "messages": [AIMessage(content=msg)],
    }


def review_list(state: AgentState) -> dict:
    """审核检查清单 — Human-in-the-loop 中断点。

    图到达此节点时会暂停，等待前端发送 approve/reject 指令。
    """
    approval = state.get("approval", "pending")

    if approval == "approved":
        return {
            "current_phase": SessionPhase.READY.value,
            "messages": [AIMessage(content="审核通过！准备开始执行检查。")],
        }
    elif approval == "rejected":
        return {
            "current_phase": SessionPhase.PRD_UPLOADED.value,
            "approval": "pending",
            "messages": [AIMessage(content="已拒绝，请修改检查清单后重新生成。")],
        }
    else:
        # pending 状态 — 使用 interrupt 等待用户操作
        interrupt("请审核检查清单，确认或拒绝。")
        return {}


def execute_checks(state: AgentState) -> dict:
    """执行检查 — 逐项执行 SOP 检查（当前为桩实现）。"""
    check_items = state.get("check_items", [])
    if not check_items:
        return {
            "current_phase": SessionPhase.COMPLETED.value,
            "check_results": [],
            "messages": [AIMessage(content="没有检查项需要执行。")],
        }

    # Phase 4 将集成 minium Tool，当前为桩实现
    results = []
    for item in check_items:
        result = {
            "check_item_id": item.get("id"),
            "description": item.get("description"),
            "category": item.get("category"),
            "status": "passed",
            "result_detail": "[桩] 检查通过 — minium 集成将在 Phase 4 实现",
            "screenshots": [],
        }
        results.append(result)

    passed = sum(1 for r in results if r["status"] == "passed")
    failed = sum(1 for r in results if r["status"] == "failed")

    msg = f"检查执行完成：{passed} 通过，{failed} 失败，共 {len(results)} 项。"

    return {
        "check_results": results,
        "current_phase": SessionPhase.RUNNING.value,
        "messages": [AIMessage(content=msg)],
    }


def generate_report(state: AgentState) -> dict:
    """生成检查报告。"""
    check_items = state.get("check_items", [])
    check_results = state.get("check_results", [])

    llm = _get_llm("generate_report")

    import json
    summary_data = json.dumps({
        "items": check_items,
        "results": check_results,
    }, ensure_ascii=False, indent=2)

    prompt = f"""请根据以下 SOP 检查结果生成一份简明的 Markdown 报告。

包含：
1. 检查概要
2. 各项检查结果
3. 问题汇总（如有失败项）
4. 建议

检查数据：
{summary_data[:6000]}
"""

    response = llm.invoke(prompt)
    report_content = response.content.strip()

    return {
        "report_content": report_content,
        "current_phase": SessionPhase.COMPLETED.value,
        "messages": [AIMessage(content="报告已生成！请在右侧面板查看。")],
    }


# ──────────────────────────────────────────────
# 条件边
# ──────────────────────────────────────────────

def _after_review(state: AgentState) -> Literal["generate_sop", "execute_checks"]:
    """审核后的路由：批准 → 执行，拒绝 → 重新生成。"""
    approval = state.get("approval", "pending")
    if approval == "approved":
        return "execute_checks"
    return "generate_sop"


def _after_checks(state: AgentState) -> Literal["generate_report", END]:
    """检查后的路由。"""
    if state.get("current_phase") == SessionPhase.COMPLETED.value:
        return END
    return "generate_report"


# ──────────────────────────────────────────────
# 图构建
# ──────────────────────────────────────────────

def build_graph() -> CompiledStateGraph:
    """构建并编译 LangGraph 状态图（PostgreSQL checkpointer）。"""
    workflow = StateGraph(AgentState)

    workflow.add_node("parse_prd", parse_prd)
    workflow.add_node("generate_sop", generate_sop)
    workflow.add_node("review_list", review_list)
    workflow.add_node("execute_checks", execute_checks)
    workflow.add_node("generate_report", generate_report)

    workflow.add_edge(START, "parse_prd")
    workflow.add_edge("parse_prd", "generate_sop")
    workflow.add_edge("generate_sop", "review_list")

    workflow.add_conditional_edges(
        "review_list",
        _after_review,
        {"generate_sop": "generate_sop", "execute_checks": "execute_checks"},
    )

    workflow.add_edge("execute_checks", "generate_report")
    workflow.add_edge("generate_report", END)

    db_url = get_settings().DATABASE_URL
    conn = connect(db_url, autocommit=True, prepare_threshold=0)
    saver = PostgresSaver(conn)
    saver.setup()

    return workflow.compile(
        checkpointer=saver,
        interrupt_before=["review_list"],
    )


# 全局图实例
_graph: CompiledStateGraph | None = None


def get_graph() -> CompiledStateGraph:
    """获取（或懒加载创建）编译后的图。"""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


# ──────────────────────────────────────────────
# 对外调用接口 — 全部通过 checkpointer
# ──────────────────────────────────────────────

def save_new_session(state: AgentState) -> None:
    """保存新会话初始状态到 checkpointer。"""
    graph = get_graph()
    config = _thread_config(state["session_id"])
    graph.invoke(state, config)


def _thread_config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def get_session_state(session_id: str) -> AgentState | None:
    """从 checkpointer 读取会话最新状态（纯读取，不触发任何节点）。

    实现原理
    --------
    LangGraph 的持久化模型是「线程(thread) → 检查点(checkpoint)」：
    - 每次图执行或 update_state 后，checkpointer 都会为对应 thread_id
      追加写入一条新 checkpoint（PostgresSaver 落在 checkpoints 表）；
    - 本函数把 session_id 当作 thread_id 定位线程，取回最新一条
      checkpoint 里保存的状态快照。

    调用约定
    --------
    - 所有 REST 端点（get_session / upload_prd / chat 等）处理请求前
      都先调用本函数确认会话存在、拿到当前状态；
    - 返回 None 即视为「会话不存在」，API 层据此抛 404；
    - 纯查询操作：不像 graph.invoke() 会让图继续执行节点，
      也不会触发 interrupt 恢复，适合高频读场景。

    返回值形态
    ----------
    - 成功：checkpoint.values —— 经 JSON 序列化/反序列化还原后的
      AgentState 字典（features / check_items 等是普通 JSON 数据；
      messages 可能是 LangChain 消息对象或 dict，两种形态的兼容处理
      见 _build_session_response 的双分支）；
    - 失败：None —— 两种可能：
      1) 该 thread_id 从未写入过 checkpoint（会话未创建或已删除）；
      2) checkpoint 存在但 values 为空（异常快照，防御性兜底）。

    注：如需历史版本可改用 graph.get_state_history() 遍历旧 checkpoint，
        本函数只关心「最新」状态。
        get_state_history -- 取所有
        get_state -- 取最新一条
    """
    graph = get_graph()
    # thread_id == session_id：会话与 LangGraph 线程一一对应
    config = _thread_config(session_id)
    # get_state 按 checkpoint_id 倒序取最新一条快照，不执行图
    checkpoint = graph.get_state(config)
    # get_state 找不到线程时返回 None；values 为空属防御性兜底
    if checkpoint is None or checkpoint.values is None:
        return None
    return checkpoint.values


def run_graph(session_id: str, state: AgentState) -> AgentState:
    """运行图。"""
    graph = get_graph()
    return graph.invoke(state, _thread_config(session_id))


def resume_graph(session_id: str, state: AgentState, approval: Literal["approved", "rejected"]) -> AgentState:
    """从审核中断点恢复。"""
    graph = get_graph()
    state["approval"] = approval
    return graph.invoke(state, _thread_config(session_id))


def update_state(session_id: str, state: AgentState, updates: dict) -> AgentState:
    """更新状态并持久化到 checkpointer（不触发节点执行）。"""
    state.update(updates)
    graph = get_graph()
    config = _thread_config(session_id)
    graph.update_state(config, updates)
    result = graph.get_state(config)
    return result.values if result else state


def list_sessions() -> list[dict]:
    """列出所有会话（从 checkpointer 数据库）。"""
    graph = get_graph()
    saver = graph.checkpointer
    conn = saver.conn
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id DESC LIMIT 100"
        )
        rows = cur.fetchall()
    result = []
    for row in rows:
        tid = row[0]
        state = get_session_state(tid) if hasattr(row, '__getitem__') else get_session_state(row)
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
