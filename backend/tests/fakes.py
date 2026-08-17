"""minium 假对象 — mock 验证工具调用链（无真实开发者工具环境）。"""

from pathlib import Path


class FakeElement:
    """假元素：记录点击/输入，inner_text 可预置。"""

    def __init__(self, text: str = ""):
        self._text = text
        self.clicked = False
        self.inputs: list[str] = []

    @property
    def inner_text(self) -> str:
        return self._text

    def click(self) -> None:
        self.clicked = True

    def input(self, text: str) -> None:
        self.inputs.append(text)


class FakePage:
    """假页面：get_element / element_is_exists（minium 1.6 两者都在 CurrentPage 上）。"""

    def __init__(self):
        self.elements: dict[str, FakeElement] = {}
        self.exists: dict[str, bool] = {}
        self.calls: list[tuple] = []   # (method, args)

    def get_element(self, selector, inner_text="", max_timeout=5):
        self.calls.append(("get_element", (selector, inner_text, max_timeout)))
        return self.elements.setdefault(selector, FakeElement())

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
