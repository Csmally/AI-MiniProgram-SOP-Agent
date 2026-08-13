"""模型工厂 — 按任务路由到 DeepSeek/Qwen，并集中声明各任务的系统提示词。

ChatOpenAI 构造参数没有 system_prompt（提示词属于任务配置而非模型实例），
因此各 Agent 的系统提示词在此集中声明，调用时以 SystemMessage 传入。
"""

from langchain_openai import ChatOpenAI

from .config import get_settings

# 各任务的系统提示词（Agent 人设集中管理；新任务在此追加）
TASK_SYSTEM_PROMPTS: dict[str, str] = {
    "chat": "你是一个微信小程序 SOP 检查助手，负责解析 PRD 需求文档、生成 SOP 检查清单并解答相关问题。回答保持简洁。",
    "parse_prd": (
        "你是资深产品需求分析师。请分析 PRD 需求文档，提取所有新增功能的信息。\n"
        "对每个功能提取：name(功能名称)、description(功能描述)、affected_pages(涉及页面路径列表)、"
        "api_endpoints(相关 API 接口列表)、ui_elements(关键 UI 元素)、acceptance_criteria(验收标准列表)。\n"
        "只输出 JSON 数组，每个元素是一个功能对象，不要输出任何多余内容。"
    ),
    "generate_sop": (
        "你是资深 QA 测试工程师。请根据功能列表生成 SOP 检查清单，对每个功能从 UI 和 API 两个角度分别生成检查项。\n"
        "每个检查项包含：id(check-001 格式)、category(\"ui\" 或 \"api\")、description(检查项描述)、"
        "priority(critical/high/medium/low)、check_steps(检查步骤列表)、expected_result(预期结果)、status(固定 \"pending\")。\n"
        "只输出 JSON 数组，不要输出任何多余内容。"
    ),
    "generate_report": (
        "你是测试报告撰写专家。请根据 SOP 检查数据生成一份简明专业的 Markdown 报告，"
        "包含：1.检查概要 2.各项检查结果 3.问题汇总（如有失败项） 4.建议。"
    ),
}


def get_system_prompt(task: str) -> str:
    """获取任务对应的系统提示词（未声明的任务返回空串）。"""
    return TASK_SYSTEM_PROMPTS.get(task, "")


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
