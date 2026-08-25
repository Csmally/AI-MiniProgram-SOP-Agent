"""调用链追溯（LangSmith 式）— 采集层。

机制：langchain-core 的 register_configure_hook（LangSmith SDK 同款）——
trace_scope 设置 ContextVar 后，本线程内所有 `llm.invoke`/`tool.invoke` 等
runnable 调用（包括 agent 节点里不传 config 的裸调用）自动继承 handler，
agent 代码零改动。事件写入 Postgres trace_runs 表，由独立展示平台读取。
"""

from .context import trace_scope
from .store import delete_session_traces, init_db as init_trace_db

__all__ = ["trace_scope", "delete_session_traces", "init_trace_db"]
