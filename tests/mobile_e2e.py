"""H5 手机端端到端验证（Playwright / headless Chromium，移动视口 390x844）

覆盖 PR 审核要求的关键链路：
  A.  全部 H5 页面加载：无 CSP 违规、无 JS 异常、无 4xx/5xx 资源
  B.  匿名申请：仓库选项加载 → 搜索选货 → 提交 → 成功弹窗显示标题 + 申请编号
  C.  登录流：登录页提交 → 重定向首页 → 首页统计加载
  C2. 切换仓库：profile 页 whSwitch（真实 UI 路径）→ 快照更新
  D.  扫码回退：mediaDevices 不可用 → 手动输入弹窗（标题 + 输入框）→ 输入回填字段
  E.  入库单：guard 防重复（同步双触发只产生一次 POST + 提示）→ 创建 → 添加明细 → 提交
  F.  盘点单：创建 → 确认数量（弹窗显示标题 + 正文）→ 完成盘点（数量一致→"盘点一致"）
  G.  仓库过滤：入库单列表按 warehouse_id 过滤（不混入其它仓库的单）
  H.  鉴权兼容：sessionStorage 中的 user/token_expiry 可登录（与桌面"不记住我"对齐）

前置：按 tests/run_pw_e2e.sh 方式起服务（端口 PW_E2E_PORT，默认 8099）+ 种子数据
（种子：一号仓 W1 含 8888001/8888003 于 A1，二号仓 W2 含 8888002 于 A2）。
运行：PLAYWRIGHT_BROWSERS_PATH=... python tests/mobile_e2e.py
"""
import json
import os
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 浏览器缓存目录定位（与 csp_check.py 相同：优先环境变量，其次仓库父目录）
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

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:%s" % os.environ.get("PW_E2E_PORT", "8099")
ADMIN_USER = "admin"
ADMIN_PASS = "Admin-Test-2026"
MOBILE_PAGES = [
    "index.html", "apply.html", "inbound.html", "outbound.html", "check.html",
    "scan.html", "stock.html", "logs.html", "orders.html", "profile.html", "login.html",
]
# 种子仓库 → (条码, 库位)：每个仓自己的货物/库位，保证"当前仓库"语义下数据自洽
WH_GOODS = {"一号仓": ("8888001", "A1"), "二号仓": ("8888002", "A2")}

ok_all = True


def check(name, cond, extra=""):
    global ok_all
    ok_all = bool(cond) and ok_all
    line = ("PASS | " if cond else "FAIL | ") + name
    if extra and not cond:
        line += " — " + str(extra)[:300]
    print(line, flush=True)
    return bool(cond)


def attach_monitors(page, mon):
    page.on("console", lambda m: mon["console_errors"].append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: mon["page_errors"].append(str(e)))
    page.on("response", lambda r: mon["http_failures"].append(r.url)
            if r.status >= 400 and "/favicon.ico" not in r.url else None)


def wait_until(fn, timeout_ms=10000, poll_ms=100):
    # 注意：fn 内必须包含至少一个 Playwright 往返调用（locator/evaluate 等）。
    # sync API 下 page.url 等是缓存属性，纯属性轮询不会驱动 dispatcher 处理事件，
    # 会读到陈旧值（实测：导航完成后 20s 内 page.url 仍为旧值）。
    t0 = time.time()
    last_err = None
    while time.time() - t0 < timeout_ms / 1000.0:
        try:
            if fn():
                return True
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, str(e).split("\n")[0][:200])
        time.sleep(poll_ms / 1000.0)
    if last_err:
        print("  [wait_until 期间持续异常] %s" % last_err, flush=True)
    return False


# 页面内 guard 防重复测试：mock M.api 的 POST 为挂起 300ms 的 Promise，
# 同步连发两次同一 mutation——第一次在途（未 release）时第二次必须被 guard 拦截。
# 不用 page.route+sleep 模拟慢接口：sync API 下路由处理器的 sleep 会阻塞
# dispatcher 线程，导致第二次点击被串行化到响应之后，测不出并发窗口。
GUARD_TEST_JS = """({action, arg, reject}) => {
    return new Promise(function (resolve) {
        var calls = 0;
        var orig = window.M.api;
        window.M.api = function (method) {
            if (method === 'POST') {
                calls++;
                return new Promise(function (r, rej) {
                    setTimeout(function () {
                        if (reject) { rej(new Error('mock-reject')); }
                        else { r({ order_no: 'FAKE', id: 9999 }); }
                    }, 300);
                });
            }
            return orig.apply(window.M, arguments);
        };
        window.M_ACTIONS[action](null, arg);
        var t1 = (document.getElementById('mToast') || {}).textContent;
        window.M_ACTIONS[action](null, arg);
        var t2 = (document.getElementById('mToast') || {}).textContent;
        setTimeout(function () {
            window.M.api = orig;
            resolve(JSON.stringify({ calls: calls, toast_first: t1, toast_second: t2 }));
        }, 700);
    });
}"""


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()

    # ---------- A. 匿名页面加载（申请页 + 登录页） ----------
    for name in ("apply.html", "login.html"):
        mon = {"console_errors": [], "page_errors": [], "http_failures": []}
        attach_monitors(page, mon)
        page.goto(BASE + "/mobile/" + name, wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(300)
        check("页面加载 %s（无控制台错误/JS异常/4xx5xx）" % name,
              not mon["console_errors"] and not mon["page_errors"] and not mon["http_failures"],
              mon)

    # ---------- B. 匿名申请 ----------
    page.goto(BASE + "/mobile/apply.html", wait_until="networkidle")
    ok = wait_until(lambda: page.evaluate(
        "() => { var s = document.querySelector('#mApplyWarehouse'); if (!s) return false; "
        "for (var i = 0; i < s.options.length; i++) if (s.options[i].value) return true; "
        "return false; }"))
    check("申请页仓库选项已加载", ok)
    wh_first = page.evaluate(
        "() => { var s = document.querySelector('#mApplyWarehouse'); "
        "for (var i = 0; i < s.options.length; i++) if (s.options[i].value) return s.options[i].value; }")
    page.select_option("#mApplyWarehouse", wh_first)
    page.fill("#mApplyApplicant", "E2E申请人")
    page.fill("#mApplyContact", "e2e-mobile@test.com")
    page.fill("#mApplyDescription", "H5 端到端验证申请")
    # 搜索并添加一行货物（覆盖选货 + 库存显示路径）
    page.fill("#mApplyGoodsKw", "8888001")
    page.wait_for_timeout(800)  # 350ms 防抖 + 请求
    picked = page.locator("[data-pick-barcode='8888001']").count()
    if check("申请页货物搜索出 8888001", picked == 1):
        page.click("[data-pick-barcode='8888001']")
        page.wait_for_timeout(200)
        page.fill("#mApplyGoodsItems .m-qty-inp", "1")
    page.click("#mApplySubmitBtn")
    ok = wait_until(lambda: page.locator(".m-modal .m-modal-title").count() > 0)
    check("申请提交后弹出成功弹窗", ok)
    if ok:
        title_txt = page.locator(".m-modal .m-modal-title").text_content().strip()
        ref_txt = page.locator(".m-modal .m-ref-code").text_content().strip() if \
            page.locator(".m-modal .m-ref-code").count() else ""
        check("成功弹窗标题显示（'提交成功'）", title_txt == "提交成功", "实际: %r" % title_txt)
        check("成功弹窗显示申请编号（APP-*）", ref_txt.startswith("APP-"), "实际: %r" % ref_txt)
    # 关闭弹窗（完成），进入登录流
    page.wait_for_timeout(200)
    done_btn = page.locator(".m-modal .m-btn", has_text="完成")
    if done_btn.count():
        done_btn.click()

    # ---------- C. 登录流 ----------
    # 注意（实测陷阱）：Playwright sync API 下 page.url 是缓存属性，只有主线程发起
    # 同步 API 调用（往返）时 dispatcher 才会处理导航事件并更新它；纯属性轮询 + sleep
    # 会一直读到陈旧值。因此等待导航必须用 wait_for_url（真实 API 调用），
    # 校验 URL 用 evaluate 往返（page.evaluate("() => location.href")）。
    page.goto(BASE + "/mobile/login.html", wait_until="networkidle")
    page.fill("#mLoginUser", ADMIN_USER)
    page.fill("#mLoginPass", ADMIN_PASS)
    page.click("#mLoginBtn")
    try:
        page.wait_for_url("**/index.html", timeout=25000)
    except Exception:
        pass
    cur = page.evaluate("() => location.href")
    ok = "index.html" in cur
    if not ok:  # 诊断 + 重试一次（值仍在输入框，重新触发 submit 是幂等的）
        toast_dbg = page.locator("#mToast").text_content() if page.locator("#mToast").count() else ""
        print("DIAG[login-fail] url=%s toast=%r" % (cur, toast_dbg), flush=True)
        try:
            page.locator("#mLoginBtn").click(timeout=5000)
            page.wait_for_url("**/index.html", timeout=25000)
        except Exception:
            pass
        cur = page.evaluate("() => location.href")
        ok = "index.html" in cur
    check("登录成功并重定向首页", ok, cur)
    ok = wait_until(lambda: (lambda el: el and "加载中" not in el.inner_text())(
        page.locator("#mOverview").first.element_handle()) if page.locator("#mOverview").count() else False)
    check("首页概览统计已加载", ok)
    # 服务端核对申请单存在（管理员列表）
    try:
        req_rows = page.evaluate('() => window.M.api("GET", "/requests/").then(r => r)')
    except Exception as e:
        req_rows = "ERR: %s" % e
    ok = isinstance(req_rows, list) and any(r.get("applicant_name") == "E2E申请人" for r in req_rows)
    check("申请单已入库（管理员列表可见 E2E申请人）", ok)

    # ---------- A2. 登录后各页加载 ----------
    for name in MOBILE_PAGES:
        if name in ("apply.html", "login.html"):
            continue
        mon = {"console_errors": [], "page_errors": [], "http_failures": []}
        attach_monitors(page, mon)
        page.goto(BASE + "/mobile/" + name, wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(300)
        check("页面加载 %s（无控制台错误/JS异常/4xx5xx）" % name,
              not mon["console_errors"] and not mon["page_errors"] and not mon["http_failures"],
              mon)

    # ---------- C2. 切换当前仓库（profile 页 whSwitch，真实 UI 路径） ----------
    # 管理员初始 current_warehouse_id 为空（后端创建入库/出库单依赖它），
    # 且种子每仓的货物/库位不同——先经 profile 页切到一个确定仓库，
    # 后续扫码/入库/盘点全部发生在该仓，语义与真实操作一致。
    page.goto(BASE + "/mobile/profile.html", wait_until="networkidle")
    page.wait_for_timeout(300)
    wh_names = page.evaluate(
        "() => { var u = window.M.AUTH.getUser() || {}; var m = {}; "
        "(u.warehouses || []).forEach(function (w) { m[w.id] = w.name; }); return m; }")
    cur_id = page.evaluate("() => (window.M.AUTH.getUser() || {}).current_warehouse_id")
    # 归一化：若当前已指向"切换按钮目标"之外的仓，先切回第一个仓，保证 UI 上存在切换按钮
    sw_btns = page.locator('[data-act="whSwitch"]')
    if sw_btns.count() == 0:
        first_id = min(int(k) for k in wh_names.keys())
        page.evaluate("""(fid) => {
            var u = window.M.AUTH.getUser();
            return window.M.api('POST', '/users/' + u.id + '/switch-warehouse?warehouse_id=' + fid)
              .then(function () { u.current_warehouse_id = fid; window.M.AUTH.save(u, window.M.AUTH.getExpiry()); });
        }""", first_id)
        page.wait_for_timeout(300)
        sw_btns = page.locator('[data-act="whSwitch"]')
    check("profile 页存在仓库切换按钮", sw_btns.count() >= 1,
          "cur=%s wh=%s" % (cur_id, wh_names))
    target_wh_id = int(sw_btns.first.get_attribute("data-arg"))
    sw_btns.first.click()
    ok = wait_until(lambda: "切换成功" in (page.locator("#mToast").text_content() or ""), timeout_ms=8000)
    check("whSwitch 切换仓库成功（toast'切换成功'）", ok)
    cur_after = page.evaluate("() => (window.M.AUTH.getUser() || {}).current_warehouse_id")
    check("登录后快照 current_warehouse_id 已更新", cur_after == target_wh_id,
          "期望=%s 实际=%s" % (target_wh_id, cur_after))
    target_name = wh_names.get(str(target_wh_id)) or wh_names.get(target_wh_id) or ""
    barcode, location = WH_GOODS.get(target_name, ("8888002", "A2"))
    check("目标仓库在种子映射中（%s）" % target_name, target_name in WH_GOODS, wh_names)

    # ---------- D. 扫码回退（无相机 → 手动输入弹窗） ----------
    # 相机能力在 CI/本机不可靠（虚拟相机、权限弹窗等），这里确定性地把
    # mediaDevices 置为不可用，强制走 scanCode 的回退分支（promptCode 手动输入）——
    # 这正是审核要求验证的"扫码 fallback"路径。
    page.goto(BASE + "/mobile/scan.html", wait_until="networkidle")
    page.wait_for_timeout(300)
    page.evaluate("() => { Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true }); }")
    page.click('[data-scan="barcode"]')
    ok = wait_until(lambda: page.locator(".m-modal .m-code-inp").count() > 0, timeout_ms=12000)
    check("扫码失败后弹出手动输入弹窗（标题 + 输入框）", ok)
    if ok:
        t_txt = page.locator(".m-modal .m-modal-title").text_content().strip()
        check("扫码弹窗标题非空", len(t_txt) > 0, repr(t_txt))
        page.fill(".m-modal .m-code-inp", barcode)
        page.locator(".m-modal .m-btn", has_text="确定").click()
        check("手动输入回填到货物条码字段", page.input_value("#mScanBarcode") == barcode)
        # 库位同样走回退
        page.click('[data-scan="location"]')
        ok2 = wait_until(lambda: page.locator(".m-modal .m-code-inp").count() > 0, timeout_ms=12000)
        if check("库位扫码回退弹窗出现", ok2):
            page.fill(".m-modal .m-code-inp", location)
            page.locator(".m-modal .m-btn", has_text="确定").click()
            check("库位回填 %s" % location, page.input_value("#mScanLocation") == location)
        # 库存参考应显示数值（当前仓内该条码+库位有种子库存）。
        # 关键：占位文案"输入条码与库位后显示当前库存"本身就含子串"当前库存"，
        # 因此成功判据必须是"文本含数字"——否则占位符会误满足等待条件，
        # 与应用的异步写入形成竞态（这正是此前偶发失败的根因）。
        def _stock_has_num():
            txt = page.locator("#mScanStock").text_content() or ""
            return any(ch.isdigit() for ch in txt)
        ok3 = wait_until(_stock_has_num, timeout_ms=8000)
        if not ok3:
            # 防御性重触发：对两个输入框各派发一次 input 事件，
            # 让监听器从 DOM 值重新同步 state 并再次 refreshStock
            page.evaluate("() => { var b = document.getElementById('mScanBarcode');"
                          " var l = document.getElementById('mScanLocation');"
                          " if (b) b.dispatchEvent(new Event('input'));"
                          " if (l) l.dispatchEvent(new Event('input')); }")
            ok3 = wait_until(_stock_has_num, timeout_ms=8000)
        stock_txt = page.locator("#mScanStock").text_content() if page.locator("#mScanStock").count() else "n/a"
        if not ok3:  # 失败时留下自诊断信息
            try:
                tabs = [(pp.url, pp.is_closed()) for pp in ctx.pages]
            except Exception as e:
                tabs = "ERR %s" % e
            print("DIAG[d-stock] tabs=%r stock=%r barcode=%r location=%r console=%r pageerr=%r http=%r" % (
                tabs, stock_txt,
                page.input_value("#mScanBarcode") if page.locator("#mScanBarcode").count() else "n/a",
                page.input_value("#mScanLocation") if page.locator("#mScanLocation").count() else "n/a",
                mon["console_errors"], mon["page_errors"], mon["http_failures"]), flush=True)
        check("扫码页库存参考显示（%s@%s）" % (barcode, location), ok3, stock_txt)

    # ---------- E. 入库单（guard 防重复 + 创建/明细/提交） ----------
    page.goto(BASE + "/mobile/inbound.html", wait_until="networkidle")
    page.wait_for_timeout(300)
    page.fill("#mCreatePartner", "E2E供应商")

    # E1. guard 防重复：第一次 POST 在途时第二次必须被拦截（mock M.api，见 GUARD_TEST_JS）
    res = page.evaluate(GUARD_TEST_JS, {"action": "ordCreate", "arg": None})
    d = json.loads(res)
    check("guard：创建单双击只产生一次 POST", d["calls"] == 1, d)
    check("guard：第二次触发提示'操作处理中'", "操作处理中" in (d["toast_second"] or ""), d)

    # E2. 真实创建（单击）
    # 注意：E1 的 mock 调用完成后，其 then 分支会清空 #mCreatePartner（elPartner.value=""），
    # 必须先重新填写，否则 ordCreate 会以"请填写供应商"拒绝。
    page.fill("#mCreatePartner", "E2E供应商")
    page.locator("[data-act='ordCreate']").click()
    ok = wait_until(lambda: page.locator(".m-item", has_text="E2E供应商").count() > 0, timeout_ms=10000)
    check("入库单列表出现新建草稿", ok)
    order_no_mine = None
    if ok:
        order_no_mine = page.locator(".m-item", has_text="E2E供应商").first.locator(".m-item-title").text_content().strip()
        page.locator(".m-item", has_text="E2E供应商").locator("[data-act='ordOpen']").first.click()
        ok = wait_until(lambda: page.locator("#mItBarcode").count() > 0)
        check("草稿详情打开（添加明细表单可见）", ok)
        if ok:
            page.fill("#mItBarcode", barcode)
            page.fill("#mItLocation", location)
            page.fill("#mItQty", "5")
            page.fill("#mItPrice", "10")
            # E3. guard 防重复（明细）：arg 用页面按钮上真实的单据 id
            add_arg = page.evaluate("() => { var b = document.querySelector(\"[data-act='ordAddItem']\"); "
                                    "return b ? b.getAttribute('data-arg') : null; }")
            # reject：走 catch(release) 分支，避免 then 里 openDetail(假id) 把详情页冲掉
            res2 = page.evaluate(GUARD_TEST_JS, {"action": "ordAddItem", "arg": add_arg, "reject": True})
            d2 = json.loads(res2)
            check("guard：添加明细双击只产生一次 POST", d2["calls"] == 1, d2)
            check("guard：添加明细第二次触发提示'操作处理中'", "操作处理中" in (d2["toast_second"] or ""), d2)
            # E4. 真实添加明细
            page.locator("[data-act='ordAddItem']").click()
            ok = wait_until(lambda: page.locator("#mItList .m-item").count() > 0, timeout_ms=8000)
            check("明细列表出现新增行（%s@%s）" % (barcode, location), ok)
            # E5. 提交
            page.locator("[data-act='ordSubmit']").first.click()
            ok = wait_until(lambda: page.locator(".m-badge-done").count() > 0, timeout_ms=8000)
            check("提交后单据状态为已完成", ok)

    # ---------- G. 仓库过滤（列表按 warehouse_id 过滤，不混入其它仓的单） ----------
    def api_get(path):
        try:
            return page.evaluate("(p) => window.M.api('GET', p).then(r => r)", path)
        except Exception as e:  # 页面内 reject → 记为失败而非崩溃
            return "ERR: %s" % e

    wh_id = page.evaluate('() => (window.M.AUTH.currentWarehouse() || {}).id')
    rows_mine = api_get("/inbound-orders/?warehouse_id=%s" % wh_id)
    ok = (order_no_mine is not None  # E2 必须真实建成了单据（防空断言）
          and isinstance(rows_mine, list) and len(rows_mine) >= 1
          and all(r["warehouse_id"] == wh_id for r in rows_mine)
          and any(r["order_no"] == order_no_mine for r in rows_mine))
    check("入库单列表按当前仓库过滤（含本测试新建单，且全部属于该仓）", ok, rows_mine)
    other_wid = min(int(k) for k in wh_names.keys())
    if other_wid == wh_id:
        other_wid = max(int(k) for k in wh_names.keys())
    rows_other = api_get("/inbound-orders/?warehouse_id=%s" % other_wid)
    ok = (isinstance(rows_other, list)
          and all(r["warehouse_id"] == other_wid for r in rows_other)
          and not any(r["order_no"] == order_no_mine for r in rows_other))
    check("入库单列表 warehouse_id 过滤生效（其它仓列表不混入本仓新建单）", ok, rows_other)

    # ---------- F. 盘点单（当前仓：盘点数量 = 当前实际库存 → 确定性"盘点一致"） ----------
    # 注意：.pwtest.db 跨运行持久化（历史出入库/盘点完成都会改库存），不能硬编码种子值；
    # 这里先读当前实际库存，再按该值盘点 → diff 必为 0。
    cur_stock = page.evaluate("""(a) => window.M.api('GET',
        '/stock/?warehouse_id=' + a.wid + '&goods_barcode=' + a.b)
        .then(function (rows) {
            var m = (rows || []).filter(function (s) {
                return s.goods_barcode === a.b && s.location_code === a.l;
            })[0];
            return m ? Number(m.quantity) : 0;
        })""", {"wid": wh_id, "b": barcode, "l": location})
    _cs = float(cur_stock) if cur_stock is not None else 0.0
    count_qty = _cs if _cs > 0 else 1
    expect_match = _cs > 0
    print("DIAG[f] %s@%s 当前库存=%s 盘点数量=%s 期望一致=%s"
          % (barcode, location, cur_stock, count_qty, expect_match), flush=True)
    page.goto(BASE + "/mobile/check.html", wait_until="networkidle")
    page.wait_for_timeout(300)
    page.locator("[data-act='chkCreate']").click()
    ok = wait_until(lambda: page.locator("#mChkBarcode").count() > 0)
    check("盘点单创建并自动打开详情", ok)
    if ok:
        page.fill("#mChkBarcode", barcode)
        page.fill("#mChkLocation", location)
        page.fill("#mChkQty", str(count_qty))
        page.locator("[data-act='chkAddItem']").click()
        ok = wait_until(lambda: page.locator(".m-modal .m-modal-title").count() > 0)
        check("盘点确认弹窗出现", ok)
        if ok:
            t_txt = page.locator(".m-modal .m-modal-title").text_content().strip()
            b_txt = page.locator(".m-modal .m-modal-body").text_content().strip()
            if expect_match:
                check("盘点弹窗标题显示（数量一致→'盘点一致'）", "盘点一致" in t_txt, repr(t_txt))
            else:
                check("盘点弹窗标题显示（无库存→'盘点差异'）", "盘点差异" in t_txt, repr(t_txt))
            check("盘点弹窗正文显示（是否继续盘点下一项）", "是否继续" in b_txt, repr(b_txt))
            page.locator(".m-modal .m-btn", has_text="继续").click()
            page.wait_for_timeout(200)
        page.locator("[data-act='chkComplete']").click()
        ok = wait_until(lambda: page.locator("#mCheckDetail .m-badge-done").count() > 0, timeout_ms=8000)
        check("完成盘点后状态为已完成", ok)

    # ---------- H. sessionStorage 鉴权兼容 ----------
    # 桌面端"不记住我"把 user/token_expiry 写入 sessionStorage；H5 必须同样认可
    page2 = ctx.new_page()
    page2.goto(BASE + "/mobile/apply.html", wait_until="networkidle")
    page2.evaluate("""() => {
        sessionStorage.setItem("user", localStorage.getItem("user"));
        sessionStorage.setItem("token_expiry", localStorage.getItem("token_expiry"));
        localStorage.removeItem("user");
        localStorage.removeItem("token_expiry");
    }""")
    mon2 = {"console_errors": [], "page_errors": [], "http_failures": []}
    attach_monitors(page2, mon2)
    page2.goto(BASE + "/mobile/stock.html", wait_until="networkidle")
    # 等待库存页真正渲染（locator/evaluate 均为往返，驱动 dispatcher 处理导航事件）
    ok = wait_until(lambda: page2.locator("#mStockList").count() > 0
                    and "stock.html" in page2.evaluate("() => location.href"))
    check("仅 sessionStorage 有凭据时 H5 认可登录（不跳登录页）", ok,
          page2.evaluate("() => location.href"))
    # 反向：两处都清空 → 应跳登录页
    page3 = ctx.new_page()
    page3.goto(BASE + "/mobile/apply.html", wait_until="networkidle")
    page3.evaluate("""() => {
        sessionStorage.removeItem("user"); sessionStorage.removeItem("token_expiry");
        localStorage.removeItem("user"); localStorage.removeItem("token_expiry");
    }""")
    page3.goto(BASE + "/mobile/stock.html", wait_until="domcontentloaded")
    try:
        page3.wait_for_url("**/login.html", timeout=8000)
        ok3 = True
    except Exception:
        ok3 = "login.html" in page3.evaluate("() => location.href")
    check("无任何本地凭据时跳登录页", ok3, page3.evaluate("() => location.href"))

    browser.close()

print()
print("H5 E2E 结果: %s" % ("全部通过" if ok_all else "存在失败"))
sys.exit(0 if ok_all else 1)
