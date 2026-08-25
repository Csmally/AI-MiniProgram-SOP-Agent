"""REST API 路由定义 — 多 Agent 架构版。

操作模式：REST = 触发 action（invoke_action）+ 读取 checkpoint 状态。
phase 的唯一来源是后端 checkpoint，前端不再本地推断。

认证与隔离：所有会话端点挂 get_current_user（JWT Bearer）依赖；
会话归属由 session_owners 表校验（_get_owned_state），非本人会话
（含无主旧会话）统一 404，不暴露存在性。

并发约定：
- 非 SSE 端点一律用同步 `def` —— FastAPI 自动在线程池执行，
  同步图调用（含 LLM，30~70s）不会阻塞 uvicorn 事件循环；
- SSE 端点（run/stream、chat/stream）保持 `async def`：worker 线程跑图，
  事件经 asyncio.Queue + call_soon_threadsafe 桥接；
- 每会话互斥锁保证同一 session 同时只有一个图执行（并发请求 409）。
"""

import asyncio
import json
import threading
import uuid
from contextlib import contextmanager

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from ..agents import chat_agent
from ..core import auth_store, orchestrator
from ..core.state import create_initial_state
from ..tracing import delete_session_traces
from ..sop.models import (
    SessionResponse,
    ParseResultResponse,
    ChecklistResponse,
    ChatRequest,
    UpdateCheckItemRequest,
    CreateCheckItemRequest,
    StreamRunRequest,
)
from .auth import get_current_user

router = APIRouter(prefix="/api")


# ──────────────────────────────────────────────
# 会话归属守卫（认证后的第二道闸：session 必须是本人的）
# ──────────────────────────────────────────────

def _get_owned_state(session_id: str, user: dict) -> dict:
    """会话存在 + 归属校验，返回最新 checkpoint 状态。

    统一 404：非本人会话（含无主旧会话）与不存在的会话返回相同错误，
    不暴露他人会话的存在性。
    """
    state = orchestrator.get_session_state(session_id)
    if state is None or not auth_store.is_owned(session_id, user["id"]):
        raise HTTPException(status_code=404, detail="会话不存在")
    return state


# ──────────────────────────────────────────────
# 会话管理
# ──────────────────────────────────────────────

@router.post("/sessions", response_model=SessionResponse)
def create_session(user: dict = Depends(get_current_user)):
    # 只落盘初始状态，不跑图（修复旧版创建即跑图导致 phase 错乱的 bug）
    session_id = uuid.uuid4().hex[:12]
    state = orchestrator.update_state(session_id, create_initial_state(session_id, str(user["id"])))
    auth_store.register_owner(session_id, user["id"])   # 归属即刻生效
    return _build_session_response(state)


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: str, user: dict = Depends(get_current_user)):
    return _build_session_response(_get_owned_state(session_id, user))


@router.delete("/sessions/{session_id}")
def del_session(session_id: str, user: dict = Depends(get_current_user)):
    _get_owned_state(session_id, user)
    if not orchestrator.delete_session(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    auth_store.remove_owner(session_id)
    delete_session_traces(session_id)   # 调用链数据级联清理
    return {"message": "删除成功", "session_id": session_id}


@router.get("/sessions")
def get_sessions(user: dict = Depends(get_current_user)):
    sessions = orchestrator.list_sessions(user["id"])
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
        agent_progress=state.get("agent_progress", []),
        messages=msgs,
    )


# ──────────────────────────────────────────────
# PRD 上传与解析（链式：解析 → 生成清单 → 审核中断）
# ──────────────────────────────────────────────

@router.post("/sessions/{session_id}/prd", response_model=ParseResultResponse)
def upload_prd(session_id: str, file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    _get_owned_state(session_id, user)

    content = file.file.read().decode("utf-8")
    with _session_exec(session_id):
        result = orchestrator.invoke_action(
            session_id, "upload_prd",
            {
                "prd_content": content,
                # 上传动作也记入聊天记录（消息经 add_messages 追加，刷新后仍在）
                "messages": [HumanMessage(content=f"[上传 PRD: {file.filename}]")],
            },
            user_id=user["id"],
        )

    return ParseResultResponse(
        session_id=session_id,
        features=result.get("features", []),
        message=f"已解析出 {len(result.get('features', []))} 个功能",
    )


# ──────────────────────────────────────────────
# SOP 生成（重新生成 / 审核拒绝后重跑）
# ──────────────────────────────────────────────

@router.post("/sessions/{session_id}/generate", response_model=ChecklistResponse)
def generate_checklist(session_id: str, user: dict = Depends(get_current_user)):
    _get_owned_state(session_id, user)

    with _session_exec(session_id):
        result = orchestrator.invoke_action(session_id, "generate_sop", user_id=user["id"])
    return ChecklistResponse(
        session_id=session_id,
        check_items=result.get("check_items", []),
        message=f"已生成 {len(result.get('check_items', []))} 个检查项",
    )


@router.post("/sessions/{session_id}/approve", response_model=SessionResponse)
def approve_checklist(session_id: str, user: dict = Depends(get_current_user)):
    _get_owned_state(session_id, user)

    with _session_exec(session_id):
        result = orchestrator.invoke_action(
            session_id, "approve", {"approval": "approved"}, user_id=user["id"])
    return _build_session_response(result)


# ──────────────────────────────────────────────
# 检查项管理（持久化式更新，不触发图）
# ──────────────────────────────────────────────

@router.put("/sessions/{session_id}/check-items/{item_id}")
def update_check_item(session_id: str, item_id: str, body: UpdateCheckItemRequest,
                      user: dict = Depends(get_current_user)):
    _get_owned_state(session_id, user)

    # 读-改-写全程持锁，避免与其他写操作交叉
    with _session_exec(session_id):
        state = orchestrator.get_session_state(session_id)
        items = state.get("check_items", [])
        for item in items:
            if item.get("id") == item_id:
                if body.description is not None: item["description"] = body.description
                if body.priority is not None: item["priority"] = body.priority
                if body.check_steps is not None: item["check_steps"] = body.check_steps
                if body.expected_result is not None: item["expected_result"] = body.expected_result
                break
        orchestrator.update_state(session_id, {"check_items": items})
    return {"message": "更新成功", "item_id": item_id}


@router.delete("/sessions/{session_id}/check-items/{item_id}")
def del_check_item(session_id: str, item_id: str, user: dict = Depends(get_current_user)):
    _get_owned_state(session_id, user)
    with _session_exec(session_id):
        state = orchestrator.get_session_state(session_id)
        items = [i for i in state.get("check_items", []) if i.get("id") != item_id]
        orchestrator.update_state(session_id, {"check_items": items})
    return {"message": "删除成功", "item_id": item_id}


@router.post("/sessions/{session_id}/check-items")
def add_check_item(session_id: str, body: CreateCheckItemRequest,
                   user: dict = Depends(get_current_user)):
    _get_owned_state(session_id, user)
    with _session_exec(session_id):
        state = orchestrator.get_session_state(session_id)
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
        orchestrator.update_state(session_id, {"check_items": items})
    return {"message": "新增成功", "item": new_item}


# ──────────────────────────────────────────────
# AI 对话
# ──────────────────────────────────────────────

@router.post("/sessions/{session_id}/chat/stream")
async def chat_stream(session_id: str, body: ChatRequest,
                      user: dict = Depends(get_current_user)):
    """流式聊天 — 经主图调用 chat_agent（聊天逻辑唯一实现）。

    同步图在 worker 线程执行，chat_agent 节点内 llm.stream 逐 token 推送，
    经 thread-local 钩子 + call_soon_threadsafe 桥接成 SSE。前端 wire 格式不变。
    """
    _get_owned_state(session_id, user)

    lock = _session_lock(session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该会话正在执行中，请稍后再试")

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def worker():
        try:
            chat_agent.register_stream_hook(
                lambda token: loop.call_soon_threadsafe(queue.put_nowait, ("token", token))
            )
            orchestrator.invoke_action(
                session_id, "chat",
                {"messages": [HumanMessage(content=body.message)]},
                user_id=user["id"],
            )
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc)))
        finally:
            chat_agent.unregister_stream_hook()
            lock.release()
            loop.call_soon_threadsafe(queue.put_nowait, ("__end__", None))

    threading.Thread(target=worker, daemon=True).start()

    async def generate():
        try:
            while True:
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # SSE 注释行，前端自动忽略
                    continue
                if kind == "__end__":
                    break
                if kind == "error":
                    # 以纯文本 token 形式进入回复气泡，前端无需特殊处理
                    yield f"data: 回复失败: {data}\n\n"
                    break
                if kind == "token":
                    yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            raise  # 客户端断开：worker 继续跑完，消息仍持久化
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ──────────────────────────────────────────────
# SSE 流式执行（Agent 实时进度）
# ──────────────────────────────────────────────

_session_locks: dict[str, threading.Lock] = {}
_session_locks_guard = threading.Lock()


def _session_lock(session_id: str) -> threading.Lock:
    with _session_locks_guard:
        return _session_locks.setdefault(session_id, threading.Lock())


@contextmanager
def _session_exec(session_id: str):
    """同一会话的写操作串行化（def 端点用）。

    不同会话互不影响（各持各的锁）；同一会话等待前一个操作结束，
    超时 5 分钟仍拿不到锁返回 409。SSE 端点沿用非阻塞 acquire（立即 409）。
    """
    lock = _session_lock(session_id)
    if not lock.acquire(timeout=300):
        raise HTTPException(status_code=409, detail="该会话正在执行中，请稍后再试")
    try:
        yield
    finally:
        lock.release()


def _msg_to_dict(m) -> dict:
    if isinstance(m, dict):
        role = m.get("role", "assistant")
        if role == "ai": role = "assistant"
        if role == "human": role = "user"
        return {"role": role, "content": m.get("content", "")}
    role = "user" if getattr(m, "type", "") == "human" else "assistant"
    return {"role": role, "content": getattr(m, "content", "")}


def _serialize_state(state: dict) -> dict:
    """把 checkpoint 状态转为 JSON 可序列化的 dict。"""
    out = dict(state)
    out["messages"] = [_msg_to_dict(m) for m in state.get("messages", [])]
    return out


def _extract_events(chunk: dict) -> list[dict]:
    """把 graph.stream 的 updates chunk（{node: writes}）翻译成 SSE 事件。"""
    events = []
    for node, writes in chunk.items():
        if node == "dispatch":
            if writes.get("current_phase"):
                events.append({
                    "type": "phase", "phase": writes["current_phase"],
                    "run_id": writes.get("run_id", ""),
                })
        elif node == "execute_agent":
            for p in writes.get("agent_progress", []):
                events.append({
                    "type": "item",
                    "agent": p.get("agent", "executor"),
                    "item_id": p.get("item_id"),
                    "status": p.get("status"),
                    "run_id": p.get("run_id", ""),
                })
        elif node == "generate_report":
            if writes.get("report_content"):
                events.append({"type": "report", "content": writes["report_content"]})
            if writes.get("current_phase"):
                events.append({"type": "phase", "phase": writes["current_phase"]})
        elif node in ("parse_prd", "generate_sop", "chat_agent"):
            for m in writes.get("messages", []):
                if hasattr(m, "content"):
                    events.append({"type": "message", "role": "assistant", "content": m.content})
    return events


@router.post("/sessions/{session_id}/run/stream")
async def stream_run(session_id: str, body: StreamRunRequest,
                     user: dict = Depends(get_current_user)):
    _get_owned_state(session_id, user)

    lock = _session_lock(session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该会话正在执行中，请稍后再试")

    action = body.next_action or "run"
    updates = {"approval": body.approval} if body.approval else None

    # 同步图只在 worker 线程跑（Windows 事件循环不受影响），
    # 事件经 call_soon_threadsafe 桥接到 asyncio.Queue。
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def worker():
        try:
            for kind, data in orchestrator.stream_action(session_id, action, updates,
                                                         user_id=user["id"]):
                loop.call_soon_threadsafe(queue.put_nowait, (kind, data))
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc)))
        finally:
            lock.release()
            loop.call_soon_threadsafe(queue.put_nowait, ("__end__", None))

    threading.Thread(target=worker, daemon=True).start()

    async def generate():
        try:
            while True:
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"  # 心跳防代理超时
                    continue
                if kind == "__end__":
                    break
                if kind == "error":
                    yield f"data: {json.dumps({'type': 'error', 'message': data}, ensure_ascii=False)}\n\n"
                    break
                if kind == "done":
                    payload = {"type": "done", "state": _serialize_state(data) if data else None}
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    break
                for event in _extract_events(data):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            raise  # 客户端断开：worker 继续跑完并落 checkpoint，状态仍持久化
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
