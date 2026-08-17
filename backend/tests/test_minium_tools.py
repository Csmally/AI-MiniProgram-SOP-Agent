"""minium 工具单测 — 注入假会话，验证参数透传与结果。"""

import pytest

from sop_agent.tools import minium_tools


def test_navigate_to(fake_session):
    out = minium_tools.navigate_to.invoke({"page_path": "/pages/index/index"})
    assert "跳转成功" in out
    assert ("navigate_to", ("/pages/index/index",)) in fake_session.app.calls


def test_navigate_to_normalizes_path(fake_session):
    # LLM 传参不可信：不带斜杠 / 多斜杠 → 工具层归一化为恰好一个前导斜杠（PITFALLS 6.3）
    minium_tools.navigate_to.invoke({"page_path": "pages/index/index"})
    minium_tools.navigate_to.invoke({"page_path": "//pages/index/index"})
    for _, (url,) in fake_session.app.calls:
        assert url == "/pages/index/index"


def test_switch_tab(fake_session):
    minium_tools.switch_tab.invoke({"tab_path": "pages/home/home"})
    assert ("switch_tab", ("/pages/home/home",)) in fake_session.app.calls


def test_tap_passes_args(fake_session):
    out = minium_tools.tap.invoke({"selector": "button.submit", "inner_text": "提交", "max_timeout": 3})
    assert "button.submit" in out
    assert fake_session.page.elements["button.submit"].clicked
    assert fake_session.page.calls[-1][1] == ("button.submit", "提交", 3)


def test_input_text(fake_session):
    minium_tools.input_text.invoke({"selector": "input.nickname", "text": "测试昵称"})
    assert fake_session.page.elements["input.nickname"].inputs == ["测试昵称"]


def test_get_text_returns_full_text(fake_session):
    fake_session.page.elements["view.title"] = __import__("tests.fakes", fromlist=["FakeElement"]).FakeElement("x" * 2000)
    out = minium_tools.get_text.invoke({"selector": "view.title"})
    assert out == "x" * 2000


def test_element_exists(fake_session):
    fake_session.page.exists["image.avatar"] = False
    assert minium_tools.element_exists.invoke({"selector": "image.avatar"}) is False
    assert minium_tools.element_exists.invoke({"selector": "image.logo"}) is True


def test_navigate_to_raises_on_failure(fake_session):
    # 原生方法失败即抛异常（executor 循环捕获后转成可读文本回喂 agent，PITFALLS 6.5）
    fake_session.app.fail_paths.add("/pages/x/index")
    with pytest.raises(RuntimeError, match="模拟导航失败"):
        minium_tools.navigate_to.invoke({"page_path": "pages/x/index"})


def test_get_pages(fake_session):
    pages = minium_tools.get_pages.invoke({})
    assert pages == ["pages/loginPage/index", "pages/chatPage/index"]


def test_screenshot_saves_and_returns_filename(fake_session):
    minium_tools.set_run_context(session_id="s1", run_id="r1", item_id="c1")
    try:
        name = minium_tools.screenshot.invoke({"name": "step1"})
    finally:
        minium_tools.clear_run_context()
    assert name == "step1.png"
    assert len(fake_session.app.screenshots) == 1
    assert "s1" in fake_session.app.screenshots[0]


def test_is_available_true_when_all_configured(tmp_path, monkeypatch):
    """环境检测：MINIUM_ENABLED=true 且双路径存在 → 启用。"""
    from sop_agent.core.config import get_settings
    from sop_agent.tools import minium_session

    cli = tmp_path / "cli.bat"
    cli.write_text("")
    s = get_settings()
    monkeypatch.setattr(s, "MINIUM_PROJECT_PATH", str(tmp_path), raising=False)
    monkeypatch.setattr(s, "MINIUM_DEV_TOOL_PATH", str(cli), raising=False)
    monkeypatch.setenv("MINIUM_ENABLED", "true")

    assert minium_session.is_available() is True


def test_is_available_false_when_explicitly_disabled(tmp_path, monkeypatch):
    """环境检测：MINIUM_ENABLED=false 不启用（即使路径已配置）。"""
    import sys
    from sop_agent.core.config import get_settings
    from sop_agent.tools import minium_session

    cli = tmp_path / "cli.bat"
    cli.write_text("")
    s = get_settings()
    monkeypatch.setattr(s, "MINIUM_PROJECT_PATH", str(tmp_path), raising=False)
    monkeypatch.setattr(s, "MINIUM_DEV_TOOL_PATH", str(cli), raising=False)
    monkeypatch.setenv("MINIUM_ENABLED", "false")

    assert minium_session.is_available() is False


def test_is_available_false_when_not_explicitly_enabled(tmp_path, monkeypatch):
    """环境检测：MINIUM_ENABLED 未设置（非 true）→ 不启用，即使路径已配置。"""
    from sop_agent.core.config import get_settings
    from sop_agent.tools import minium_session

    cli = tmp_path / "cli.bat"
    cli.write_text("")
    s = get_settings()
    monkeypatch.setattr(s, "MINIUM_PROJECT_PATH", str(tmp_path), raising=False)
    monkeypatch.setattr(s, "MINIUM_DEV_TOOL_PATH", str(cli), raising=False)
    monkeypatch.delenv("MINIUM_ENABLED", raising=False)

    assert minium_session.is_available() is False


def test_is_available_false_when_project_path_missing(tmp_path, monkeypatch):
    """环境检测：MINIUM_ENABLED=true 但项目路径不存在 → 不启用。"""
    from sop_agent.core.config import get_settings
    from sop_agent.tools import minium_session

    cli = tmp_path / "cli.bat"
    cli.write_text("")
    s = get_settings()
    monkeypatch.setattr(s, "MINIUM_PROJECT_PATH", str(tmp_path / "no_such_dir"), raising=False)
    monkeypatch.setattr(s, "MINIUM_DEV_TOOL_PATH", str(cli), raising=False)
    monkeypatch.setenv("MINIUM_ENABLED", "true")

    assert minium_session.is_available() is False
