"""模型工厂 — 按任务路由到 DeepSeek/Qwen。"""

from langchain_openai import ChatOpenAI

from .config import get_settings


def get_llm(task: str) -> ChatOpenAI:
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
