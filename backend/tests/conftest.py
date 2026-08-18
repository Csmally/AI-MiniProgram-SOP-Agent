"""pytest fixtures — mock minium 注入。"""

import pytest


@pytest.fixture
def fake_session(tmp_path):
    """注入假 minium 会话：is_available=True + execute 直接返回 fake。"""
    from sop_agent.tools import minium_session
    from .fakes import FakeMiniumSession

    fake = FakeMiniumSession(shots_dir=tmp_path / "shots")
    minium_session._session = fake
    minium_session._session_run_id = "fake-run"

    original_available = minium_session.is_available
    original_execute = minium_session.execute

    minium_session.is_available = lambda: True
    minium_session.execute = lambda action, **kwargs: action(fake)

    yield fake

    minium_session.is_available = original_available
    minium_session.execute = original_execute
    minium_session._session = None
    minium_session._session_run_id = ""


@pytest.fixture
def minium_unavailable():
    """强制 minium_session.is_available=False（桩模式路径）。"""
    from sop_agent.tools import minium_session

    original = minium_session.is_available
    minium_session.is_available = lambda: False
    yield
    minium_session.is_available = original
