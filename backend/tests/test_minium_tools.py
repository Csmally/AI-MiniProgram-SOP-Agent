"""minium 工具集单测 — 元素清单解析 / 选择器推导 / 文本定位链 / 失败证据。"""

import base64
import json
from types import SimpleNamespace

import pytest

from sop_agent.tools import minium_tools
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
        msg = fake.calls[0][0]
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
