"""认证安全单测 — 密码哈希 / JWT 签发解析（纯函数，无网络无 DB）。"""

import time

import jwt as pyjwt
import pytest

from sop_agent.core import security


class TestPasswordHash:
    def test_roundtrip(self):
        stored = security.hash_password("secret123")
        assert stored.startswith("pbkdf2_sha256$200000$")
        assert security.verify_password("secret123", stored) is True

    def test_wrong_password_fails(self):
        stored = security.hash_password("secret123")
        assert security.verify_password("wrong-pass", stored) is False

    def test_same_password_different_salts(self):
        """相同密码两次哈希结果不同（随机 salt），且互相可验证。"""
        a = security.hash_password("secret123")
        b = security.hash_password("secret123")
        assert a != b
        assert security.verify_password("secret123", a) is True
        assert security.verify_password("secret123", b) is True

    def test_malformed_stored_returns_false(self):
        for bad in ("", "plaintext", "md5$1$2$3", "pbkdf2_sha256$abc$!!$??"):
            assert security.verify_password("secret123", bad) is False


class TestJwt:
    def test_roundtrip(self):
        token = security.create_token(42, "alice")
        payload = security.decode_token(token)
        assert payload == {"user_id": 42, "username": "alice"}

    def test_tampered_token_rejected(self):
        token = security.create_token(42, "alice")
        tampered = token[:-2] + ("aa" if token[-2:] != "aa" else "bb")
        with pytest.raises(pyjwt.InvalidTokenError):
            security.decode_token(tampered)

    def test_expired_token_rejected(self, monkeypatch):
        monkeypatch.setattr(security.time, "time", lambda: 0)          # 签发时间 = 0
        token = security.create_token(1, "alice")
        monkeypatch.setattr(security.time, "time", lambda: 8 * 86400)  # 8 天后（默认 7 天有效期）
        with pytest.raises(pyjwt.InvalidTokenError):
            security.decode_token(token)
