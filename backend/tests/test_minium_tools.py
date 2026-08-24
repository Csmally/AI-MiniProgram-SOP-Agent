"""minium 工具集单测 — 元素清单解析 / 选择器推导 / 文本定位链 / 失败证据。"""

import base64
import json
from types import SimpleNamespace

import pytest

from mcp_server.tools import minium_tools
from tests.fakes import FakeElement


SAMPLE_WXML = """
<page>
  <view class="header">
    <text class="title">欢迎页</text>
  </view>
  <view class="btn-box" wx:if="{{ready}}">
    <button id="submit-btn" class="btn btn-primary">提交订单</button>
    <button class="btn">取消</button>
  </view>
</page>
"""


class TestParseWxml:
    def test_extracts_tag_class_id_text(self):
        items = minium_tools._parse_wxml(SAMPLE_WXML)
        btn = next(e for e in items if e["id"] == "submit-btn")
        assert btn["tag"] == "button"
        assert btn["class"] == "btn btn-primary"
        assert btn["text"] == "提交订单"

    def test_wx_prefixed_attributes_do_not_break_parsing(self):
        # wx:if 未经声明命名空间，lxml 默认直接报错——解析器必须能容忍
        items = minium_tools._parse_wxml(SAMPLE_WXML)
        assert "取消" in {e["text"] for e in items}  # wx:if 容器内的元素能枚举出来

    def test_parent_text_is_descendant_concat(self):
        # view.header 的文本 = 后代 text 拼接（对齐 inner_text 语义）
        items = minium_tools._parse_wxml(SAMPLE_WXML)
        assert any(e["tag"] == "view" and e["text"] == "欢迎页" for e in items)

    def test_malformed_wxml_raises_readable_error(self):
        with pytest.raises(RuntimeError, match="WXML 解析失败"):
            minium_tools._parse_wxml("<view><text>未闭合</view>")


class TestMatchCandidates:
    def test_leaf_first_when_parent_shares_text(self):
        """父容器与子元素同文本（Taro 委托典型结构）→ 候选[0] 必须是叶子。"""
        wxml = '<page><view data-sid="_As" id="_As"><view data-sid="_Ar" id="_Ar">跳转页面</view></view></page>'
        candidates = minium_tools._match_candidates(wxml, "跳转页面")
        assert candidates[0]["id"] == "_Ar"   # 文本直接落在 _Ar 上
        assert candidates[1]["id"] == "_As"   # 祖先兜底候选排后（定向补点用）

    def test_text_on_container_is_leaf_itself(self):
        """文本直接落在容器上时，容器自身就是叶子（事件可达目标）。"""
        wxml = '<page><view id="_As">跳转页面<text>更多</text></view></page>'
        candidates = minium_tools._match_candidates(wxml, "跳转页面")
        assert candidates[0]["id"] == "_As"


class TestPickSelector:
    def test_id_first(self):
        assert minium_tools._pick_selector({"id": "a", "class": "b", "tag": "c"}) == "#a"

    def test_class_fallback_uses_first_class(self):
        assert minium_tools._pick_selector({"id": "", "class": "btn btn-primary", "tag": "button"}) == ".btn"

    def test_tag_last_resort(self):
        assert minium_tools._pick_selector({"id": "", "class": "", "tag": "view"}) == "view"


class TestToolsWithFakeSession:
    def test_get_page_elements_returns_json_list(self, fake_session):
        fake_session.page.page_wxml = SAMPLE_WXML
        items = json.loads(minium_tools.get_page_elements.invoke({"limit": 20}))
        texts = {e["text"] for e in items}
        assert "提交订单" in texts and "取消" in texts
        btn = next(e for e in items if e["id"] == "submit-btn")
        assert btn["class"] == "btn btn-primary"

    def test_tap_by_selector_legacy_path(self, fake_session):
        minium_tools.tap.invoke({"selector": ".btn"})
        assert ("get_element", (".btn", "", 5)) in fake_session.page.calls
        assert fake_session.page.elements[".btn"].clicked is True

    def test_tap_by_text_derives_real_selector(self, fake_session):
        fake_session.page.page_wxml = SAMPLE_WXML
        minium_tools.tap.invoke({"inner_text": "提交订单"})
        assert fake_session.page.elements["#submit-btn"].clicked is True

    def test_tap_by_text_prefers_leaf_not_container(self, fake_session):
        """复刻真机案例：父 _As 与子 _Ar 同文本 → 必须点 _Ar（叶子），
        点 _As 事件 target 是容器，Taro 根代理查无 onClick 静默失败。"""
        fake_session.page.page_wxml = (
            '<page><view data-sid="_As" id="_As"><view data-sid="_Ar" id="_Ar">跳转页面</view></view></page>'
        )
        minium_tools.tap.invoke({"inner_text": "跳转页面"})
        assert fake_session.page.elements["#_Ar"].clicked is True
        assert "#_As" not in fake_session.page.elements

    def test_tap_reports_navigation_when_page_changes(self, fake_session):
        fake_session.page.page_wxml = SAMPLE_WXML
        el = fake_session.page.elements.setdefault("#submit-btn", FakeElement())
        el.on_click = lambda: setattr(fake_session.page, "path", "pages/testPage/index")
        out = minium_tools.tap.invoke({"inner_text": "提交订单"})
        assert "页面路径已变化" in out
        assert "pages/testPage/index" in out

    def test_tap_reports_structure_change(self, fake_session):
        """点击后页面内状态变化（弹窗/文案更新）→ 结构变化信号，而非跳转。"""
        fake_session.page.page_wxml = SAMPLE_WXML
        el = fake_session.page.elements.setdefault("#submit-btn", FakeElement())
        el.on_click = lambda: setattr(
            fake_session.page, "page_wxml", SAMPLE_WXML.replace("提交订单", "已提交")
        )
        out = minium_tools.tap.invoke({"inner_text": "提交订单"})
        assert "页面结构已变化" in out

    def test_tap_reports_tap_coordinates(self, fake_session):
        """Element.tap 响应带落点坐标——排查「点错目标」的直接证据，需进返回文本。"""
        fake_session.page.page_wxml = SAMPLE_WXML
        out = minium_tools.tap.invoke({"inner_text": "提交订单"})
        assert "落点 (195, 336)" in out

    def test_tap_reports_fallback_candidates_when_no_change(self, fake_session):
        fake_session.page.page_wxml = (
            '<page><view data-sid="_As" id="_As"><view data-sid="_Ar" id="_Ar">跳转页面</view></view></page>'
        )
        out = minium_tools.tap.invoke({"inner_text": "跳转页面"})
        assert "未检测到变化" in out
        assert "同文本其他节点" in out and "#_As" in out

    def test_tap_dynamic_text_falls_back_to_native_text_contains(self, fake_session):
        """{{}} 动态文本 WXML 静态解析看不到 → 原生 text_contains 按运行时文本兜底。"""
        fake_session.page.page_wxml = "<page><button>{{btnText}}</button></page>"
        minium_tools.tap.invoke({"inner_text": "动态文案"})
        # 解析匹配为空 → 落入 tag 循环：get_element("button", text_contains=...)
        assert fake_session.page.elements["button"].clicked is True

    def test_tap_selector_failure_falls_back_to_text(self, fake_session):
        """selector 查不到但有文本 → 解析 WXML 推导真实 selector 重试。"""
        fake_session.page.page_wxml = SAMPLE_WXML
        original = fake_session.page.get_element

        def failing_get_element(selector, inner_text="", max_timeout=5):
            if selector == ".nope":
                raise RuntimeError(f"未找到 {selector}")
            return original(selector, inner_text=inner_text, max_timeout=max_timeout)

        fake_session.page.get_element = failing_get_element
        minium_tools.tap.invoke({"selector": ".nope", "inner_text": "提交订单"})
        assert fake_session.page.elements["#submit-btn"].clicked is True

    def test_element_exists_missing_returns_evidence_text(self, fake_session):
        """不存在时返回带候选清单的说明文本（fail with evidence），而非裸 False。"""
        fake_session.page.page_wxml = SAMPLE_WXML

        def failing_get_element(selector, inner_text="", max_timeout=5):
            raise RuntimeError(f"未找到 {selector}")

        fake_session.page.get_element = failing_get_element
        out = minium_tools.element_exists.invoke({"selector": ".nope"})
        assert isinstance(out, str)
        assert "当前页面含文本的元素" in out
        assert "提交订单" in out


class TestNavigationGuard:
    def test_navigate_to_skips_when_already_on_target(self, fake_session):
        fake_session.page.path = "pages/a/index"
        out = minium_tools.navigate_to.invoke({"page_path": "/pages/a/index"})
        assert "无需重复跳转" in out
        assert fake_session.app.navigations == []

    def test_navigate_to_skips_with_slash_mismatch(self, fake_session):
        """当前页带 /、参数不带 /（或反过来）都判定为同一页。"""
        fake_session.page.path = "/pages/a/index"
        out = minium_tools.navigate_to.invoke({"page_path": "pages/a/index"})
        assert "无需重复跳转" in out
        assert fake_session.app.navigations == []

    def test_navigate_to_proceeds_when_different(self, fake_session):
        fake_session.page.path = "pages/a/index"
        out = minium_tools.navigate_to.invoke({"page_path": "pages/b/index"})
        assert "导航跳转成功" in out
        assert fake_session.app.navigations == ["/pages/b/index"]

    def test_switch_tab_skips_when_already_on_target(self, fake_session):
        fake_session.page.path = "pages/chatPage/index"
        out = minium_tools.switch_tab.invoke({"tab_path": "pages/chatPage/index"})
        assert "无需重复切换" in out
        assert fake_session.app.tab_switches == []

    def test_switch_tab_proceeds_when_different(self, fake_session):
        fake_session.page.path = "pages/a/index"
        out = minium_tools.switch_tab.invoke({"tab_path": "pages/chatPage/index"})
        assert "导航跳转成功" in out
        assert fake_session.app.tab_switches == ["/pages/chatPage/index"]

    def test_unknown_current_page_does_not_block(self, fake_session):
        """当前页取不到（path 空）时不拦截——宁可重复跳，不可漏跳。"""
        fake_session.page.path = ""
        out = minium_tools.navigate_to.invoke({"page_path": "pages/a/index"})
        assert "导航跳转成功" in out


class _FakeVisionLLM:
    """假视觉模型：记录调用，返回预置回答。"""

    def __init__(self):
        self.calls: list = []
        self.answer = "看到了一个按钮"

    def invoke(self, messages):
        self.calls.append(messages)

        class _Resp:
            content = self.answer

        return _Resp()


class TestAnalyzeScreenshot:
    def _setup(self, monkeypatch, tmp_path):
        fake = _FakeVisionLLM()
        monkeypatch.setattr(
            minium_tools, "get_settings", lambda: SimpleNamespace(SESSIONS_DIR=str(tmp_path))
        )
        monkeypatch.setattr(minium_tools, "get_llm", lambda task: fake)
        minium_tools.set_run_context(session_id="s1", run_id="r1", item_id="i1")
        shot_dir = minium_tools._shot_dir()
        shot_dir.mkdir(parents=True)
        (shot_dir / "a.png").write_bytes(b"\x89PNG fake")
        return fake

    def test_sends_image_and_question_to_vision_llm(self, monkeypatch, tmp_path):
        fake = self._setup(monkeypatch, tmp_path)
        try:
            out = minium_tools.analyze_screenshot.invoke(
                {"name": "a.png", "question": "有没有按钮？"}
            )
        finally:
            minium_tools.clear_run_context()
        assert out == "看到了一个按钮"
        system, msg = fake.calls[0]
        assert "中文" in system.content
        assert msg.content[0]["text"] == "有没有按钮？"
        url = msg.content[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        assert base64.b64decode(url.split(",", 1)[1]) == b"\x89PNG fake"

    def test_name_without_extension_is_normalized(self, monkeypatch, tmp_path):
        """LLM 高频省略扩展名（传 xxx 而非 xxx.png）——工具自动补 .png。"""
        self._setup(monkeypatch, tmp_path)
        try:
            out = minium_tools.analyze_screenshot.invoke({"name": "a", "question": "q"})
        finally:
            minium_tools.clear_run_context()
        assert out == "看到了一个按钮"

    def test_missing_file_raises(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        try:
            with pytest.raises(RuntimeError, match="截图不存在"):
                minium_tools.analyze_screenshot.invoke({"name": "nope.png", "question": "q"})
        finally:
            minium_tools.clear_run_context()

    def test_illegal_name_rejected(self, monkeypatch, tmp_path):
        """拒绝带路径分隔符/..的名字（防 LLM 被注入时越界读文件）。"""
        self._setup(monkeypatch, tmp_path)
        try:
            with pytest.raises(RuntimeError, match="非法截图名"):
                minium_tools.analyze_screenshot.invoke({"name": "../../.env", "question": "q"})
        finally:
            minium_tools.clear_run_context()


class TestSnapshotAppState:
    def test_returns_page_path_all_pages_and_elements(self, fake_session):
        """快照：当前页路径 + 已配置页面 + 当前页元素清单（fake WXML 解析）。"""
        fake_session.page.path = "pages/imagePage/index"
        fake_session.app.pages = ["pages/imagePage/index", "pages/testPage/index"]
        fake_session.page.page_wxml = (
            '<page><view><button id="jump">跳转页面</button></view></page>'
        )

        snap = minium_tools.snapshot_app_state()

        assert snap["current_page"] == "pages/imagePage/index"
        assert snap["all_pages"] == ["pages/imagePage/index", "pages/testPage/index"]
        assert any("跳转页面" in e["text"] for e in snap["current_page_elements"])

    def test_leaf_own_text_used_over_descendant_concat(self, fake_session):
        """元素文本取叶子 own_text，避免父容器与子元素重复文本。"""
        fake_session.page.page_wxml = (
            '<page><view><text>跳转页面</text></view></page>'
        )
        snap = minium_tools.snapshot_app_state()
        texts = {e["text"] for e in snap["current_page_elements"]}
        assert "跳转页面" in texts

    def test_graceful_when_execute_raises(self, monkeypatch):
        """连接不可用时不抛异常，返回「不可用」说明（executor 首项也能拿到空上下文）。"""
        def boom(action, **kwargs):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(minium_tools.minium_session, "execute", boom)
        snap = minium_tools.snapshot_app_state()
        assert snap["current_page"] == ""
        assert "不可用" in snap["all_pages"]
        assert "不可用" in snap["current_page_elements"]


class TestPageScroll:
    def test_scroll_down_one_screen_reports_continue(self, fake_session):
        """每次滑动一屏：视口 600 → scrollTop 0 -> 600，说明还可以继续滑动。"""
        fake_session.page.scroll_height = 2000
        out = minium_tools.page_scroll.invoke({"direction": "down"})
        assert "0 -> 600" in out and "还可以继续滑动" in out
        assert fake_session.page.scroll_y == 600

    def test_scroll_down_at_bottom_reports_end(self, fake_session):
        """滑到头：说明无法继续滑动且不再发起滚动。总高 800、视口 600 → 最大 scrollTop 200。"""
        fake_session.page.scroll_height = 800
        out1 = minium_tools.page_scroll.invoke({"direction": "down"})
        assert "还可以继续滑动" in out1
        assert fake_session.page.scroll_y == 200   # 运行时钳位到最大可滚动位置
        out2 = minium_tools.page_scroll.invoke({"direction": "down"})
        assert "无法继续向下滑动" in out2
        assert fake_session.page.scroll_y == 200   # 位置不变
        scroll_calls = [c for c in fake_session.page.calls if c[0] == "scroll_to"]
        assert len(scroll_calls) == 1              # 第二次没有再发起滚动

    def test_scroll_up_reports_continue_from_middle(self, fake_session):
        fake_session.page.scroll_height = 2000
        fake_session.page.scroll_y = 600
        out = minium_tools.page_scroll.invoke({"direction": "up"})
        assert "600 -> 0" in out and "还可以继续滑动" in out
        assert fake_session.page.scroll_y == 0

    def test_scroll_up_at_top_reports_end(self, fake_session):
        out = minium_tools.page_scroll.invoke({"direction": "up"})
        assert "无法继续向上滑动" in out
        assert all(c[0] != "scroll_to" for c in fake_session.page.calls)

    def test_no_scroll_space_is_reported(self, fake_session):
        """内容未超屏（总高 ≤ 视口）：说明无需滚动，不发起滚动。"""
        fake_session.page.scroll_height = 500
        out = minium_tools.page_scroll.invoke({"direction": "down"})
        assert "未超出屏幕" in out
        assert all(c[0] != "scroll_to" for c in fake_session.page.calls)

    def test_invalid_direction_raises(self, fake_session):
        with pytest.raises(RuntimeError, match="非法滚动方向"):
            minium_tools.page_scroll.invoke({"direction": "left"})


class TestGetWindowSize:
    def test_returns_width_and_height(self, fake_session):
        fake_session.page.inner_size = {"width": 375, "height": 812}
        out = json.loads(minium_tools.get_window_size.invoke({}))
        assert out == {"width": 375, "height": 812}


class TestScrollView:
    def _el(self, fake_session):
        el = fake_session.page.elements.setdefault(".list", FakeElement())
        el._tag_name = "scroll-view"
        return el

    def test_scroll_down_one_viewport_reports_continue(self, fake_session):
        """容器滚一屏：内容 2000、视口 600 → scrollTop 0 -> 600，说明还可以继续滚动。"""
        el = self._el(fake_session)
        el.scroll_content_height = 2000
        out = minium_tools.scroll_view.invoke({"selector": ".list", "direction": "down"})
        assert "0 -> 600" in out and "还可以继续滚动" in out
        assert el.scroll_top == 600

    def test_scroll_down_at_bottom_reports_end(self, fake_session):
        """滚到头：内容 800、视口 600 → 最大 scrollTop 200，第二次说明无法继续。"""
        el = self._el(fake_session)
        out1 = minium_tools.scroll_view.invoke({"selector": ".list", "direction": "down"})
        assert "还可以继续滚动" in out1
        assert el.scroll_top == 200
        out2 = minium_tools.scroll_view.invoke({"selector": ".list", "direction": "down"})
        assert "无法继续向下滚动" in out2
        assert el.scroll_top == 200

    def test_scroll_up_at_top_reports_end(self, fake_session):
        self._el(fake_session)
        out = minium_tools.scroll_view.invoke({"selector": ".list", "direction": "up"})
        assert "无法继续向上滚动" in out

    def test_scroll_left_reports_continue(self, fake_session):
        el = self._el(fake_session)
        el.scroll_left = 300
        out = minium_tools.scroll_view.invoke({"selector": ".list", "direction": "left"})
        assert "300 -> 0" in out and "还可以继续滚动" in out
        assert el.scroll_left == 0

    def test_non_scroll_view_element_raises(self, fake_session):
        """目标不是 scroll-view：报可读错误提示换元素，而不是静默失败。"""
        fake_session.page.elements.setdefault(".btn", FakeElement())  # 默认 tag=view
        with pytest.raises(RuntimeError, match="不是 scroll-view"):
            minium_tools.scroll_view.invoke({"selector": ".btn"})

    def test_invalid_direction_raises(self, fake_session):
        self._el(fake_session)
        with pytest.raises(RuntimeError, match="非法滚动方向"):
            minium_tools.scroll_view.invoke({"selector": ".list", "direction": "top"})


class TestContextProvider:
    def test_provider_overrides_thread_local(self, monkeypatch, tmp_path):
        """MCP server 注入：provider 优先于 thread-local（跨线程调用也能拿到上下文）。"""
        monkeypatch.setattr(minium_tools, "get_settings",
                            lambda: SimpleNamespace(SESSIONS_DIR=str(tmp_path)))
        minium_tools.set_run_context("thread-s", "thread-r", "thread-i")
        minium_tools.set_context_provider(
            lambda: {"session_id": "mcp-s", "run_id": "mcp-r", "item_id": "mcp-i"})
        try:
            assert minium_tools._ctx_values() == {
                "session_id": "mcp-s", "run_id": "mcp-r", "item_id": "mcp-i"}
            assert minium_tools._shot_dir().as_posix().endswith(
                "screenshots/mcp-s/mcp-r/mcp-i")
        finally:
            minium_tools.set_context_provider(None)
            minium_tools.clear_run_context()

    def test_provider_error_falls_back_to_thread_local(self):
        """provider 异常静默降级 thread-local（不炸工具调用）。"""
        minium_tools.set_run_context("thread-s", "thread-r", "thread-i")

        def boom():
            raise RuntimeError("boom")

        minium_tools.set_context_provider(boom)
        try:
            assert minium_tools._ctx_values()["session_id"] == "thread-s"
        finally:
            minium_tools.set_context_provider(None)
            minium_tools.clear_run_context()


class TestNavigateBack:
    def test_navigate_back_reports_page_change(self, fake_session):
        """回退成功：返回前后页面路径变化。"""
        fake_session.page.path = "pages/testPage/index"
        fake_session.app.back_target = "pages/imagePage/index"
        out = minium_tools.navigate_back.invoke({})
        assert "pages/imagePage/index" in out and "->" in out
        assert ("navigate_back", (1,)) in fake_session.app.calls

    def test_navigate_back_at_root_reports_no_change(self, fake_session):
        """栈底回退：如实说明页面未变化，不假装成功。"""
        out = minium_tools.navigate_back.invoke({})
        assert "页面未变化" in out

    def test_navigate_back_with_delta(self, fake_session):
        """delta 透传：多级回退。"""
        minium_tools.navigate_back.invoke({"delta": 2})
        assert ("navigate_back", (2,)) in fake_session.app.calls
