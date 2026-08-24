"""真机导航探针 — 排查「导航目标页面失败」用。

前置：微信开发者工具已打开项目、服务端口已开启（PITFALLS 6.1）。
用法：uv run python backend/probe_nav.py
"""

import os
import time

from mcp_server.tools import minium_session


def ev(js: str):
    r = s.app.evaluate(js, sync=True)
    return r.result.get("result") if getattr(r, "result", None) else r


def route():
    return ev(
        "function(){ var p=getCurrentPages(); "
        "return p.map(function(x){ return x.route; }); }"
    )


def rel_path(target: str) -> str:
    """按当前页目录把目标页转成相对路径（app 代理按当前目录解析相对 url）。"""
    cur = route()
    if not cur:
        return target
    cur_dir = "/".join(cur[-1].split("/")[:-1])
    return os.path.relpath(target, cur_dir).replace("\\", "/")


def nav(method: str, url: str):
    """派发导航（接回调写标记）→ 等落定 → 读标记 + 真实路由。"""
    print(f"\n--- {method} -> {url} ---")
    out = ev(
        "function(){ globalThis.__nav_res = undefined; "
        f"wx.{method}({{url:'{url}', "
        "success: function(){ globalThis.__nav_res='ok'; }, "
        "fail: function(e){ globalThis.__nav_res='fail: '+(e&&(e.errMsg||e.message)||String(e)); }"
        "}); return 'dispatched'; }"
    )
    print("dispatch:", out)
    time.sleep(2)
    print("marker:", ev(
        "function(){ return (typeof globalThis.__nav_res !== 'undefined') "
        "? globalThis.__nav_res : 'pending'; }"
    ))
    time.sleep(1)
    print("route:", route())


s = minium_session.get_session()

print("=== 1. 真实页面表（get_all_pages_path）===")
pages = s.app.get_all_pages_path()
print(pages)

print("=== 2. 当前路由（getCurrentPages）===")
cur = route()
print(cur)

target = "pages/giftPage/index"
print(f"=== 3. switchTab 候选矩阵（目标: {target}）===")
forms = {
    "relative": rel_path(target),
    "absolute": "/" + target,
}
print("候选:", forms)

for label, url in forms.items():
    print(f"\n### 候选 [{label}]: {url}")
    nav("switchTab", url)
