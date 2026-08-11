"""REST API 路由定义。"""

from fastapi import APIRouter, UploadFile, File, HTTPException
from ..core.state import session_store, SessionPhase
from ..core.orchestrator import run_graph, resume_graph, update_state
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
    """创建新的检查会话。"""
    state = session_store.create()
    return SessionResponse(
        session_id=state["session_id"],
        current_phase=state["current_phase"],
        features_count=0,
        check_items_count=0,
        messages=state["messages"],
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str):
    """获取会话状态。"""
    state = session_store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    return SessionResponse(
        session_id=state["session_id"],
        current_phase=state["current_phase"],
        features_count=len(state.get("features", [])),
        check_items_count=len(state.get("check_items", [])),
        messages=state.get("messages", []),
    )


# ──────────────────────────────────────────────
# PRD 上传与解析
# ──────────────────────────────────────────────

@router.post("/sessions/{session_id}/prd", response_model=ParseResultResponse)
async def upload_prd(session_id: str, file: UploadFile = File(...)):
    """上传 PRD 文件并触发解析。"""
    state = session_store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 读取文件内容
    content = await file.read()
    prd_text = content.decode("utf-8")

    # 更新状态
    state["prd_content"] = prd_text
    session_store.update(session_id, state)

    # 运行图
    result = await run_graph(session_id)

    if result.get("error"):
        return ParseResultResponse(
            session_id=session_id,
            features=[],
            message=f"解析失败：{result['error']}",
        )

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
    """从当前审核点恢复，进入生成（重新生成清单）。"""
    state = session_store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 如果当前在审核阶段且被拒绝，重新运行
    result = await resume_graph(session_id, "rejected")

    return ChecklistResponse(
        session_id=session_id,
        check_items=result.get("check_items", []),
        message=f"已生成 {len(result.get('check_items', []))} 个检查项",
    )


@router.post("/sessions/{session_id}/approve", response_model=SessionResponse)
async def approve_checklist(session_id: str):
    """用户确认检查清单，继续执行。"""
    result = await resume_graph(session_id, "approved")

    return SessionResponse(
        session_id=session_id,
        current_phase=result["current_phase"],
        features_count=len(result.get("features", [])),
        check_items_count=len(result.get("check_items", [])),
        messages=result.get("messages", []),
    )


# ──────────────────────────────────────────────
# 检查项管理
# ──────────────────────────────────────────────

@router.get("/sessions/{session_id}/check-items", response_model=ChecklistResponse)
async def get_check_items(session_id: str):
    """获取检查项列表。"""
    state = session_store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    return ChecklistResponse(
        session_id=session_id,
        check_items=state.get("check_items", []),
        message=f"共 {len(state.get('check_items', []))} 个检查项",
    )


@router.put("/sessions/{session_id}/check-items/{item_id}")
async def update_check_item(session_id: str, item_id: str, body: UpdateCheckItemRequest):
    """修改检查项。"""
    state = session_store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    items = state.get("check_items", [])
    updated = False
    for item in items:
        if item.get("id") == item_id:
            if body.description is not None:
                item["description"] = body.description
            if body.priority is not None:
                item["priority"] = body.priority
            if body.check_steps is not None:
                item["check_steps"] = body.check_steps
            if body.expected_result is not None:
                item["expected_result"] = body.expected_result
            updated = True
            break

    if not updated:
        raise HTTPException(status_code=404, detail="检查项不存在")

    update_state(session_id, {"check_items": items})
    return {"message": "更新成功", "item_id": item_id}


@router.delete("/sessions/{session_id}/check-items/{item_id}")
async def delete_check_item(session_id: str, item_id: str):
    """删除检查项。"""
    state = session_store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    items = state.get("check_items", [])
    items = [item for item in items if item.get("id") != item_id]
    update_state(session_id, {"check_items": items})
    return {"message": "删除成功", "item_id": item_id}


@router.post("/sessions/{session_id}/check-items")
async def create_check_item(session_id: str, body: CreateCheckItemRequest):
    """手动新增检查项。"""
    state = session_store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    import uuid

    new_item = {
        "id": uuid.uuid4().hex[:8],
        "category": body.category,
        "description": body.description,
        "priority": body.priority,
        "check_steps": body.check_steps,
        "expected_result": body.expected_result,
        "status": "pending",
        "screenshots": [],
        "result_detail": None,
    }
    items = state.get("check_items", [])
    items.append(new_item)
    update_state(session_id, {"check_items": items})
    return {"message": "新增成功", "item": new_item}


# ──────────────────────────────────────────────
# 检查执行
# ──────────────────────────────────────────────

@router.post("/sessions/{session_id}/run", response_model=RunResponse)
async def run_checks(session_id: str):
    """开始执行检查。"""
    state = session_store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    result = await run_graph(session_id)

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
    """获取检查报告。"""
    state = session_store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    check_results = state.get("check_results", [])
    total = len(check_results)
    passed = sum(1 for r in check_results if r.get("status") == "passed")
    failed = sum(1 for r in check_results if r.get("status") == "failed")

    return ReportResponse(
        session_id=session_id,
        report_content=state.get("report_content", ""),
        summary={
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": f"{passed/total*100:.0f}%" if total > 0 else "N/A",
        },
    )


# ──────────────────────────────────────────────
# AI 对话
# ──────────────────────────────────────────────

@router.post("/sessions/{session_id}/chat", response_model=ChatResponse)
async def chat(session_id: str, body: ChatRequest):
    """与 AI 模型对话。"""
    state = session_store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    from ..core.orchestrator import _get_llm

    # 构建对话上下文
    messages = state.get("messages", [])[-10:]  # 最近 10 条
    history = "\n".join(
        f"{'用户' if m['role']=='user' else '助手'}: {m['content'][:200]}"
        for m in messages
    )

    llm = _get_llm("chat")
    prompt = (
        f"你是一个微信小程序 SOP 检查助手。当前会话正在处理 PRD 文档。\n\n"
        f"对话历史：\n{history}\n\n"
        f"用户提问：{body.message}\n\n"
        f"请简洁回答，帮助用户完成 SOP 检查。"
    )
    response = await llm.ainvoke(prompt)
    reply = response.content.strip()

    # 保存对话
    messages.append({"role": "user", "content": body.message})
    messages.append({"role": "assistant", "content": reply})
    update_state(session_id, {"messages": messages})

    return ChatResponse(reply=reply, session_id=session_id)
