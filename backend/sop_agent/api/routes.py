"""REST API 路由定义。"""

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from ..core.state import AgentState, create_initial_state, SessionPhase
from ..core.orchestrator import (
    run_graph, resume_graph, update_state,
    save_new_session, get_session_state, list_sessions, delete_session,
)
from ..sop.models import (
    SessionResponse,
    ParseResultResponse,
    ChecklistResponse,
    ChatResponse,
    ChatRequest,
    UpdateCheckItemRequest,
    CreateCheckItemRequest,
    RunResponse,
    ReportResponse,
)

router = APIRouter(prefix="/api")


# ──────────────────────────────────────────────
# 会话管理
# ──────────────────────────────────────────────

@router.post("/sessions", response_model=SessionResponse)
async def create_session():
    state = create_initial_state()
    save_new_session(state)
    return _build_session_response(state)


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str):
    state = get_session_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return _build_session_response(state)


@router.delete("/sessions/{session_id}")
async def del_session(session_id: str):
    if not delete_session(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"message": "删除成功", "session_id": session_id}


@router.get("/sessions")
async def get_sessions():
    sessions = list_sessions()
    return {"sessions": sessions, "total": len(sessions)}


def _build_session_response(state: dict) -> SessionResponse:
    msgs = []
    for m in state.get("messages", []):
        if hasattr(m, "type") and hasattr(m, "content"):
            role = "user" if m.type == "human" else "assistant"
            msgs.append({"role": role, "content": m.content})
        elif isinstance(m, dict):
            role = m.get("role", "assistant")
            if role == "ai": role = "assistant"
            if role == "human": role = "user"
            msgs.append({"role": role, "content": m.get("content", "")})

    return SessionResponse(
        session_id=state.get("session_id", ""),
        current_phase=state.get("current_phase", "idle"),
        features=state.get("features", []),
        check_items=state.get("check_items", []),
        check_results=state.get("check_results", []),
        report_content=state.get("report_content", ""),
        messages=msgs,
    )


# ──────────────────────────────────────────────
# PRD 上传与解析
# ──────────────────────────────────────────────

@router.post("/sessions/{session_id}/prd", response_model=ParseResultResponse)
async def upload_prd(session_id: str, file: UploadFile = File(...)):
    state = get_session_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    content = await file.read()
    state["prd_content"] = content.decode("utf-8")
    result = run_graph(session_id, state)

    if result.get("error"):
        return ParseResultResponse(session_id=session_id, features=[], message=f"解析失败：{result['error']}")

    return ParseResultResponse(
        session_id=session_id,
        features=result.get("features", []),
        message=f"已解析出 {len(result.get('features', []))} 个功能",
    )


# ──────────────────────────────────────────────
# SOP 生成
# ──────────────────────────────────────────────

@router.post("/sessions/{session_id}/generate", response_model=ChecklistResponse)
async def generate_checklist(session_id: str):
    state = get_session_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    result = resume_graph(session_id, state, "rejected")
    return ChecklistResponse(
        session_id=session_id,
        check_items=result.get("check_items", []),
        message=f"已生成 {len(result.get('check_items', []))} 个检查项",
    )


@router.post("/sessions/{session_id}/approve", response_model=SessionResponse)
async def approve_checklist(session_id: str):
    state = get_session_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    result = resume_graph(session_id, state, "approved")
    return _build_session_response(result)


# ──────────────────────────────────────────────
# 检查项管理
# ──────────────────────────────────────────────

@router.get("/sessions/{session_id}/check-items", response_model=ChecklistResponse)
async def get_check_items(session_id: str):
    state = get_session_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return ChecklistResponse(
        session_id=session_id,
        check_items=state.get("check_items", []),
        message=f"共 {len(state.get('check_items', []))} 个检查项",
    )


@router.put("/sessions/{session_id}/check-items/{item_id}")
async def update_check_item(session_id: str, item_id: str, body: UpdateCheckItemRequest):
    state = get_session_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    items = state.get("check_items", [])
    for item in items:
        if item.get("id") == item_id:
            if body.description is not None: item["description"] = body.description
            if body.priority is not None: item["priority"] = body.priority
            if body.check_steps is not None: item["check_steps"] = body.check_steps
            if body.expected_result is not None: item["expected_result"] = body.expected_result
            break
    update_state(session_id, state, {"check_items": items})
    return {"message": "更新成功", "item_id": item_id}


@router.delete("/sessions/{session_id}/check-items/{item_id}")
async def del_check_item(session_id: str, item_id: str):
    state = get_session_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    items = [i for i in state.get("check_items", []) if i.get("id") != item_id]
    update_state(session_id, state, {"check_items": items})
    return {"message": "删除成功", "item_id": item_id}


@router.post("/sessions/{session_id}/check-items")
async def add_check_item(session_id: str, body: CreateCheckItemRequest):
    state = get_session_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    new_item = {
        "id": uuid.uuid4().hex[:8],
        "category": body.category,
        "description": body.description,
        "priority": body.priority,
        "check_steps": body.check_steps,
        "expected_result": body.expected_result,
        "status": "pending", "screenshots": [], "result_detail": None,
    }
    items = state.get("check_items", []) + [new_item]
    update_state(session_id, state, {"check_items": items})
    return {"message": "新增成功", "item": new_item}


# ──────────────────────────────────────────────
# 检查执行
# ──────────────────────────────────────────────

@router.post("/sessions/{session_id}/run", response_model=RunResponse)
async def run_checks(session_id: str):
    state = get_session_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    result = run_graph(session_id, state)
    return RunResponse(
        session_id=session_id,
        message="检查执行完成",
        total_items=len(result.get("check_results", [])),
    )


# ──────────────────────────────────────────────
# 报告
# ──────────────────────────────────────────────

@router.get("/sessions/{session_id}/report", response_model=ReportResponse)
async def get_report(session_id: str):
    state = get_session_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    results = state.get("check_results", [])
    total = len(results)
    passed = sum(1 for r in results if r.get("status") == "passed")
    failed = sum(1 for r in results if r.get("status") == "failed")
    return ReportResponse(
        session_id=session_id,
        report_content=state.get("report_content", ""),
        summary={"total": total, "passed": passed, "failed": failed,
                  "pass_rate": f"{passed/total*100:.0f}%" if total > 0 else "N/A"},
    )


# ──────────────────────────────────────────────
# AI 对话
# ──────────────────────────────────────────────

@router.post("/sessions/{session_id}/chat", response_model=ChatResponse)
async def chat(session_id: str, body: ChatRequest):
    state = get_session_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    from ..core.orchestrator import _get_llm

    messages = state.get("messages", [])[-10:]
    history_lines = []
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "type", "unknown")
        content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
        history_lines.append(f"{'用户' if role in ('human','user') else '助手'}: {content[:200]}")
    history = "\n".join(history_lines)

    llm = _get_llm("chat")
    prompt = (
        f"你是一个微信小程序 SOP 检查助手。\n\n"
        f"对话历史：\n{history}\n\n"
        f"用户提问：{body.message}\n\n请简洁回答。"
    )
    response = llm.invoke(prompt)
    reply = response.content.strip()

    messages = list(messages)
    messages.append(HumanMessage(content=body.message))
    messages.append(AIMessage(content=reply))
    update_state(session_id, state, {"messages": messages})

    return ChatResponse(reply=reply, session_id=session_id)


@router.post("/sessions/{session_id}/chat/stream")
async def chat_stream(session_id: str, body: ChatRequest):
    state = get_session_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    from ..core.orchestrator import _get_llm

    messages = state.get("messages", [])[-10:]
    history_lines = []
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "type", "unknown")
        content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
        history_lines.append(f"{'用户' if role in ('human','user') else '助手'}: {content[:200]}")
    history = "\n".join(history_lines)

    llm = _get_llm("chat")
    prompt = (
        f"你是一个微信小程序 SOP 检查助手。\n\n"
        f"对话历史：\n{history}\n\n"
        f"用户提问：{body.message}\n\n请简洁回答。"
    )

    async def generate():
        full_reply = ""
        async for chunk in llm.astream(prompt):
            token = chunk.content if hasattr(chunk, 'content') else str(chunk)
            if token:
                full_reply += token
                yield f"data: {token}\n\n"

        messages = list(state.get("messages", []))
        messages.append(HumanMessage(content=body.message))
        messages.append(AIMessage(content=full_reply))
        update_state(session_id, state, {"messages": messages})
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


import uuid
from langchain_core.messages import HumanMessage, AIMessage
