"""Pydantic 数据模型 — Feature、CheckItem、API 请求/响应。"""

import uuid
from typing import Literal, Optional
from pydantic import BaseModel, Field


class Feature(BaseModel):
    """PRD 中提取的功能信息。"""
    name: str = Field(..., description="功能名称")
    description: str = Field(..., description="功能描述")
    affected_pages: list[str] = Field(default_factory=list, description="涉及页面")
    api_endpoints: list[str] = Field(default_factory=list, description="相关 API 接口")
    ui_elements: list[str] = Field(default_factory=list, description="关键 UI 元素")
    acceptance_criteria: list[str] = Field(default_factory=list, description="验收标准")


class CheckItem(BaseModel):
    """SOP 检查项。"""
    id: str = Field(
        default_factory=lambda: uuid.uuid4().hex[:8],
        description="检查项唯一 ID",
    )
    category: Literal["ui", "api"] = Field(..., description="检查类别：UI 或 API")
    description: str = Field(..., description="检查项描述")
    priority: Literal["critical", "high", "medium", "low"] = Field(
        "medium", description="优先级"
    )
    check_steps: list[str] = Field(default_factory=list, description="检查步骤")
    expected_result: str = Field("", description="预期结果")
    status: Literal["pending", "running", "passed", "failed", "skipped"] = Field(
        "pending", description="执行状态"
    )
    result_detail: Optional[str] = Field(None, description="结果详情")
    screenshots: list[str] = Field(default_factory=list, description="截图文件名列表")


# -----------------------------
# API 请求模型
# -----------------------------

class ChatRequest(BaseModel):
    """/api/sessions/{id}/chat 请求体。"""
    message: str = Field(..., min_length=1, description="用户消息")


class UpdateCheckItemRequest(BaseModel):
    """修改检查项的请求体。"""
    description: Optional[str] = None
    priority: Optional[Literal["critical", "high", "medium", "low"]] = None
    check_steps: Optional[list[str]] = None
    expected_result: Optional[str] = None


class CreateCheckItemRequest(BaseModel):
    """手动新增检查项的请求体。"""
    category: Literal["ui", "api"]
    description: str = Field(..., min_length=1)
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    check_steps: list[str] = Field(default_factory=list)
    expected_result: str = ""


# -----------------------------
# API 响应模型
# -----------------------------

class SessionResponse(BaseModel):
    """会话信息响应（完整状态，前端可恢复）。"""
    session_id: str
    current_phase: str
    features: list[dict] = []
    check_items: list[dict] = []
    check_results: list[dict] = []
    report_content: str = ""
    messages: list[dict] = []


class ParseResultResponse(BaseModel):
    """PRD 解析结果响应。"""
    session_id: str
    features: list[Feature]
    message: str


class ChecklistResponse(BaseModel):
    """检查清单响应。"""
    session_id: str
    check_items: list[CheckItem]
    message: str


class ChatResponse(BaseModel):
    """对话响应。"""
    reply: str
    session_id: str


class RunResponse(BaseModel):
    """检查执行响应。"""
    session_id: str
    message: str
    total_items: int


class ReportResponse(BaseModel):
    """报告响应。"""
    session_id: str
    report_content: str
    summary: dict
