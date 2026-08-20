"""minium 假对象 — mock 验证工具调用链（无真实开发者工具环境）。"""

from pathlib import Path


class FakeElement:
    """假元素：记录点击/输入，inner_text / inner_wxml 可预置。

    on_click 回调可预置（测试「点击后页面跳转」验证分支用）。
    """

    def __init__(self, text: str = "", wxml: str = ""):
        self._text = text
        self._wxml = wxml
        self.clicked = False
        self.inputs: list[str] = []
        self.on_click = None   # 测试钩子：click 时调用

    @property
    def inner_text(self) -> str:
        return self._text

    @property
    def inner_wxml(self) -> str:
        return self._wxml

    def click(self) -> None:
        self.tap()

    def styles(self, name: str = ""):
        return None

    def tap(self):
        """对齐 minium：tap 返回落点坐标 dict；click 内部就是 tap。"""
        self.clicked = True
        if self.on_click:
            self.on_click()
        return {"pageX": 195, "pageY": 336, "clientX": 195, "clientY": 336}

    def input(self, text: str) -> None:
        self.inputs.append(text)


class FakePage:
    """假页面：get_element / get_elements / element_is_exists（minium 1.6 都在 CurrentPage 上）。

    page_wxml 可预置当前页 WXML（get_page_elements / 文本定位解析用）。
    """

    def __init__(self):
        self.elements: dict[str, FakeElement] = {}
        self.exists: dict[str, bool] = {}
        self.calls: list[tuple] = []   # (method, args)
        self.page_wxml: str = ""       # 当前页 WXML（测试预置）
        self.path: str = "pages/index/index"   # 当前页面路径（tap 跳转验证用）

    def get_element(self, selector, inner_text="", max_timeout=5, **kwargs):
        self.calls.append(("get_element", (selector, inner_text, max_timeout)))
        return self.elements.setdefault(selector, FakeElement())

    def get_elements(self, selector, max_timeout=0, **kwargs):
        self.calls.append(("get_elements", (selector, max_timeout)))
        if selector == "page":
            return [FakeElement(wxml=self.page_wxml)]
        return []

    def element_is_exists(self, selector=None, max_timeout=10, inner_text=None,
                          text_contains=None, value=None, xpath=None) -> bool:
        self.calls.append(("element_is_exists", (selector,)))
        return self.exists.get(selector, True)


class FakeApp:
    """假 App：记录导航/切换/截图，get_all_pages_path 返回预置页表。

    对齐 minium 1.6.0：navigate_to(url, params) / switch_tab(url) 原生签名；
    fail_paths 预置失败路径，命中即抛异常（模拟 minium 真实行为）。
    """

    def __init__(self, shots_dir: Path):
        self.shots_dir = shots_dir
        self.navigations: list[str] = []
        self.tab_switches: list[str] = []
        self.screenshots: list[str] = []
        self.pages: list[str] = ["pages/loginPage/index", "pages/chatPage/index"]
        self.calls: list[tuple] = []   # (method, args)
        self.fail_paths: set = set()   # 预置导航失败路径（命中即抛异常）
        self.current_page = None       # 由 FakeMiniumSession 装配

    def get_current_page(self):
        self.calls.append(("get_current_page", ()))
        return self.current_page

    def navigate_to(self, url, params=None, is_wait_url_change=True):
        self.calls.append(("navigate_to", (url,)))
        if url in self.fail_paths:
            raise RuntimeError(f"模拟导航失败: {url}")
        self.navigations.append(url)

    def switch_tab(self, url, *args, **kwargs):
        self.calls.append(("switch_tab", (url,)))
        if url in self.fail_paths:
            raise RuntimeError(f"模拟切换失败: {url}")
        self.tab_switches.append(url)

    def get_all_pages_path(self):
        self.calls.append(("get_all_pages_path", ()))
        return self.pages

    def screen_shot(self, save_path=None, format="raw", use_native=False) -> None:
        self.calls.append(("screen_shot", (save_path,)))
        self.screenshots.append(save_path)
        p = Path(save_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fake-png-data")


class FakeMiniumSession:
    """假 minium 会话：app/page 两级假对象 + 调用历史。"""

    def __init__(self, shots_dir: Path):
        self.app = FakeApp(shots_dir)
        self.page = FakePage()
        self.app.current_page = self.page
