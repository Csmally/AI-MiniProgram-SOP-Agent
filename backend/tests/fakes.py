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
        self._tag_name: str = "view"          # scroll_view 工具校验 tag 用
        self.scroll_top: int = 0              # scroll-view 当前滚动位置
        self.scroll_left: int = 0
        self.size: dict = {"width": 375, "height": 600}   # 元素可视尺寸（对齐 Rect）
        self.scroll_content_height: int = 800  # 内容总高（scroll_to 钳位用）
        self.scroll_content_width: int = 800

    @property
    def inner_text(self) -> str:
        return self._text

    @property
    def inner_wxml(self) -> str:
        return self._wxml

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

    def scroll_to(self, x=0, y=0):
        """对齐 minium：scroll-view 滚动到指定位置（运行时钳位到 [0, 内容−视口]）。"""
        self.scroll_top = max(0, min(y, max(self.scroll_content_height - self.size["height"], 0)))
        self.scroll_left = max(0, min(x, max(self.scroll_content_width - self.size["width"], 0)))


class FakePage:
    """假页面：get_element / get_elements（minium 1.6 都在 CurrentPage 上）。

    page_wxml 可预置当前页 WXML（get_page_elements / 文本定位解析用）。
    """

    def __init__(self):
        self.elements: dict[str, FakeElement] = {}
        self.calls: list[tuple] = []   # (method, args)
        self.page_wxml: str = ""       # 当前页 WXML（测试预置）
        self.path: str = "pages/index/index"   # 当前页面路径（tap 跳转验证用）
        self.scroll_height: int = 800  # 页面总高（page_scroll 用）
        self.scroll_y: int = 0         # 当前滚动位置（scroll_to 后更新）
        self.inner_size: dict = {"width": 375, "height": 600}   # 视口尺寸

    def scroll_to(self, scroll_top, duration=300):
        """对齐 minium 1.6：page.scroll_to(scroll_top, duration)。

        scrollTop 由运行时钳位到 [0, 总高−视口高]（与真实 pageScrollTo 一致）。
        """
        self.calls.append(("scroll_to", (scroll_top, duration)))
        max_top = max(self.scroll_height - self.inner_size["height"], 0)
        self.scroll_y = max(0, min(scroll_top, max_top))

    def get_element(self, selector, inner_text="", max_timeout=5, **kwargs):
        self.calls.append(("get_element", (selector, inner_text, max_timeout)))
        return self.elements.setdefault(selector, FakeElement())

    def get_elements(self, selector, max_timeout=0, **kwargs):
        self.calls.append(("get_elements", (selector, max_timeout)))
        if selector == "page":
            return [FakeElement(wxml=self.page_wxml)]
        return []


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
        self.back_target = None        # navigate_back 后模拟停留的页面路径
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

    def navigate_back(self, delta=1):
        """对齐 minium 1.6：app.navigate_back(delta)（栈底 no-op 返回当前页）。

        back_target 可预置：调用后把当前页路径设为目标（模拟回退后的页面变化）。
        """
        self.calls.append(("navigate_back", (delta,)))
        if self.back_target is not None and self.current_page is not None:
            self.current_page.path = self.back_target
        return self.current_page

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
