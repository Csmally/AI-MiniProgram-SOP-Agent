"""TraceCallbackHandler — 把 LangChain 回调事件写入 trace_runs。

事件覆盖三类 run：
- chain：langgraph 节点/图（name 取 metadata.langgraph_node）
- llm：每次大模型调用（prompts 入 input；usage_metadata 提 token；回复+tool_calls 入 output）
- tool：executor 的 MCP 工具调用（入参/结果）

父子层级：configure hook 继承的 run 没有原生 parent_run_id，
用 thread-local 的 chain run 栈推断（同步串行执行，栈顶即当前父节点）。
写库全部 try/except 吞异常——tracing 永不伤害业务。
"""

import threading
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from . import store


class TraceCallbackHandler(BaseCallbackHandler):
    """单次图执行绑定一个实例（session_id/user_id 固定）。"""

    # thread-local：当前线程的 chain run 栈（父级推断用）
    _stacks = threading.local()

    def __init__(self, session_id: str, user_id: int | None = None):
        self.session_id = session_id
        self.user_id = user_id

    # ── 父级推断 ──

    @classmethod
    def _stack(cls) -> list:
        stack = getattr(cls._stacks, "stack", None)
        if stack is None:
            stack = []
            cls._stacks.stack = stack
        return stack

    def _parent(self, parent_run_id) -> UUID | None:
        if parent_run_id is not None:
            return parent_run_id
        stack = self._stack()
        return stack[-1] if stack else None

    def _chain_start(self, run_id: UUID) -> None:
        self._stack().append(run_id)

    def _chain_end(self, run_id: UUID) -> None:
        stack = self._stack()
        if run_id in stack:
            stack.remove(run_id)

    # ── chain（langgraph 节点）──

    def on_chain_start(self, serialized: dict, inputs: Any, *, run_id: UUID,
                       parent_run_id: UUID | None = None, metadata: dict | None = None,
                       **kwargs: Any) -> None:
        node = (metadata or {}).get("langgraph_node")
        name = node or serialized.get("name") or "graph"
        self._chain_start(run_id)
        try:
            store.insert_run(
                run_id=run_id, parent_id=self._parent(parent_run_id),
                session_id=self.session_id, user_id=self.user_id,
                kind="chain", name=name, node=node, input_obj=inputs,
            )
        except Exception:
            pass

    def on_chain_end(self, outputs: Any, *, run_id: UUID,
                     parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        try:
            store.update_run(run_id, output_obj=outputs)
        except Exception:
            pass
        finally:
            self._chain_end(run_id)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        try:
            store.update_run(run_id, error=str(error))
        except Exception:
            pass
        finally:
            self._chain_end(run_id)

    # ── llm ──

    def on_llm_start(self, serialized: dict, prompts: list[str], *, run_id: UUID,
                     parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        model = (kwargs.get("invocation_params") or {}).get("model") or serialized.get("name") or "llm"
        try:
            store.insert_run(
                run_id=run_id, parent_id=self._parent(parent_run_id),
                session_id=self.session_id, user_id=self.user_id,
                kind="llm", name=model, node=None, input_obj={"prompts": prompts},
            )
        except Exception:
            pass

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        try:
            tokens_in, tokens_out = _extract_tokens(response)
            store.update_run(
                run_id,
                output_obj=_extract_llm_output(response),
                tokens_in=tokens_in, tokens_out=tokens_out,
            )
        except Exception:
            pass

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        try:
            store.update_run(run_id, error=str(error))
        except Exception:
            pass

    # ── tool ──

    def on_tool_start(self, serialized: dict, input_str: str, *, run_id: UUID,
                      parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        try:
            store.insert_run(
                run_id=run_id, parent_id=self._parent(parent_run_id),
                session_id=self.session_id, user_id=self.user_id,
                kind="tool", name=serialized.get("name", "tool"), node=None,
                input_obj={"input": input_str},
            )
        except Exception:
            pass

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        try:
            store.update_run(run_id, output_obj={"output": output})
        except Exception:
            pass

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        try:
            store.update_run(run_id, error=str(error))
        except Exception:
            pass


# ──────────────────────────────────────────────
# 事件内容提取
# ──────────────────────────────────────────────

def _extract_tokens(response: Any) -> tuple[int | None, int | None]:
    """从 LLMResult 提 token 消耗（优先 generation 级 usage_metadata，兜底 llm_output）。"""
    try:
        for gen in getattr(response, "generations", []) or []:
            for g in gen:
                usage = getattr(getattr(g, "message", None), "usage_metadata", None)
                if usage:
                    return (
                        usage.get("input_tokens") if isinstance(usage, dict) else getattr(usage, "input_tokens", None),
                        usage.get("output_tokens") if isinstance(usage, dict) else getattr(usage, "output_tokens", None),
                    )
    except Exception:
        pass
    try:
        usage = (getattr(response, "llm_output", None) or {}).get("token_usage", {})
        return usage.get("prompt_tokens"), usage.get("completion_tokens")
    except Exception:
        return None, None


def _extract_llm_output(response: Any) -> Any:
    """每轮回复内容 + tool_calls 结构化输出。"""
    try:
        gen = getattr(response, "generations", [])[0][0]
        msg = gen.message
        out: dict = {"content": getattr(msg, "content", None)}
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            out["tool_calls"] = [
                {"name": tc.get("name"), "args": tc.get("args"), "id": tc.get("id")}
                for tc in tool_calls
            ]
        return out
    except Exception:
        return {"content": str(response)}
