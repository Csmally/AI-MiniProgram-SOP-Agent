"""minium 会话管理 — 微信开发者工具自动化的唯一入口抽象层。

设计要点：
- 单 DevTools 实例约束：全局锁串行化所有自动化操作（跨会话/跨线程）；
- 懒加载单例，进程生命周期内复用（连接成本高）；
- 连接类异常时自动废弃会话，下次调用重建；
- is_available() 双模式检测：环境缺失时 executor 自动降级桩。
"""

import threading
from pathlib import Path
from typing import Any, Callable

import minium

from sop_agent.core.config import get_settings


class MiniumConnectError(RuntimeError):
    """minium 连接开发者工具失败。"""


_lock = threading.RLock()   # 全局：单 DevTools 实例的跨会话串行化
_session: Any = None        # minium.Minium 单例
_session_run_id: str = ""   # 会话归属的 run_id


def is_available() -> bool:
    """检测 minium 环境是否可用（全部满足才返回 True）。

    1) settings.MINIUM_ENABLED（MINIUM_ENABLED=true 显式启用；路径未配齐也不启用）；
    2) 项目路径目录存在、DevTools 路径文件存在。
    """
    settings = get_settings()
    if not settings.MINIUM_ENABLED:
        return False
    if not Path(settings.MINIUM_PROJECT_PATH).exists():
        return False
    if not Path(settings.MINIUM_DEV_TOOL_PATH).exists():
        return False
    return True


def get_session(run_id: str = "") -> Any:
    """获取 minium 会话单例（懒加载；run_id 变化自动重建，为空则复用现有）。"""
    global _session, _session_run_id
    with _lock:
        if _session is not None and (not run_id or run_id == _session_run_id):
            return _session
        _dispose_locked()
        settings = get_settings()
        try:
            _session = minium.Minium({
                "project_path": settings.MINIUM_PROJECT_PATH,
                "dev_tool_path": settings.MINIUM_DEV_TOOL_PATH,
                "test_port": settings.MINIUM_TEST_PORT,
            })
        except Exception as e:
            _session = None
            raise MiniumConnectError(f"minium 连接开发者工具失败: {e}") from e
        _session_run_id = run_id
        return _session


def execute(action: Callable[[Any], Any], run_id: str = "") -> Any:
    """工具统一入口：全局锁内获取会话并执行 action。

    锁保证同一时刻只有一个自动化操作在动 DevTools（单实例约束）；
    run_id 透传给 get_session，复用单例会话（连接成本高，勿每次重建）。
    连接类异常自动废弃会话，下次调用重建。
    """
    with _lock:
        try:
            return action(get_session(run_id))
        except MiniumConnectError:
            raise
        except Exception as e:
            if _is_connection_error(e):
                _dispose_locked()
            raise


def _is_connection_error(e: Exception) -> bool:
    """启发式判断是否为连接类异常（元素未找到等业务失败不触发会话重建）。"""
    msg = str(e).lower()
    return any(k in msg for k in (
        "websocket", "connection", "connect", "closed", "refused",
        "timeout", "remote", "1006", "10061", "socket",
    ))


def dispose() -> None:
    """释放会话单例（orchestrator.close 调用）。"""
    with _lock:
        _dispose_locked()


def _dispose_locked() -> None:
    global _session, _session_run_id
    # minium 无显式 close API，置空引用即释放（DevTools 自动化连接随之回收）
    _session = None
    _session_run_id = ""
