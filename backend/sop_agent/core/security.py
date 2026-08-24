"""认证安全工具 — 密码哈希（stdlib PBKDF2）+ JWT 签发/解析（pyjwt）。

零新增密码库：hashlib.pbkdf2_hmac 是标准库实现，200k 迭代；存储格式
pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>，verify 兼容未来调整迭代数。
"""

import base64
import hashlib
import hmac
import secrets
import time

import jwt

from .config import get_settings

PBKDF2_ITERATIONS = 200_000


# ──────────────────────────────────────────────
# 密码哈希
# ──────────────────────────────────────────────

def hash_password(password: str) -> str:
    """生成密码哈希（随机 salt，200k 次 PBKDF2-SHA256）。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    """校验密码（格式不符/哈希不匹配一律 False，不区分错误原因）。"""
    try:
        algo, iterations_s, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iterations_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except Exception:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


# ──────────────────────────────────────────────
# JWT
# ──────────────────────────────────────────────

def create_token(user_id: int, username: str) -> str:
    """签发登录令牌（HS256，payload: sub/username/exp）。"""
    settings = get_settings()
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": int(time.time()) + settings.JWT_EXPIRE_DAYS * 86400,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict:
    """解析令牌，返回 {user_id, username}；签名错误/过期/格式错误抛 jwt.InvalidTokenError。"""
    settings = get_settings()
    payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    return {"user_id": int(payload["sub"]), "username": payload.get("username", "")}
