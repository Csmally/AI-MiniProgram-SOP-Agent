"""认证路由 — 注册 / 登录 / 当前用户 + get_current_user 依赖（JWT Bearer）。"""

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from ..core import auth_store, security

router = APIRouter(prefix="/api/auth", tags=["auth"])

_bearer = HTTPBearer(auto_error=False)


class AuthRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class AuthResponse(BaseModel):
    token: str
    username: str


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """解析 Authorization: Bearer <token> → 用户 dict {id, username}；无效/缺失 → 401。"""
    if credentials is None:
        raise HTTPException(401, "未登录")
    try:
        payload = security.decode_token(credentials.credentials)
    except pyjwt.InvalidTokenError:
        raise HTTPException(401, "登录已过期或无效，请重新登录") from None
    user = auth_store.get_user_by_id(payload["user_id"])
    if user is None:
        raise HTTPException(401, "账号不存在") from None
    return user


@router.post("/register", response_model=AuthResponse)
def register(body: AuthRequest):
    """注册新用户（用户名唯一、密码 ≥ 6 位），成功后直接签发令牌（免二次登录）。"""
    if auth_store.get_user_by_username(body.username):
        raise HTTPException(409, f"用户名 {body.username!r} 已被注册")
    user_id = auth_store.create_user(body.username, security.hash_password(body.password))
    return {"token": security.create_token(user_id, body.username), "username": body.username}


@router.post("/login", response_model=AuthResponse)
def login(body: AuthRequest):
    """登录：校验用户名密码，签发令牌。"""
    user = auth_store.get_user_by_username(body.username)
    if user is None or not security.verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    return {"token": security.create_token(user["id"], user["username"]), "username": user["username"]}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    """当前登录用户信息（前端启动时用 token 恢复会话）。"""
    return {"user_id": user["id"], "username": user["username"]}
