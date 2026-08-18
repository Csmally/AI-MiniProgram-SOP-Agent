"""检查执行 Agent — 每个检查项由 LLM Agent 自主驱动小程序自动化工具执行。

双模式（切换点在 execute_one_item 内）：
- minium 环境可用（minium_session.is_available()）→ LLM agent 循环
  驱动真实微信开发者工具自动化，结构化判定 passed/failed；
- 环境缺失 → 桩实现（sleep + 固定 passed，结果标注 [桩]）。

本模块是单节点子图（build_executor_subgraph）：串行循环控制
（条件边自循环 + exec_cursor 游标）仍在主图 orchestrator（微信开发者工具
单实例约束，无法并行自动化）。
"""

import json
import time
from typing import Any

from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.graph.state import CompiledStateGraph
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from ..core.llm import get_llm, get_system_prompt
from ..sop.models import CheckResult
from ..tools import automator_session, automator_tools

from rich import print as rPrint

MAX_TOOL_ITERATIONS = 15       # agent 循环最大轮次
ITEM_DEADLINE_SECONDS = 600    # 单项墙钟上限
VERDICT_HISTORY_LEN = 12       # 判定时保留的最近消息条数


class ExecutorAgentState(MessagesState):
    """检查执行 Agent 的状态（主图状态子集）。

    exec_results / agent_progress 在父图是 operator.add reducer 通道，子图里
    声明为普通 LastValue（避免回写时被父图 reducer 二次累积），节点只返回
    本轮单项贡献。
    """

    session_id: str
    check_items: list[dict]
    exec_cursor: int
    run_id: str
    exec_results: list      # 子图内 LastValue，回写父图由 reducer 累积
    agent_progress: list    # 同上


def execute_one_item(state: ExecutorAgentState) -> dict:
    """执行当前游标指向的检查项（子图节点；串行循环控制在主图）。

    返回 exec_cursor 推进 + 单条 exec_results + agent_progress 事件。
    """
    cursor = state.get("exec_cursor", 0)
    check_items = state.get("check_items", [])
    if not check_items:
        # 空清单守卫：直接滑入 collect（report 空数据安全）
        return {"exec_cursor": 0}

    item = check_items[min(cursor, len(check_items) - 1)]
    run_id = state.get("run_id", "")

    rPrint(f"[bold blue]==========execute_one_item_abcd-{run_id}==========[/bold blue]")
    rPrint(item)
    rPrint(f"[bold blue]==========execute_one_item_abcd-{run_id}==========[/bold blue]")

    automator_tools.set_run_context(
        session_id=state.get("session_id", ""),
        run_id=run_id,
        item_id=item.get("id", ""),
    )
    try:
        if automator_session.is_available():
            result = _run_with_automator(item, run_id)   # 真实执行
        else:
            result = _run_stub(item, run_id)          # 桩降级
    finally:
        automator_tools.clear_run_context()

    return {
        "exec_cursor": cursor + 1,
        "exec_results": [result],
        "agent_progress": [{
            "agent": "executor",
            "item_id": item.get("id"),
            "status": result["status"],
            "run_id": run_id,
        }],
    }


def _run_stub(item: dict, run_id: str) -> dict:
    """桩实现（环境缺失降级路径）：保留原并行版行为。"""
    time.sleep(0.5)
    return {
        "check_item_id": item.get("id"),
        "description": item.get("description"),
        "category": item.get("category"),
        "status": "passed",
        "result_detail": "[桩] 检查通过 — minium 环境未配置（MINIUM_PROJECT_PATH / MINIUM_DEV_TOOL_PATH）",
        "screenshots": [],
        "run_id": run_id,
    }


def _run_with_automator(item: dict, run_id: str) -> dict:
    """真实执行：LLM agent 循环驱动 automator 工具 + 结构化判定。

    异常隔离：连接断/LLM 异常 → 该项 failed + 错误详情，不中断整个 run。
    """
    screenshots: list[str] = []
    try:
        llm_tools = get_llm("execute_checks").bind_tools(automator_tools.EXECUTOR_TOOLS)
        llm_plain = get_llm("execute_checks")

        messages: list[Any] = [
            SystemMessage(content=get_system_prompt("execute_checks")),
            HumanMessage(content=json.dumps({
                "check_item_id": item.get("id"),
                "category": item.get("category"),
                "description": item.get("description"),
                "priority": item.get("priority"),
                "check_steps": item.get("check_steps", []),
                "expected_result": item.get("expected_result", ""),
            }, ensure_ascii=False)),
        ]

        # 工具循环：LLM 决定调用，观察结果，直至停止或达上限
        deadline = time.monotonic() + ITEM_DEADLINE_SECONDS
        tool_calls_total = 0
        for _ in range(MAX_TOOL_ITERATIONS):
            if time.monotonic() > deadline:
                messages.append(HumanMessage(content="已达到单项时间上限，请基于已有证据给出判定。"))
                break

            rPrint(f"[bold red]==========调用大模型-开始==========[/bold red]")

            resp = llm_tools.invoke(messages)
            resp.pretty_print()

            rPrint(f"[bold red]==========调用大模型-结束==========[/bold red]")

            messages.append(resp)
            if not resp.tool_calls:
                if tool_calls_total == 0:
                    # LLM 只输出了计划没有动手：催促实际执行
                    messages.append(HumanMessage(content="请直接调用工具执行检查步骤，不要只描述计划。"))
                    continue
                break  # 已有执行证据后 LLM 主动停止 → 进入判定
            tool_calls_total += len(resp.tool_calls)
            for tc in resp.tool_calls:
                tool_name = tc.get("name", "")

                rPrint(f"[bold green]==========调用工具-{tool_name}==========[/bold green]")

                try:
                    # 新版 langchain-core 支持直接传 ToolCall dict：内部剥 args
                    # 并透传 tool_call_id，直接返回 ToolMessage
                    out_msg = automator_tools.TOOL_MAP[tool_name].invoke(tc)
                except Exception as e:
                    out_msg = ToolMessage(
                        content=f"[工具执行失败: {tool_name}] {e}",
                        tool_call_id=tc["id"],
                    )
                # 工具可能返回 list（get_pages）/ bool（element_exists），
                # DeepSeek 只接受文本 content（400 deserialize 事故）
                out_msg.content = str(out_msg.content)
                if tool_name == "screenshot" and out_msg.content and not out_msg.content.startswith("["):
                    screenshots.append(out_msg.content)
                messages.append(out_msg)

        verdict = _verdict(llm_plain, messages)
        return {
            "check_item_id": item.get("id"),
            "description": item.get("description"),
            "category": item.get("category"),
            "status": verdict["status"],
            "result_detail": verdict["result_detail"],
            "screenshots": screenshots,
            "run_id": run_id,
        }
    except Exception as e:
        return {
            "check_item_id": item.get("id"),
            "description": item.get("description"),
            "category": item.get("category"),
            "status": "failed",
            "result_detail": f"执行异常: {e}",
            "screenshots": screenshots,
            "run_id": run_id,
        }


def _verdict(llm, messages: list) -> dict:
    """基于执行历史的结构化判定（function_calling，与 parse/sop 同模式）。"""
    hist = _clean_history(messages)
    hist.append(HumanMessage(content="请基于以上执行记录，给出最终检查判定（passed 或 failed 及详细理由）。"))
    try:
        structured = llm.with_structured_output(CheckResult, method="function_calling")
        return structured.invoke(hist).model_dump()
    except Exception:
        return _verdict_fallback(llm, hist)


def _clean_history(messages: list) -> list:
    """判定前清理执行历史（保证 DeepSeek 消息序列完整）。

    - 只对**未兑现**的 tool_calls（轮次上限截断）补占位 ToolMessage，
      已兑现的不可重复补（重复 tool_call_id 会被 API 400）；
    - 取最近 VERDICT_HISTORY_LEN 条，且丢弃窗口开头的孤儿 ToolMessage
      （切片可能切断 AIMessage(tool_calls) 与其 ToolMessage 的配对）。
    """
    answered_ids = {
        m.tool_call_id for m in messages
        if isinstance(m, ToolMessage) and getattr(m, "tool_call_id", None)
    }
    full: list = []
    for m in messages:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            full.append(m)
            for tc in m.tool_calls:
                if tc.get("id") not in answered_ids:
                    full.append(ToolMessage(
                        content="[工具未执行] 已达轮次上限",
                        tool_call_id=tc["id"],
                    ))
            continue
        full.append(m)

    recent = full[-VERDICT_HISTORY_LEN:]
    while recent and isinstance(recent[0], ToolMessage):
        recent.pop(0)
    return recent


def _verdict_fallback(llm, hist: list) -> dict:
    """判定降级：prompt 约束 + 健壮解析。"""
    try:
        resp = llm.invoke(hist)
    except Exception as e:
        return {"status": "failed", "result_detail": f"判定调用失败: {e}"}
    content = resp.content.strip()
    try:
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        data = json.loads(content)
        status = data.get("status") if data.get("status") in ("passed", "failed") else "failed"
        return {"status": status, "result_detail": str(data.get("result_detail", "判定失败"))}
    except Exception:
        return {"status": "failed", "result_detail": f"判定失败: {content[:500]}"}


def build_executor_subgraph() -> CompiledStateGraph:
    """编译检查执行 Agent 子图（不传 checkpointer，继承父图）。"""
    workflow = StateGraph(ExecutorAgentState)
    workflow.add_node("execute_one_item", execute_one_item)
    workflow.add_edge(START, "execute_one_item")
    workflow.add_edge("execute_one_item", END)
    return workflow.compile()
