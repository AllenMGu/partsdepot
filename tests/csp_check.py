"""CSP 真实浏览器验证（Chromium 实际加载各页，监听 CSP 违规）：
- request.html / request-admin.html 内联脚本均须正常执行（CSP hash 正确）
- 出库单/入库单/盘点/库位/库存 五页加载无 CSP 违规（动态按钮已改事件委托，
  内联事件处理器会被 CSP 静默拦截——"查看"失效故障的根因）
- meta 中 sha256 与内联脚本实际哈希一致（双保险）
用法：先按 tests/run_pw_e2e.sh 的方式起服务（端口 8099），再
  PLAYWRIGHT_BROWSERS_PATH=... python tests/csp_check.py
"""
import base64
import hashlib
import os
import re
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 浏览器缓存目录定位（与 pw_e2e.py 相同：优先环境变量，其次仓库父目录）
_cands = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
          os.path.join(_REPO_ROOT, ".pw_browsers"),
          os.path.join(os.path.dirname(_REPO_ROOT), ".pw_browsers")]
for _c in [c for c in _cands if c]:
    try:
        if any(x.startswith("chromium-") for x in os.listdir(_c)):
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", _c)
            break
    except OSError:
        pass

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:%s" % os.environ.get("PW_E2E_PORT", "8099")
ok_all = True

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
    ctx = browser.new_context()
    page = ctx.new_page()

    def csp_check(url, name):
        global ok_all
        bad = []
        page.on("console", lambda m, b=bad: b.append(m.text)
                if m.type == "error" and "Content Security Policy" in m.text else None)
        page.goto(BASE + url, wait_until="networkidle")
        page.wait_for_timeout(300)
        ok = not bad
        ok_all &= ok
        print(f"{'PASS' if ok else 'FAIL'} | {name}: 无 CSP 违规" if ok else f"FAIL | {name}: CSP 违规 {bad}")

    # 1. 申请页（免登录）
    csp_check("/request.html", "request.html")
    # 2. 登录
    page.goto(BASE + "/", wait_until="networkidle")
    page.fill("input[name='username']", "admin")
    page.fill("input[name='password']", "Admin-Test-2026")
    page.click("button[type='submit']")
    page.wait_for_timeout(800)
    # 3. 管理页
    csp_check("/request-admin.html", "request-admin.html")
    # 3b. 五个含动态按钮的页面（历史故障：onclick= 内联处理器被 CSP 拦截）
    for u in ("/outbound.html", "/inbound.html", "/check.html", "/location.html", "/stock.html"):
        csp_check(u, u.lstrip("/"))
    # 4. meta hash 与实际内联脚本 sha256 交叉验证
    for f in ("request.html", "request-admin.html",
              "outbound.html", "inbound.html", "check.html", "location.html", "stock.html"):
        html = page.evaluate("async u => (await fetch(u)).text()", f)
        m = re.search(r"<script>(.*?)</script>", html, re.S)
        actual = base64.b64encode(hashlib.sha256(m.group(1).encode()).digest()).decode()
        meta = re.search(r"sha256-([A-Za-z0-9+/=]+)", html)
        match = bool(meta) and meta.group(1) == actual
        ok_all &= match
        print(f"{'PASS' if match else 'FAIL'} | {f}: meta hash 与内联脚本 sha256 一致")
    browser.close()

sys.exit(0 if ok_all else 1)
