"""全局追踪上下文 — ContextVar + configure hook（LangSmith SDK 同款机制）。

register_configure_hook 让本线程内**所有**新创建的 RunnableConfig（包括
agent 节点里不传 config 的裸 llm.invoke / tool.invoke）自动继承
trace_scope 里设置的 handler——agent 代码零改动即可全量采集。

用法：orchestrator 在每次图执行外层包 trace_scope(session_id, user_id)。
"""

from contextlib import contextmanager
from contextvars import ContextVar

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook

from .handler import TraceCallbackHandler

# 注意：configure hook 的 ContextVar 值必须是**单个** BaseCallbackHandler
# （langchain-core 的类型契约，传 list 会在回调合并时炸 raise_error）
_trace_var: ContextVar[BaseCallbackHandler | None] = ContextVar(
    "sop_trace_handler", default=None
)

# import 时注册一次（全局生效；handler 由每次 trace_scope 动态提供）
register_configure_hook(_trace_var, inheritable=True)


@contextmanager
def trace_scope(session_id: str, user_id: int | None = None):
    """单次图执行的追踪作用域：设置/恢复 ContextVar。

    SSE 场景：graph.stream 在 worker 线程迭代，本作用域需包住整个
    生成器执行（orchestrator.stream_action 内部），ContextVar 随线程生效。
    """
    token = _trace_var.set(TraceCallbackHandler(session_id, user_id))
    try:
        yield
    finally:
        _trace_var.reset(token)
