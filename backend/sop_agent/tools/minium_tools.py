"""minium 工具集 — 供 executor Agent 调用的微信小程序自动化工具。

架构：工具只依赖 minium_session 抽象层，与 executor 无耦合——
未来抽成独立 MCP server 时本文件原样搬走。

元素定位策略（LLM 盲猜 selector 的解法，分三层）：
1. 发现层：get_page_elements 抓当前页真实元素清单（解析 WXML），LLM 从中选 selector；
2. 文本定位：tap/get_text/element_exists 支持「只给 inner_text 不给 selector」——
   内部解析 WXML 按文本推导真实 selector（id > class > tag）再定位；
3. 失败证据：定位失败时异常文本附「当前页面含文本的元素清单」，LLM 据此换目标。
"""

import base64
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from lxml import etree
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from ..core.config import get_settings
from ..core.llm import get_llm
from . import minium_session

from rich import print as rPrint

# run context（thread-local）：executor 节点设置，screenshot 据此拼路径
_ctx = threading.local()



def set_run_context(session_id: str, run_id: str, item_id: str) -> None:
    _ctx.session_id = session_id
    _ctx.run_id = run_id
    _ctx.item_id = item_id


def clear_run_context() -> None:
    _ctx.session_id = ""
    _ctx.run_id = ""
    _ctx.item_id = ""


def _run(action) -> Any:
    return minium_session.execute(action, run_id=getattr(_ctx, "run_id", ""))


# ──────────────────────────────────────────────
# 工具定义（中文 docstring 供 LLM 决策调用）
# ──────────────────────────────────────────────

def _normalize_path(page_path: str) -> str:
    """剥光所有前导斜杠。原生导航 API 的路径契约要求以 / 开头（PITFALLS 6.3），
    调用方用 "/" + _normalize_path(x) 保证恰好一个前导斜杠。"""
    return page_path.lstrip("/")


def _is_current_page(s, page_path: str) -> bool:
    """当前页是否已是目标页（两侧都剥前导斜杠再比——minium 返回的 page.path
    与调用方传参都可能带或不带 /）。当前页取不到时不拦截（宁可重复跳，不可漏跳）。"""
    current = _current_page_path(s).strip().lstrip("/")
    return bool(current) and current == _normalize_path(page_path)


# ──────────────────────────────────────────────
# 元素定位基础设施（发现层 / 文本定位层 / 失败证据层）
# ──────────────────────────────────────────────

_WXML_TEXT_TRUNCATE = 30   # 清单中单个元素文本截断长度
_EVIDENCE_LIMIT = 8        # 失败证据中候选元素个数
_TEXT_CONTAINER_TAGS = ("button", "text", "view")  # 文本兜底检索的容器 tag


def _fetch_wxml(s) -> str:
    """抓取当前页面 WXML 源码（一次协议往返）。

    minium 无「全部元素」通配选择器，但 page 根节点自带 inner_wxml
    （page.py 自身的 wxml 属性同款取法），解析它即可枚举全部元素。
    """
    pages = s.page.get_elements("page", max_timeout=0)
    if not pages or not getattr(pages[0], "inner_wxml", ""):
        raise RuntimeError("当前页面 WXML 不可用（页面可能未加载完成）")

    inner_wxml = pages[0].inner_wxml

    rPrint(f"[bold red]==========工具调用结果-_fetch_wxml==========[/bold red]")
    rPrint(inner_wxml)
    rPrint(f"[bold red]==========工具调用结果-_fetch_wxml==========[/bold red]")

    return inner_wxml


def _parse_wxml(wxml: str) -> list[dict]:
    """解析 WXML 提取元素清单 [{tag, class, id, text, own_text}]（纯函数，可单测）。

    - `wx:` 前缀属性先替换成 `wx_`（lxml 对未声明命名空间前缀直接报错）；
    - text 取该元素全部后代文本拼接（对齐 inner_text 语义）；
    - own_text 只取该元素**自身直接文本**（不含后代）——父容器与子元素同文本
      （如 <view><view>跳转页面</view></view>）时，own_text 是叶子判据：
      文本直接落在哪个节点，点击事件的可达目标就从哪个节点起算（冒泡语义）。
    """
    wxml = re.sub(r"wx:", "wx_", wxml)
    try:
        root = etree.fromstring(wxml.encode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"页面 WXML 解析失败: {e}") from e
    items = []
    for el in root.iter():
        tag = etree.QName(el).localname
        text = "".join(el.xpath(".//text()")).strip()
        own_text = "".join(el.xpath("text()")).strip()
        items.append({
            "tag": tag,
            "class": el.get("class", ""),
            "id": el.get("id", ""),
            "text": text[:_WXML_TEXT_TRUNCATE],
            "own_text": own_text[:_WXML_TEXT_TRUNCATE],
        })
    return items


def _pick_selector(el: dict) -> str:
    """从元素信息推导 minium 可用的 CSS 选择器（纯函数，可单测）。

    优先级：id > 首个 class > tag。Taro 编译产物的 class 名虽不可读
    但真实存在，直接拿来定位有效。
    """
    if el.get("id"):
        return f"#{el['id']}"
    if el.get("class"):
        return "." + el["class"].split()[0]
    return el.get("tag") or "view"


def _match_candidates(wxml: str, inner_text: str) -> list[dict]:
    """按「从内到外」排序的文本匹配候选（纯函数，可单测）。

    叶子优先不是猜测，是冒泡语义：点叶子（own_text 命中的最深节点），
    事件 target=叶子并向上冒泡，无论 handler 挂在叶子还是任一祖先都会被
    触发；点祖先则永远触达不到子孙节点上的 handler。排序键 = text 长度
    （最深节点后代文本最短），own_text 命中者整体排在祖先容器之前。
    page 根节点排除在外——它的后代拼接文本必然命中任何查询，是纯噪声。
    """
    matches = [
        e for e in _parse_wxml(wxml)
        if inner_text in e["text"] and e["tag"] != "page"
    ]
    own = [e for e in matches if inner_text in e.get("own_text", "")]
    rest = [e for e in matches if e not in own]
    own.sort(key=lambda e: len(e["text"]))
    rest.sort(key=lambda e: len(e["text"]))
    return own + rest


def _page_text_evidence(s, limit: int = _EVIDENCE_LIMIT) -> str:
    """失败证据：当前页面含文本的元素清单（fail with evidence）。

    只列 own_text 非空的叶子节点——父容器的后代拼接文本与子元素重复，
    列出来会误导 LLM 选到「点了也没反应」的容器。
    """
    try:
        items = [e for e in _parse_wxml(_fetch_wxml(s)) if e["own_text"]]
    except Exception:
        return ""
    if not items:
        return "（当前页面无含文本元素）"
    shown = "、".join(
        f"<{e['tag']} class={e['class'] or '-'}>「{e['own_text'][:12]}」" for e in items[:limit]
    )
    return f"当前页面含文本的元素: {shown}"


def _do_click(el) -> str:
    """点击元素，返回落点坐标文本（排查证据）。

    复刻 minium click() 的 pointer-events 守卫 + 1s 落定，但用 el.tap()
    直接捕获协议响应——Element.tap 的响应带 {pageX, pageY}（minium 在
    元素中心点发出的原生 tap 坐标）。注意坐标证明「点在哪」，不证明
    「谁收到事件」：中心点被遮挡层盖住时命中测试会改判 target。
    """
    try:
        styles = el.styles("pointer-events")
    except Exception:
        styles = None
    if styles and styles[0] == "none":
        raise RuntimeError("元素 pointer-events=none，无法点击")
    res = el.tap()
    if isinstance(res, dict) and "pageX" in res:
        coords = f"，落点 ({res.get('pageX')}, {res.get('pageY')})"
    else:
        coords = ""
    time.sleep(1)
    return coords


def _page_state_snapshot(s) -> tuple[str, str]:
    """页面状态快照（路径 + WXML 哈希）——点击前后对比的通用变化信号。

    不针对任何特定业务反应（跳转/toast/弹窗/状态更新）：凡在页面结构里
    体现的变化都会改变 WXML 哈希；原生层反馈（wx.showToast/showModal）
    不体现在页面 WXML 里，可能检测不到——因此「未检测到变化」≠「点击失败」。
    """
    try:
        wxml = _fetch_wxml(s)
        digest = hashlib.md5(wxml.encode("utf-8")).hexdigest()
    except Exception:
        digest = ""
    return _current_page_path(s), digest


def _change_note(before: tuple[str, str], after: tuple[str, str]) -> str:
    """点击前后差异的中性描述（只陈述事实，不做成败判断）。"""
    b_path, b_hash = before
    a_path, a_hash = after
    if b_path != a_path:
        return f"页面路径已变化: {b_path} -> {a_path}"
    if a_hash and b_hash and a_hash != b_hash:
        return "页面结构已变化（弹窗/状态更新等在页面内体现的变化）"
    return "页面路径与结构均未检测到变化（原生 toast/modal 等原生层反馈不体现在页面结构中，点击可能已生效）"


def _current_page_path(s) -> str:
    """当前页面路径（点击后验证用；拿不到返回空串，验证静默失效）。"""
    try:
        page = s.app.get_current_page()
        return getattr(page, "path", "") or ""
    except Exception:
        return ""


def snapshot_app_state() -> dict:
    """小程序当前状态快照（跨检查项执行上下文，executor 每项执行前注入）。

    非 @tool（LLM 不直接调用）。会话是进程级单例，快照即上一检查项
    结束时的真实现场：current_page 当前页面路径、all_pages 已配置页面、
    current_page_elements 当前页元素清单（前 15 个，文本取叶子 own_text）。
    每段独立容错：连接不可用时返回「不可用」说明而非抛异常。
    """
    def act(s):
        state = {"current_page": _current_page_path(s)}
        try:
            state["all_pages"] = list(s.app.get_all_pages_path())
        except Exception as e:
            state["all_pages"] = f"不可用: {e}"
        try:
            state["current_page_elements"] = [
                {"tag": e["tag"], "text": e["own_text"] or e["text"]}
                for e in _parse_wxml(_fetch_wxml(s))[:15]
            ]
        except Exception as e:
            state["current_page_elements"] = f"不可用: {e}"
        return state

    try:
        return _run(act)
    except Exception as e:
        return {
            "current_page": "",
            "all_pages": f"不可用: {e}",
            "current_page_elements": f"不可用: {e}",
        }


def _get_element(s, selector: str, inner_text: str, max_timeout: int):
    """定位元素（工具内部统一入口）。

    优先级链：
    1. selector 直接查 minium 原生（可带 inner_text 精筛）；
    2. selector 失败/未提供但给了 inner_text → 解析 WXML 按文本推导真实
       selector（叶子优先，id > class > tag）再查；
    3. 全部失败 → RuntimeError 附候选清单证据，供 LLM 下一轮换目标。
    """
    if selector:
        try:
            return s.page.get_element(selector, inner_text=inner_text, max_timeout=max_timeout)
        except Exception:
            if not inner_text:
                raise RuntimeError(
                    f"未找到元素（selector={selector!r}）。{_page_text_evidence(s)}"
                ) from None
            # selector 失败但有文本 → 落入文本推导路径
    if not inner_text:
        raise RuntimeError("selector 与 inner_text 至少提供一个")

    # 1) 解析 WXML 按文本推导真实 selector（静态文本命中率高）
    candidates = _match_candidates(_fetch_wxml(s), inner_text)
    if candidates:
        derived = _pick_selector(candidates[0])
        try:
            return s.page.get_element(derived, max_timeout=max_timeout)
        except Exception:
            pass  # 推导失败 → 落入原生 text_contains 兜底

    # 2) 原生 text_contains 兜底：{{}} 动态文本在 WXML 里不可见，
    #    但 minium 原生按运行时 inner_text 过滤（见 filter_elements）
    for tag in _TEXT_CONTAINER_TAGS:
        try:
            return s.page.get_element(tag, text_contains=inner_text, max_timeout=max_timeout)
        except Exception:
            continue

    raise RuntimeError(
        f"未找到文本包含「{inner_text}」的元素（selector={selector!r}）。{_page_text_evidence(s)}"
    )


# ──────────────────────────────────────────────
# 工具定义（中文 docstring 供 LLM 决策调用）
# ──────────────────────────────────────────────

@tool
def navigate_to(page_path: str) -> str:
    """导航到指定普通页面用这个工具，如果是tabBar页面用switch_tab工具。page_path: 页面路径（如 pages/index/index，可带或不带前导斜杠）。已在目标页面时自动跳过，不重复压栈。失败时返回可读错误文本。"""
    def act(s):
        target = "/" + _normalize_path(page_path)
        if _is_current_page(s, page_path):
            return f"已在目标页面（{target}），无需重复跳转"
        s.app.navigate_to(target, None)
        return '普通页面导航跳转成功'

    return _run(act)


@tool
def switch_tab(tab_path: str) -> str:
    """切换到指定 tabBar 页面（只能 tabBar 页）。tab_path: tabBar 页面路径，可带或不带前导斜杠。已在目标页面时自动跳过，不重复切换。失败时返回可读错误文本。"""
    def act(s):
        target = "/" + _normalize_path(tab_path)
        if _is_current_page(s, tab_path):
            return f"已在目标 tabBar 页面（{target}），无需重复切换"
        # 归一化：无论 LLM 传 /pages/x、pages/x 还是 //pages/x，保证恰好一个前导斜杠
        s.app.switch_tab(target)
        return 'tabBar页面导航跳转成功'

    return _run(act)


@tool
def get_page_elements(limit: int = 30) -> str:
    """获取当前页面真实元素清单（tag/class/id/文本，JSON 数组）。操作或断言任何页面元素前，必须先调用本工具发现真实元素，从中选取 selector——禁止凭想象猜 selector。limit: 最多返回元素个数。"""
    def act(s):
        items = _parse_wxml(_fetch_wxml(s))[:limit]
        if not items:
            return "当前页面无元素（页面可能未加载完成，稍后重试）"
        return json.dumps(items, ensure_ascii=False)

    return _run(act)


@tool
def tap(selector: str = "", inner_text: str = "", max_timeout: int = 5) -> str:
    """点击页面元素。selector 与 inner_text 至少提供一个，优先用 inner_text（元素可见文本，PRD 描述里通常是文本；无文本时才用 selector）。selector: 元素选择器（必须来自 get_page_elements 结果，WXSS 选择器或 XPath）；inner_text: 元素可见文本（文本定位按叶子优先：点击从最内层节点发出，冒泡保证无论事件绑在叶子还是祖先都会被触发）；max_timeout: 等待元素出现的秒数。返回含落点坐标与中性的页面状态变化信号（路径/结构变化或未检测到变化）——信号仅供参考，是否达成预期必须按 check_steps 的 expected_result 自行验证（toast/原生弹窗等不体现在页面结构中）。"""
    def act(s):
        label = selector or f"文本「{inner_text}」"
        before = _page_state_snapshot(s)
        el = _get_element(s, selector, inner_text, max_timeout)
        coords = _do_click(el)
        note = _change_note(before, _page_state_snapshot(s))
        if "未检测到变化" in note:
            # 附同文本其他节点供补点评估（中性陈述：不预设点击失败）
            try:
                others = "、".join(
                    _pick_selector(e) for e in _match_candidates(_fetch_wxml(s), inner_text)[1:4]
                )
                note += f"；同文本其他节点: {others}" if others else ""
            except Exception:
                pass
        return f"已点击 {label}{coords}，{note}。是否达成预期请按 check_steps 的 expected_result 用 get_text/element_exists/screenshot 验证"

    return _run(act)


@tool
def input_text(selector: str, text: str, max_timeout: int = 5) -> str:
    """向输入框输入文本。selector: 输入框选择器（必须来自 get_page_elements 结果）；text: 要输入的文本；max_timeout: 等待元素出现的秒数。"""
    def act(s):
        try:
            el = s.page.get_element(selector, max_timeout=max_timeout)
        except Exception as e:
            raise RuntimeError(
                f"未找到输入框（selector={selector!r}）: {e}。{_page_text_evidence(s)}"
            ) from e
        el.input(text)
        return f"已向 {selector} 输入 {text!r}"

    return _run(act)


@tool
def get_text(selector: str = "", inner_text: str = "", max_timeout: int = 5) -> str:
    """获取元素文本内容。selector 与 inner_text 至少提供一个，优先 inner_text（定位规则同 tap）。selector: 元素选择器（必须来自 get_page_elements 结果）；inner_text: 元素可见文本；max_timeout: 等待元素出现的秒数。"""
    def act(s):
        el = _get_element(s, selector, inner_text, max_timeout)
        text = getattr(el, "inner_text", None)
        if text is None:
            text = el.text  # 版本差异兜底
        return str(text)

    return _run(act)


@tool
def element_exists(selector: str = "", inner_text: str = "", max_timeout: int = 5) -> Any:
    """检查元素是否存在。元素存在返回 True；不存在时返回说明文本（附当前页面候选元素清单，可直接据此换选择器重试），而不是 False。selector 与 inner_text 至少提供一个，定位规则同 tap。max_timeout: 等待秒数。"""
    def act(s):
        try:
            _get_element(s, selector, inner_text, max_timeout)
            return True
        except RuntimeError as e:
            return str(e)

    return _run(act)


@tool
def get_window_size() -> str:
    """获取小程序视口（窗口）宽高，返回 JSON {"width": 宽, "height": 高}（单位 px）。用于布局/适配类检查（如判断元素是否超出屏幕宽度、内容是否超屏）和滚动量估算。"""
    def act(s):
        size = s.page.inner_size
        return json.dumps(size, ensure_ascii=False)

    return _run(act)


@tool
def page_scroll(direction: str = "down") -> str:
    """滚动当前页面一屏（一个视口高度）。direction: down 向下（默认）/ up 向上。
    返回自然语言说明：滑动成功时给出滚动前后位置并说明还可以继续滑动；
    已到顶/底或页面无滚动空间时说明无法继续滑动。滚动后应重新调用
    get_page_elements 发现新露出的元素。"""
    def act(s):
        p = s.page
        viewport = max(p.inner_size.get("height", 600), 1)
        total = p.scroll_height
        cur = p.scroll_y
        if direction == "down":
            can = cur < max(total - viewport, 0)
            target = cur + viewport
        elif direction == "up":
            can = cur > 0
            target = cur - viewport
        else:
            raise RuntimeError(f"非法滚动方向 {direction!r}（仅支持 down/up）")
        if can:
            p.scroll_to(int(target), 300)

        rPrint(f"[bold red]==========工具调用结果-page_scroll==========[/bold red]")
        rPrint(f"direction={direction}, inner_size={p.inner_size}, total={total} can={can}, scrollTop {cur} -> {int(target)}")
        rPrint(f"[bold red]==========工具调用结果-page_scroll==========[/bold red]")

        if can:
            return (
                f"已向{'下' if direction == 'down' else '上'}滑动一屏："
                f"scrollTop {cur} -> {int(target)}（总高 {total}，视口高 {viewport}），"
                f"还可以继续滑动"
            )
        if total <= viewport:
            return f"无法继续滑动：页面内容未超出屏幕（总高 {total} ≤ 视口高 {viewport}），无需滚动"
        if direction == "down":
            return f"无法继续向下滑动：已到页面底部（scrollTop {cur}，总高 {total}，视口高 {viewport}）"
        return f"无法继续向上滑动：已在页面顶部（scrollTop {cur}）"

    return _run(act)


def _as_number(value: Any, default: float = 0) -> float:
    """minium 运行时返回值可能是裸数值或包一层 dict（版本差异），统一取数。"""
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        for k in ("result", "value"):
            if isinstance(value.get(k), (int, float)):
                return value[k]
    return default


@tool
def scroll_view(selector: str = "", inner_text: str = "", direction: str = "down") -> str:
    """滚动页面内 scroll-view 容器一屏（scroll-view 自身滚动；page_scroll 只滚页面本身、对容器无效）。selector/inner_text 定位 scroll-view 元素（scroll-view 通常无自身文本，优先用 selector，必须来自 get_page_elements 结果）；direction: down 向下 / up 向上 / left 向左 / right 向右。返回自然语言说明：滚动成功时给出前后位置并说明还可以继续滚动；已滚到头时说明无法继续滚动。"""
    def act(s):
        el = _get_element(s, selector, inner_text, max_timeout=5)
        if getattr(el, "_tag_name", "") != "scroll-view":
            raise RuntimeError(
                f"目标元素不是 scroll-view（tag={getattr(el, '_tag_name', '?')}），"
                f"无法容器滚动。请用 get_page_elements 找到 scroll-view 元素再传其 selector"
            )
        vertical = direction in ("down", "up")
        horizontal = direction in ("left", "right")
        if not vertical and not horizontal:
            raise RuntimeError(f"非法滚动方向 {direction!r}（仅支持 down/up/left/right）")
        size = el.size or {}
        viewport = max(_as_number(size.get("height") if vertical else size.get("width")), 1)
        before = _as_number(el.scroll_top if vertical else el.scroll_left)
        if direction == "down":
            target = before + viewport
            el.scroll_to(0, int(target))
        elif direction == "up":
            target = before - viewport
            el.scroll_to(0, int(target))
        elif direction == "right":
            target = before + viewport
            el.scroll_to(int(target), 0)
        else:  # left
            target = before - viewport
            el.scroll_to(int(target), 0)
        time.sleep(0.3)
        after = _as_number(el.scroll_top if vertical else el.scroll_left)

        rPrint(f"[bold red]==========工具调用结果-scroll_view==========[/bold red]")
        rPrint(f"direction={direction}, before={before}, after={after}, target={int(target)}")
        rPrint(f"[bold red]==========工具调用结果-scroll_view==========[/bold red]")

        label = {"down": "下", "up": "上", "left": "左", "right": "右"}[direction]
        axis = "scrollTop" if vertical else "scrollLeft"
        if after != before:
            return (
                f"已向{label}滚动 scroll-view 一屏：{axis} {before} -> {after}"
                f"（视口{'高' if vertical else '宽'} {viewport}），还可以继续滚动"
            )
        return f"无法继续向{label}滚动 scroll-view：已滚到头（{axis} {after} 未变化）"

    return _run(act)


@tool
def get_pages() -> list:
    """获取小程序已配置的所有页面路径（用于发现真实页面，导航前先查）。"""
    def act(s):
        return list(s.app.get_all_pages_path())

    res = _run(act)

    rPrint(f"[bold blue]==========工具调用结果-get_pages==========[/bold blue]")
    rPrint(res)
    rPrint(f"[bold blue]==========工具调用结果-get_pages==========[/bold blue]")

    return res


@tool
def screenshot(name: str) -> str:
    """对当前页面截图存档。name: 截图名称（不含扩展名），返回存档文件名。"""
    def act(s):
        base = _shot_dir()
        base.mkdir(parents=True, exist_ok=True)
        abs_path = base / f"{name}.png"
        _capture(s, str(abs_path))
        return abs_path.name

    return _run(act)


def _shot_dir() -> Path:
    """当前 run context 下的截图目录。"""
    session_id = getattr(_ctx, "session_id", "unknown")
    run_id = getattr(_ctx, "run_id", "unknown")
    item_id = getattr(_ctx, "item_id", "unknown")
    return Path(get_settings().SESSIONS_DIR) / "screenshots" / session_id / run_id / item_id


@tool
def analyze_screenshot(name: str, question: str) -> str:
    """用视觉模型分析截图（截图 + 问题 → 视觉模型回答）。典型用法：①验证点击/操作后的预期结果（如「页面上是否有 toast 提示？内容是什么？」「弹窗是否出现？」）；②定位元素（如「文本为『提交订单』的按钮在页面什么位置？描述它的外观和周围文字」）；③界面状态判定（「登录按钮当前是置灰还是可用？」）。name: screenshot 工具返回的文件名（可带可不带 .png 扩展名）；question: 要视觉模型回答的问题，尽量具体、只问一个明确问题。注意：本工具不依赖开发者工具连接，只读已存档截图。"""
    def act(_s):
        if ".." in name or "/" in name or "\\" in name:
            raise RuntimeError(f"非法截图名: {name!r}（只允许纯文件名）")
        fixed = name if name.lower().endswith(".png") else f"{name}.png"
        abs_path = _shot_dir() / fixed
        if not abs_path.exists():
            raise RuntimeError(f"截图不存在: {fixed}（先调用 screenshot 工具存档）")
        b64 = base64.b64encode(abs_path.read_bytes()).decode("ascii")
        llm = get_llm("screenshot_analysis")
        system = SystemMessage(content="你是截图视觉分析助手，请始终使用中文回答。")
        msg = HumanMessage(content=[
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ])
        resp = llm.invoke([system, msg])
        return str(resp.content)

    return act(None)   # 纯文件 + LLM，不占 minium 会话锁、不要求 DevTools 连接


def _capture(session: Any, abs_path: str) -> None:
    """截图落盘（minium 1.6: app.screen_shot；版本差异兜底：keyword → bytes）。"""
    app = session.app
    try:
        app.screen_shot(save_path=abs_path)
    except TypeError:
        data = app.screen_shot()
        if isinstance(data, (bytes, bytearray)):
            Path(abs_path).write_bytes(bytes(data))
        else:
            raise


EXECUTOR_TOOLS = [navigate_to, switch_tab, get_page_elements, get_window_size, page_scroll, scroll_view, tap, input_text, get_text, element_exists, get_pages, screenshot, analyze_screenshot]
TOOL_MAP = {t.name: t for t in EXECUTOR_TOOLS}
