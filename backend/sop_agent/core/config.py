"""配置管理 — 从环境变量/.env 读取，简洁命名。"""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


class Settings:
    """应用配置。"""

    # ─────────────────────────────────────
    # DeepSeek
    # ─────────────────────────────────────
    DEEPSEEK_API_KEY: str = _env("DEEPSEEK_API_KEY")
    DEEPSEEK_LLM_URL: str = _env("DEEPSEEK_LLM_URL", "https://api.deepseek.com")
    DEEPSEEK_V4_PRO_MODEL_NAME: str = _env("DEEPSEEK_V4_PRO_MODEL_NAME", "deepseek-v4-pro")
    DEEPSEEK_V4_FLASH_MODEL_NAME: str = _env("DEEPSEEK_V4_FLASH_MODEL_NAME", "deepseek-v4-flash")

    # ─────────────────────────────────────
    # Qwen
    # ─────────────────────────────────────
    QWEN_API_KEY: str = _env("QWEN_API_KEY")
    QWEN_LLM_URL: str = _env("QWEN_LLM_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    QWEN3_7_FLASH_MODEL_NAME: str = _env("QWEN3_7_FLASH_MODEL_NAME", "qwen3.7-flash")

    # ─────────────────────────────────────
    # 任务路由：TASK_<任务名>_MODEL → model_key
    # ─────────────────────────────────────
    @property
    def MODEL_ROUTING(self) -> dict[str, str]:
        """任务 → model_key 映射，直接从环境变量读取。"""
        return {
            "parse_prd": self.DEEPSEEK_V4_PRO_MODEL_NAME,
            "generate_sop": self.DEEPSEEK_V4_PRO_MODEL_NAME,
            "chat": self.DEEPSEEK_V4_FLASH_MODEL_NAME,
            "execute_checks": self.DEEPSEEK_V4_PRO_MODEL_NAME,
            "screenshot_analysis": self.QWEN3_7_FLASH_MODEL_NAME,
            "generate_report": self.DEEPSEEK_V4_FLASH_MODEL_NAME,
        }

    # ─────────────────────────────────────
    # 微信开发者工具（minium 自动化）
    # ─────────────────────────────────────
    MINIUM_PROJECT_PATH: str = _env("MINIUM_PROJECT_PATH")
    MINIUM_DEV_TOOL_PATH: str = _env("MINIUM_DEV_TOOL_PATH")
    MINIUM_TEST_PORT: int = int(_env("MINIUM_TEST_PORT", "9420"))

    # ─────────────────────────────────────
    # MCP server（minium 工具独立进程，可选）
    # ─────────────────────────────────────
    MCP_ENABLED: bool = _env("MCP_ENABLED").lower() == "true"
    MCP_SERVER_URL: str = _env("MCP_SERVER_URL", "http://127.0.0.1:8765/mcp")
    MCP_SERVER_PORT: int = int(_env("MCP_SERVER_PORT", "8765"))

    # ─────────────────────────────────────
    # 本地 LLM（llama.cpp，可选）
    # ─────────────────────────────────────
    LOCAL_LLM_ENABLED: bool = _env("LOCAL_LLM_ENABLED").lower() == "true"
    LOCAL_LLM_URL: str = _env("LOCAL_LLM_URL", "http://127.0.0.1:8080/v1")
    LOCAL_LLM_MODEL: str = _env("LOCAL_LLM_MODEL", "Qwen3.8-27B-Q3_K_M.gguf")
    LOCAL_LLM_API_KEY: str = _env("LOCAL_LLM_API_KEY", "local-any-value")

    @property
    def MINIUM_ENABLED(self) -> bool:
        """minium 是否启用：所有配置都配好才启用——
        MINIUM_ENABLED=true 显式启用；项目路径与 DevTools 路径任一缺失即不启用。"""
        return _env("MINIUM_ENABLED").lower() == "true" and bool(self.MINIUM_PROJECT_PATH) and bool(self.MINIUM_DEV_TOOL_PATH)

    # ─────────────────────────────────────
    # 认证（登录 + 会话隔离）
    # ─────────────────────────────────────
    JWT_SECRET: str = _env("JWT_SECRET", "dev-secret-change-me")
    JWT_EXPIRE_DAYS: int = int(_env("JWT_EXPIRE_DAYS", "7"))

    # ─────────────────────────────────────
    # 服务
    # ─────────────────────────────────────
    HOST: str = _env("HOST", "127.0.0.1")
    PORT: int = int(_env("PORT", "8000"))
    DEBUG: bool = _env("DEBUG", "true").lower() == "true"
    SESSIONS_DIR: str = _env(
        "SESSIONS_DIR",
        str(Path(__file__).resolve().parent.parent.parent.parent / "sessions"),
    )
    DATABASE_URL: str = _env(
        "DATABASE_URL",
        "postgresql://postgres:postgres@127.0.0.1:5432/sop_agent",
    )

    def get_llm_config(self, model_key: str) -> dict:
        """根据 model_key 获取 {model, base_url, api_key}。

        model_key 格式: <provider>-<variant>，如 deepseek-v4-pro。
        LOCAL_LLM_ENABLED=true 时所有任务统一走本地 llama.cpp（README「本地 LLM」节）。
        """
        if self.LOCAL_LLM_ENABLED:
            return {
                "model": self.LOCAL_LLM_MODEL,
                "base_url": self.LOCAL_LLM_URL,
                "api_key": self.LOCAL_LLM_API_KEY,
            }
        if model_key.lower().startswith("deepseek"):
            return {
                "model": (
                    self.DEEPSEEK_V4_PRO_MODEL_NAME
                    if "pro" in model_key.lower()
                    else self.DEEPSEEK_V4_FLASH_MODEL_NAME
                ),
                "base_url": self.DEEPSEEK_LLM_URL,
                "api_key": self.DEEPSEEK_API_KEY,
            }
        elif model_key.lower().startswith("qwen"):
            return {
                "model": self.QWEN3_7_FLASH_MODEL_NAME,
                "base_url": self.QWEN_LLM_URL,
                "api_key": self.QWEN_API_KEY,
            }
        # 未知 provider — 返回空配置
        return {"model": model_key, "base_url": "", "api_key": ""}

    def get_llm_api_key(self, model_key: str) -> str:
        return self.get_llm_config(model_key).get("api_key", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
