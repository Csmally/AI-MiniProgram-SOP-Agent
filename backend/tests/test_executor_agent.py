"""executor 节点测试 — 桩模式（无网络）+ 真实 LLM × fake minium 集成。"""

import pytest

from sop_agent.core.config import get_settings
from sop_agent.agents.executor_agent import execute_one_item

needs_key = pytest.mark.skipif(
    not get_settings().DEEPSEEK_API_KEY,
    reason="缺少 DEEPSEEK_API_KEY，跳过真实 LLM 集成用例",
)


def _state(check_items, cursor=0, run_id="test-run"):
    return {
        "session_id": "test-session",
        "check_items": check_items,
        "exec_cursor": cursor,
        "run_id": run_id,
    }


def _item(item_id="c1"):
    return {
        "id": item_id,
        "category": "ui",
        "description": "测试检查项",
        "priority": "high",
        "check_steps": ["验证页面元素存在"],
        "expected_result": "元素存在",
    }


def test_stub_mode(minium_unavailable):
    """桩模式：无网络，断言 [桩] passed + 游标推进 + 进度事件。"""
    out = execute_one_item(_state([_item()]))

    assert out["exec_cursor"] == 1
    result = out["exec_results"][0]
    assert result["status"] == "passed"
    assert "[桩]" in result["result_detail"]
    progress = out["agent_progress"][0]
    assert progress["item_id"] == "c1" and progress["status"] == "passed"


def test_empty_checklist_guard(minium_unavailable):
    """空清单守卫：不执行，游标保持 0（滑入 collect）。"""
    out = execute_one_item(_state([]))
    assert out["exec_cursor"] == 0
    assert "exec_results" not in out


def test_clean_history_no_orphan_tool_messages():
    """回归：切片不能产生孤儿 ToolMessage（DeepSeek 400 事故）。"""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from sop_agent.agents.executor_agent import _clean_history

    # 构造：一条带 tool_calls 的 AIMessage + 若干 ToolMessage，随后大量普通消息
    # 使得 12 条窗口恰好从 ToolMessage 中间切开
    msgs = [AIMessage(content="", tool_calls=[{"name": "tap", "args": {}, "id": "t1"}])]
    msgs.append(ToolMessage(content="ok", tool_call_id="t1"))
    msgs.append(ToolMessage(content="ok2", tool_call_id="t2"))
    msgs += [HumanMessage(content=f"msg-{i}") for i in range(15)]

    cleaned = _clean_history(msgs)
    assert not isinstance(cleaned[0], ToolMessage), "窗口开头不能是孤儿 ToolMessage"
    # 每个 ToolMessage 前必须有其 AIMessage(tool_calls)
    for i, m in enumerate(cleaned):
        if isinstance(m, ToolMessage):
            assert isinstance(cleaned[i - 1], AIMessage) and cleaned[i - 1].tool_calls


def test_clean_history_pads_unfulfilled_tool_calls():
    """回归：末条 AIMessage 带未兑现 tool_calls 时补占位 ToolMessage。"""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from sop_agent.agents.executor_agent import _clean_history

    msgs = [
        HumanMessage(content="任务"),
        AIMessage(content="", tool_calls=[{"name": "screenshot", "args": {"name": "x"}, "id": "t9"}]),
    ]
    cleaned = _clean_history(msgs)
    assert any(isinstance(m, ToolMessage) and "已达轮次上限" in m.content for m in cleaned)
    # 补的占位 ToolMessage 紧跟在 AIMessage 之后
    idx = cleaned.index(msgs[1])
    assert isinstance(cleaned[idx + 1], ToolMessage)


@needs_key
def test_real_mode_with_fake_minium(fake_session):
    """集成：fake minium + 真实 LLM 跑通一个检查项的完整 agent 循环。"""
    from sop_agent.agents import executor_agent

    original_max = executor_agent.MAX_TOOL_ITERATIONS
    executor_agent.MAX_TOOL_ITERATIONS = 8  # 限轮加速
    try:
        out = execute_one_item(_state([_item()]))
    finally:
        executor_agent.MAX_TOOL_ITERATIONS = original_max

    result = out["exec_results"][0]
    assert out["exec_cursor"] == 1
    assert result["status"] in ("passed", "failed")
    assert result["result_detail"]
    assert result["run_id"] == "test-run"
    # 工具链真实打通：fake minium（app 或 page）记录了调用
    assert len(fake_session.app.calls) + len(fake_session.page.calls) > 0


def test_build_item_payload_includes_cross_item_context():
    """上下文注入：负载包含此前检查项结果 + 小程序当前状态。"""
    from sop_agent.agents.executor_agent import _build_item_payload

    payload = _build_item_payload(
        _item(),
        [{"id": "c0", "description": "此前项", "status": "passed", "result_detail": "已通过"}],
        {"current_page": "pages/a/index", "all_pages": [], "current_page_elements": []},
    )
    assert payload["check_item_id"] == "c1"
    assert payload["previous_check_results"][0]["id"] == "c0"
    assert payload["previous_check_results"][0]["status"] == "passed"
    assert payload["current_app_state"]["current_page"] == "pages/a/index"


def test_build_item_payload_empty_context_for_first_item():
    """首个检查项：无前项、无状态快照时字段为空（LLM 自行导航），不抛异常。"""
    from sop_agent.agents.executor_agent import _build_item_payload

    payload = _build_item_payload(_item(), [], {})
    assert payload["previous_check_results"] == []
    assert payload["current_app_state"] == {}


def test_execute_one_item_passes_prior_results_by_run_id(fake_session, monkeypatch):
    """接线回归：按 run_id 过滤 exec_results 传给 _run_with_minium，其他 run 隔离。"""
    from sop_agent.agents import executor_agent

    captured = {}

    def fake_run(item, run_id, prior_results):
        captured.update(item=item, run_id=run_id, prior_results=prior_results)
        return {
            "check_item_id": item["id"],
            "description": item.get("description"),
            "category": item.get("category"),
            "status": "passed",
            "result_detail": "ok",
            "screenshots": [],
            "run_id": run_id,
        }

    monkeypatch.setattr(executor_agent, "_run_with_minium", fake_run)
    state = _state([_item("c2")])
    state["exec_results"] = [
        {"check_item_id": "c1", "description": "d1", "status": "passed",
         "result_detail": "ok", "run_id": "test-run"},
        {"check_item_id": "old", "description": "d0", "status": "failed",
         "result_detail": "ok", "run_id": "other-run"},  # 其他 run 应被过滤
    ]

    out = executor_agent.execute_one_item(state)

    assert captured["run_id"] == "test-run"
    assert [r["id"] for r in captured["prior_results"]] == ["c1"]
    assert out["exec_cursor"] == 1
    assert out["exec_results"][0]["status"] == "passed"
