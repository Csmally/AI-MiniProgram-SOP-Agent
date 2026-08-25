"""调用链追溯单测 — 内容截断 / 父级栈推断 / token 提取（纯逻辑，不依赖真库）。"""

import uuid
from types import SimpleNamespace

from sop_agent.tracing import store
from sop_agent.tracing.handler import (
    TraceCallbackHandler,
    _extract_llm_output,
    _extract_tokens,
)


class TestSafeJson:
    def test_long_text_truncated_with_marker(self):
        out = store._safe_json("x" * 100, max_chars=20)
        assert isinstance(out, str)
        assert out.startswith("[已截断]") and "…" in out

    def test_short_dict_passthrough(self):
        assert store._safe_json({"a": 1, "b": "短文本"}) == {"a": 1, "b": "短文本"}

    def test_unserializable_falls_back_to_str(self):
        obj = object()
        out = store._safe_json(obj)
        assert isinstance(out, str) and "object" in out


class TestParentInference:
    def test_fallback_to_thread_stack_when_no_native_parent(self, monkeypatch):
        """hook 继承的 run 无原生 parent_run_id → 取 chain 栈顶。"""
        monkeypatch.setattr(store, "insert_run", lambda **kw: None)
        handler = TraceCallbackHandler("s1", 1)

        chain_id = uuid.uuid4()
        handler.on_chain_start({"name": "node"}, {"x": 1}, run_id=chain_id)
        # llm run 无 parent：应推断为 chain_id
        captured = {}

        def fake_insert(**kw):
            captured.update(kw)

        monkeypatch.setattr(store, "insert_run", fake_insert)
        llm_id = uuid.uuid4()
        handler.on_llm_start({"name": "llm"}, ["hi"], run_id=llm_id)
        assert captured["parent_id"] == chain_id
        assert captured["kind"] == "llm" and captured["session_id"] == "s1"

        # 栈清空后父级为 None
        handler.on_chain_end({}, run_id=chain_id)
        captured.clear()
        handler.on_llm_start({"name": "llm"}, ["hi"], run_id=uuid.uuid4())
        assert captured["parent_id"] is None


class TestTokenExtraction:
    def test_from_generation_usage_metadata(self):
        msg = SimpleNamespace(usage_metadata={"input_tokens": 120, "output_tokens": 45})
        gen = SimpleNamespace(message=msg)
        response = SimpleNamespace(generations=[[gen]], llm_output=None)
        assert _extract_tokens(response) == (120, 45)

    def test_fallback_to_llm_output(self):
        response = SimpleNamespace(
            generations=[], llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        )
        assert _extract_tokens(response) == (10, 5)

    def test_no_usage_returns_none(self):
        assert _extract_tokens(SimpleNamespace(generations=[], llm_output=None)) == (None, None)


class TestLlmOutputExtraction:
    def test_content_and_tool_calls(self):
        msg = SimpleNamespace(content="回复内容", tool_calls=[
            {"name": "tap", "args": {"inner_text": "提交"}, "id": "t1"},
        ])
        response = SimpleNamespace(generations=[[SimpleNamespace(message=msg)]])
        out = _extract_llm_output(response)
        assert out["content"] == "回复内容"
        assert out["tool_calls"][0]["name"] == "tap"
