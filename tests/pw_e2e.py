#!/usr/bin/env python3
"""Playwright 端到端验证（Chromium）：
1. request.html 申请提交：仓库下拉（必选，v2）→ 添加行 → 行内搜索备件 → 候选/选中行显示可用库存 → 超库存软提示 → 提交
1b. 多行异步搜索竞态（乱序响应不错行）/ 重渲染保留搜索词 / 精确聚焦到该行
2. index.html 登录 → request-admin.html 申请管理：点击申请单 → 详情弹窗（表头信息 + 带表头的货物明细表）
   → 弹窗内通过处理（v2 通过即扣库存，确认文案说明扣减与不可撤销）
3b. v2 通过即扣库存：扣减金额核对 / 状态机（重复通过/驳回拦截）/ 库存不足拦截 / 驳回不动库存 / 无明细仅留痕
4. 归档 Tab 点击归档单 → 只读详情（含申请仓库）
"""
import os
import sys
import tempfile
import time


def _writable_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".w_probe")
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 浏览器缓存目录：优先环境变量，其次仓库内（CI），再次仓库父目录（本机沙箱布局），最后系统临时目录
_browser_cands = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
                  os.path.join(_REPO_ROOT, ".pw_browsers"),
                  os.path.join(os.path.dirname(_REPO_ROOT), ".pw_browsers"),
                  os.path.join(tempfile.gettempdir(), "pw_e2e_browsers")]
_browser_cands = [c for c in _browser_cands if c]

def _has_browser(p):
    try:
        return any(x.startswith("chromium-") for x in os.listdir(p))
    except OSError:
        return False

_pw_browsers = next((c for c in _browser_cands if _has_browser(c)), None) \
    or next((c for c in _browser_cands if _writable_dir(c)), None)
if _pw_browsers:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _pw_browsers

# HOME 需要可写（某些沙箱内默认 HOME 只读）；可写则保持原值
if not _writable_dir(os.environ.get("HOME") or "/nonexistent"):
    _home_cands = [os.environ.get("PW_E2E_HOME"),
                   os.path.join(_REPO_ROOT, ".pw_home"),
                   os.path.join(tempfile.gettempdir(), "pw_e2e_home")]
    _home_cands = [c for c in _home_cands if c]
    _pw_home = next((c for c in _home_cands if _writable_dir(c)), None)
    if not _pw_home:
        raise SystemExit("找不到可写的 HOME/PW_E2E_HOME 目录")
    os.environ["HOME"] = _pw_home

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:%s" % os.environ.get("PW_E2E_PORT", "8099")
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL") + " | " + name + ((" | " + str(detail)) if (detail and not cond) else ""))

def wait_js(page, cond_arrow_js, timeout_ms=5000):
    """CSP 安全等待（页面 script-src 无 'unsafe-eval'）：
    Playwright wait_for_function 的轮询会落到页面 RAF/setTimeout 任务上下文，
    其中 globalThis.eval 被 CSP 拦截（实测：同一 eval 在 CDP 同步上下文可用、
    异步上下文被拒）。因此改用逐轮独立 evaluate（每轮都是 CDP 同步上下文）轮询。
    cond_arrow_js 必须是箭头函数表达式字符串，返回 truthy 即视为满足。"""
    deadline = time.time() + timeout_ms / 1000.0
    while True:
        if page.evaluate(cond_arrow_js):
            return
        if time.time() >= deadline:
            raise TimeoutError("wait_js 超时: " + cond_arrow_js)
        page.wait_for_timeout(100)

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
    # 浏览器（申请人视角）使用独立 X-Real-IP 分桶（8.8.8.210），与种子脚本（127.0.0.1）、
    # §3b API 侧（8.8.8.201/202）互不干扰——测试即模拟多个不同客户端 IP，
    # 避免同一限流桶（搜索 30 次/分钟）被测试自身撞满导致假 429
    ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                              extra_http_headers={"X-Real-IP": "8.8.8.210"})
    page = ctx.new_page()
    page.set_default_timeout(15000)
    dialogs = []
    page.on("dialog", lambda d: (d.accept(), dialogs.append(d.message)))

    # ================= 1. 申请提交页 =================
    page.goto(BASE + "/request.html")
    page.wait_for_selector("#addGoodsRowBtn")
    check("提交页：可见『添加货物行』按钮", page.is_visible("#addGoodsRowBtn"))
    check("提交页：初始无货物行", page.query_selector_all("#goodsTableBody tr") == [])
    # 申请仓库下拉（v2 通过即扣库存：提交时必选仓库）——双仓环境（评审 P1：展示仓库=扣减仓库）
    page.wait_for_selector("#warehouseId option:not([value=''])", timeout=5000, state="attached")
    check("提交页：申请仓库下拉已加载并默认选中",
          page.locator("#warehouseId option").count() >= 1 and page.locator("#warehouseId").input_value() != "",
          page.locator("#warehouseId").inner_text())
    check("提交页：仓库下拉含两个仓（一号仓/二号仓），默认一号仓",
          page.locator("#warehouseId option").count() == 2
          and "一号仓" in page.locator("#warehouseId option").nth(0).inner_text()
          and "二号仓" in page.locator("#warehouseId option").nth(1).inner_text(),
          page.locator("#warehouseId").inner_text())

    # ---------- 仓库切换联动演练（评审 P1：库存展示必须与审批扣减仓库一致） ----------
    # 密封圈：一号仓 0、二号仓 100 → 切换仓库后候选必须作废、按新仓库重搜，
    # 已选行的库存快照必须按新仓库刷新并重算超库存软提示。
    page.click("#addGoodsRowBtn")
    page.wait_for_selector("#goodsTableBody tr")
    d_in = page.locator("#goodsTableBody tr:nth-child(1) input[type=text]")
    d_in.fill("密封圈")
    page.locator("#goodsTableBody tr:nth-child(1) button[title=搜索备件]").click()
    page.wait_for_selector("#goodsSearchResults:not(.hidden)")
    d1 = page.locator("#goodsSearchResults li button").first
    check("仓库联动：密封圈@一号仓候选显示『可用库存 0（无库存）』",
          "可用库存 0" in d1.inner_text() and "无库存" in d1.inner_text(), d1.inner_text())
    # 切到二号仓 → 在途/残留候选必须被清空（旧仓库数据不得残留）
    page.locator("#warehouseId").select_option(index=1)
    page.wait_for_timeout(300)
    check("仓库联动：切换仓库后候选面板被清空（旧仓候选作废）",
          page.locator("#goodsSearchResults").evaluate("el => el.classList.contains('hidden')"),
          page.locator("#goodsSearchResults").inner_text())
    # 按新仓库重新搜索 → 二号仓有 100
    page.locator("#goodsTableBody tr:nth-child(1) button[title=搜索备件]").click()
    page.wait_for_selector("#goodsSearchResults:not(.hidden)")
    d2 = page.locator("#goodsSearchResults li button").first
    check("仓库联动：密封圈@二号仓候选显示『可用库存 100 件』", "可用库存 100" in d2.inner_text(), d2.inner_text())
    d2.click()
    check("仓库联动：选中行库存列显示二号仓 100 件",
          "100" in page.locator("#goodsTableBody tr:nth-child(1) td:nth-child(3)").inner_text(),
          page.locator("#goodsTableBody tr:nth-child(1) td:nth-child(3)").inner_text())
    page.locator("#goodsTableBody tr:nth-child(1) input[type=number]").fill("2")
    page.wait_for_timeout(200)
    check("仓库联动：数量 2 ≤ 二号仓库存 100 → 无超库存提示",
          page.locator("#goodsTableBody .qty-warn:not(.hidden)").count() == 0)
    # 切回一号仓 → 已选行库存快照必须按一号仓刷新为 0，且 2 > 0 触发软提示
    page.locator("#warehouseId").select_option(index=0)
    page.wait_for_selector("#goodsTableBody .qty-warn:not(.hidden)", timeout=3000)
    check("仓库联动：切回一号仓后已选行库存列刷新为 0（无库存）",
          "无库存" in page.locator("#goodsTableBody tr:nth-child(1) td:nth-child(3)").inner_text(),
          page.locator("#goodsTableBody tr:nth-child(1) td:nth-child(3)").inner_text())
    check("仓库联动：数量 2 超过一号仓库存 0 → 超库存软提示重新出现",
          "超过可用库存" in page.locator("#goodsTableBody tr:nth-child(1)").inner_text())
    # 再切回二号仓 → 库存恢复 100、软提示消除（证明快照随仓库双向刷新）
    page.locator("#warehouseId").select_option(index=1)
    wait_js(page,
            "Array.from(document.querySelectorAll('#goodsTableBody tr')).some(tr => tr.innerText.includes('100') "
            "&& !tr.querySelector('.qty-warn:not(.hidden)'))")
    check("仓库联动：再切回二号仓 → 库存列恢复 100 件且软提示消除",
          "100" in page.locator("#goodsTableBody tr:nth-child(1) td:nth-child(3)").inner_text()
          and page.locator("#goodsTableBody .qty-warn:not(.hidden)").count() == 0,
          page.locator("#goodsTableBody tr:nth-child(1) td:nth-child(3)").inner_text())
    # 移除演练行，恢复干净状态后继续原流程
    page.locator("#goodsTableBody tr:nth-child(1) button[title=移除该行]").click()
    check("仓库联动：演练行已移除", page.query_selector_all("#goodsTableBody tr") == [])
    # 演练结束于二号仓——把仓库选择器复位为默认一号仓，后续主流程按 W1 语义断言
    # （此时无已选行，切换不触发库存刷新请求）
    page.locator("#warehouseId").select_option(index=0)
    page.wait_for_timeout(200)

    # ---------- 批量刷新演练（评审 P2 跟进：切仓 N 行 = 1 次 stock-lookup；失败标记"库存未知"） ----------
    # 轴承 8888001：W1=12 / W2=0；密封圈 8888002：W1=0 / W2=100（种子刻意错位）
    page.click("#addGoodsRowBtn")
    page.wait_for_selector("#goodsTableBody tr")
    page.locator("#goodsTableBody tr:nth-child(1) input[type=text]").fill("轴承")
    page.locator("#goodsTableBody tr:nth-child(1) button[title=搜索备件]").click()
    page.wait_for_selector("#goodsSearchResults:not(.hidden)")
    page.locator("#goodsSearchResults li button").first.click()
    page.click("#addGoodsRowBtn")
    page.locator("#goodsTableBody tr:nth-child(2) input[type=text]").fill("密封圈")
    page.locator("#goodsTableBody tr:nth-child(2) button[title=搜索备件]").click()
    page.wait_for_selector("#goodsSearchResults:not(.hidden)")
    page.locator("#goodsSearchResults li button").first.click()
    page.locator("#goodsTableBody tr:nth-child(1) input[type=number]").fill("2")
    def row_stock(idx):
        return page.locator("#goodsTableBody tr:nth-child(%d) td:nth-child(3)" % idx).inner_text()
    check("批量演练：前置 轴承@W1=12、密封圈@W1=0（无库存）",
          "12" in row_stock(1) and "无库存" in row_stock(2), (row_stock(1), row_stock(2)))
    reqs_seen = []
    req_cap = page.on("request", lambda r: reqs_seen.append(r.url))
    # 切二号仓 → 两行同时刷新（轴承 12→无库存、密封圈 0→100），且只发 1 次批量调用
    page.locator("#warehouseId").select_option(index=1)
    wait_js(page, "document.querySelectorAll('#goodsTableBody tr')[0].innerText.includes('无库存') "
                  "&& document.querySelectorAll('#goodsTableBody tr')[1].innerText.includes('100')")
    _lk_n = sum(1 for u in reqs_seen if "stock-lookup" in u)
    _gs_n = sum(1 for u in reqs_seen if "goods-search" in u)
    check("批量演练：切二号仓后两行库存同时刷新（轴承无库存 / 密封圈 100）",
          "无库存" in row_stock(1) and "100" in row_stock(2), (row_stock(1), row_stock(2)))
    check("批量演练：2 行切仓仅 1 次 stock-lookup（不再逐行 goods-search）",
          _lk_n == 1 and _gs_n == 0, "stock-lookup=%d goods-search=%d" % (_lk_n, _gs_n))
    reqs_seen.clear()
    # 切回一号仓 → 轴承 12、密封圈 无库存（0）
    page.locator("#warehouseId").select_option(index=0)
    wait_js(page, "document.querySelectorAll('#goodsTableBody tr')[1].innerText.includes('无库存') "
                  "&& document.querySelectorAll('#goodsTableBody tr')[0].innerText.includes('12')")
    check("批量演练：切回一号仓两行同时刷新（轴承 12 / 密封圈无库存）",
          "12" in row_stock(1) and "无库存" in row_stock(2), (row_stock(1), row_stock(2)))
    check("批量演练：反向切仓同样仅 1 次 stock-lookup",
          sum(1 for u in reqs_seen if "stock-lookup" in u) == 1, "seen=%r" % reqs_seen)
    reqs_seen.clear()
    # 失败路径：拦截 stock-lookup 返回 429 → 已选行必须标记"库存未知"，不得残留旧仓数字
    def _fail429(route):
        route.fulfill(status=429, content_type="application/json",
                      body='{"detail": "搜索过于频繁，请稍后再试"}')
    page.route("**/stock-lookup*", _fail429)
    page.locator("#warehouseId").select_option(index=1)
    wait_js(page, "document.querySelectorAll('#goodsTableBody tr')[0].innerText.includes('库存未知') "
                  "&& document.querySelectorAll('#goodsTableBody tr')[1].innerText.includes('库存未知')")
    check("批量演练：刷新失败(429) → 两行均标记『库存未知』（不残留旧仓数字 12）",
          "库存未知" in row_stock(1) and "库存未知" in row_stock(2) and "12" not in row_stock(1),
          (row_stock(1), row_stock(2)))
    _tr1_txt = page.locator("#goodsTableBody tr:nth-child(1)").inner_text()
    check("批量演练：库存未知时数量提示为『库存未知』而非误报『超过可用库存』",
          "库存未知" in _tr1_txt and "超过可用库存" not in _tr1_txt, _tr1_txt)
    page.unroute("**/stock-lookup*", _fail429)
    # 恢复：切回一号仓 → 批量成功 → "未知"标记清除、库存数字恢复
    page.locator("#warehouseId").select_option(index=0)
    wait_js(page, "document.querySelectorAll('#goodsTableBody tr')[1].innerText.includes('无库存') "
                  "&& document.querySelectorAll('#goodsTableBody tr')[0].innerText.includes('12')")
    check("批量演练：恢复后两行库存刷新成功（轴承 12 / 密封圈无库存），『库存未知』清除",
          "12" in row_stock(1) and "无库存" in row_stock(2)
          and "库存未知" not in row_stock(1) and "库存未知" not in row_stock(2),
          (row_stock(1), row_stock(2)))
    # 注：request 监听器保留至进程退出（仅向 reqs_seen 追加 URL，无副作用；
    # Playwright sync API 的 remove_listener 包装器对象不一致会抛 KeyError）
    # 移除演练行，恢复干净状态
    while page.query_selector_all("#goodsTableBody tr"):
        page.locator("#goodsTableBody tr:nth-child(1) button[title=移除该行]").click()
        page.wait_for_timeout(150)
    check("批量演练：演练行已移除（干净状态）", page.query_selector_all("#goodsTableBody tr") == [])

    # 添加第一行
    page.click("#addGoodsRowBtn")
    page.wait_for_selector("#goodsTableBody tr")
    row1_input = page.locator("#goodsTableBody tr:nth-child(1) input[type=text]")
    check("提交页：新行含行内搜索框", row1_input.count() == 1)
    ths = [t.inner_text().replace("*", "").strip() for t in page.locator("table:has(tbody#goodsTableBody) thead th").all()]
    check("提交页：表格有 5 列表头（# / 备件 / 可用库存 / 数量 / 操作）",
          ths == ["#", "备件", "可用库存", "数量", ""], ths)

    # 行内搜索：轴承
    row1_input.fill("轴承")
    page.locator("#goodsTableBody tr:nth-child(1) button[title=搜索备件]").click()
    page.wait_for_selector("#goodsSearchResults:not(.hidden)")
    first_result = page.locator("#goodsSearchResults li button").first
    check("提交页：搜索结果出现且含可用库存",
          "测试轴承" in first_result.inner_text() and "可用库存 12 个" in first_result.inner_text(),
          first_result.inner_text())

    # 选择备件
    first_result.click()
    row1 = page.locator("#goodsTableBody tr:nth-child(1)")
    check("提交页：选中后行内显示备件名", "测试轴承" in row1.inner_text())
    check("提交页：选中后『可用库存』列显示 12 个", "12 个" in row1.locator("td:nth-child(3)").inner_text(),
          row1.locator("td:nth-child(3)").inner_text())

    # 数量 99 → 超库存软提示
    qty1 = row1.locator("input[type=number]")
    qty1.fill("99")
    page.wait_for_selector("#goodsTableBody .qty-warn:not(.hidden)", timeout=3000)
    check("提交页：数量超可用库存 → 出现软提示", "超过可用库存" in row1.inner_text())
    qty1.fill("3")

    # 添加第二行：密封圈（无库存）
    page.click("#addGoodsRowBtn")
    row2 = page.locator("#goodsTableBody tr:nth-child(2)")
    check("提交页：可继续添加第二行", row2.count() == 1)
    row2_input = row2.locator("input[type=text]")
    row2_input.fill("密封圈")
    row2.locator("button[title=搜索备件]").click()
    page.wait_for_selector("#goodsSearchResults:not(.hidden)")
    r2 = page.locator("#goodsSearchResults li button").first
    check("提交页：无库存备件的候选显示『可用库存 0』", "可用库存 0" in r2.inner_text(), r2.inner_text())
    r2.click()
    row2 = page.locator("#goodsTableBody tr:nth-child(2)")
    check("提交页：第二行选中且库存列显示 0（无库存）", "无库存" in row2.locator("td:nth-child(3)").inner_text(),
          row2.locator("td:nth-child(3)").inner_text())
    row2.locator("input[type=number]").fill("2")

    # 填写表单并提交
    page.fill("#applicantName", "浏览器申请人")
    page.fill("#department", "测试部")
    page.fill("#contact", "pw@test.com")
    page.fill("#description", "Playwright 端到端：两行备件")
    page.click("#submitBtn")
    page.wait_for_selector("#successPanel:not(.hidden)")
    ref = page.locator("#refValue").inner_text()
    check("提交页：提交成功并显示申请编号", ref.startswith("APP-"), ref)

    # 未选备件的行不参与提交（添加一行不选，再提交，仍成功）
    page.click("#resetBtn")
    page.click("#addGoodsRowBtn")
    rowx = page.locator("#goodsTableBody tr:nth-child(1) input[type=text]")
    rowx.fill("轴承")
    page.locator("#goodsTableBody tr:nth-child(1) button[title=搜索备件]").click()
    page.wait_for_selector("#goodsSearchResults:not(.hidden)")
    page.locator("#goodsSearchResults li button").first.click()
    page.locator("#goodsTableBody tr:nth-child(1) input[type=number]").fill("4")
    # 第二行保持未选择
    page.click("#addGoodsRowBtn")
    page.fill("#applicantName", "浏览器申请人B")
    page.fill("#contact", "pw2@test.com")
    page.fill("#description", "一行已选 + 一行未选")
    page.click("#submitBtn")
    page.wait_for_selector("#successPanel:not(.hidden)")
    check("提交页：未选择备件的行不阻断提交（只提交已选行）", True)
    ref2 = page.locator("#refValue").inner_text()
    check("提交页：第二张申请编号不同", ref2 != ref, ref2)
    page.click("#resetBtn")

    # ================= 1b. 审核修复验证：多行异步竞态 / 输入保留 / 精确聚焦 =================
    # 注：resetBtn 位于 successPanel 内（提交成功后才可见），干净状态一律用整页重载获取
    def fresh_page():
        page.goto(BASE + "/request.html")
        page.wait_for_selector("#addGoodsRowBtn")

    # P1：两行搜索交错 → 延迟的过期结果不得被选入错误的行
    fresh_page()
    # 在页面内包装 fetch：把行1（8888001 轴承）的响应人为延迟 1.5s（浏览器端延迟，
    # 不阻塞 Playwright 分发线程，从而得到真实乱序）；真实数据仍由后端返回
    page.evaluate("""
        () => {
            const orig = window.fetch;
            window.fetch = function (url, opts) {
                const u = String(url);
                if (u.indexOf('q=8888001') !== -1) {
                    return new Promise((resolve, reject) =>
                        setTimeout(() => Promise.resolve(orig.call(window, u, opts)).then(resolve, reject), 1500));
                }
                return orig.call(window, u, opts);
            };
        }
    """)

    # 行1 搜索（响应延迟 1.5s）→ 行2 搜索（即时响应）→ 行2 的结果先到
    page.click("#addGoodsRowBtn")
    page.locator("#goodsTableBody tr:nth-child(1) input[type=text]").fill("8888001")
    page.locator("#goodsTableBody tr:nth-child(1) button[title=搜索备件]").click()
    page.click("#addGoodsRowBtn")
    page.locator("#goodsTableBody tr:nth-child(2) input[type=text]").fill("8888002")
    page.locator("#goodsTableBody tr:nth-child(2) button[title=搜索备件]").click()
    page.wait_for_selector("#goodsSearchResults:not(.hidden)")
    first = page.locator("#goodsSearchResults li button").first
    check("竞态：行2（快响应）的结果先展示在候选面板", "测试密封圈" in first.inner_text(), first.inner_text())

    page.wait_for_timeout(2000)  # 行1 的延迟响应此时已返回
    first = page.locator("#goodsSearchResults li button").first
    check("竞态：行1 的迟到过期结果未覆盖当前面板",
          "测试密封圈" in first.inner_text() and "测试轴承" not in first.inner_text(), first.inner_text())

    first.click()
    row1 = page.locator("#goodsTableBody tr:nth-child(1)")
    row2 = page.locator("#goodsTableBody tr:nth-child(2)")
    check("竞态：候选正确归属行2", "测试密封圈" in row2.inner_text(), row2.inner_text())
    check("竞态：行1 未被错误选中", row1.locator("input[type=text]").count() == 1, row1.inner_text())
    check("竞态：行1 的搜索词在重渲染后保留",
          row1.locator("input[type=text]").input_value() == "8888001",
          row1.locator("input[type=text]").input_value())

    # 行1 重新搜索 → 结果归属行1
    page.locator("#goodsTableBody tr:nth-child(1) button[title=搜索备件]").click()
    page.wait_for_selector("#goodsSearchResults:not(.hidden)")
    page.locator("#goodsSearchResults li button").first.click()
    row1 = page.locator("#goodsTableBody tr:nth-child(1)")
    check("竞态：行1 重新搜索后结果归属行1", "测试轴承" in row1.inner_text(), row1.inner_text())
    check("竞态：两行各自选中正确备件",
          "测试轴承" in row1.inner_text() and "测试密封圈" in row2.inner_text())
    fresh_page()

    # P2：重新渲染（添加/删除其他行）不丢失未完成行的搜索词
    page.click("#addGoodsRowBtn")
    page.locator("#goodsTableBody tr:nth-child(1) input[type=text]").fill("轴承")
    page.click("#addGoodsRowBtn")
    check("输入保留：添加新行（触发重渲染）后前一行搜索词仍在",
          page.locator("#goodsTableBody tr:nth-child(1) input[type=text]").input_value() == "轴承")
    page.locator("#goodsTableBody tr:nth-child(1) button[title=搜索备件]").click()
    page.wait_for_selector("#goodsSearchResults:not(.hidden)")
    page.click("#addGoodsRowBtn")
    check("输入保留：结果展示中再添加行，行1 搜索词仍在",
          page.locator("#goodsTableBody tr:nth-child(1) input[type=text]").input_value() == "轴承")
    page.locator("#goodsTableBody tr:nth-child(3) button[title=移除该行]").click()
    check("输入保留：删除其他行后行1 搜索词仍在",
          page.locator("#goodsTableBody tr:nth-child(1) input[type=text]").input_value() == "轴承")
    fresh_page()

    # 聚焦：添加行 / 选中 / 更换 都聚焦到「该行」而非最后一行
    def focused_row_id():
        return page.evaluate(
            "() => { const a = document.activeElement;"
            " const tr = a && a.closest ? a.closest('tr[data-row-id]') : null;"
            " return tr ? tr.dataset.rowId : null; }")

    page.click("#addGoodsRowBtn")
    new_row_id = page.evaluate("() => document.querySelector('#goodsTableBody tr').dataset.rowId")
    check("聚焦：添加行后焦点落在该行输入框", focused_row_id() == new_row_id, str(focused_row_id()))

    page.locator("#goodsTableBody tr:nth-child(1) input[type=text]").fill("轴承")
    page.locator("#goodsTableBody tr:nth-child(1) button[title=搜索备件]").click()
    page.wait_for_selector("#goodsSearchResults:not(.hidden)")
    page.locator("#goodsSearchResults li button").first.click()
    check("聚焦：选中后焦点落在该行数量框",
          focused_row_id() == page.evaluate("() => document.querySelector('#goodsTableBody tr').dataset.rowId"),
          str(focused_row_id()))

    page.click("#addGoodsRowBtn")
    page.locator("#goodsTableBody tr:nth-child(2) input[type=text]").fill("密封圈")
    page.locator("#goodsTableBody tr:nth-child(2) button[title=搜索备件]").click()
    page.wait_for_selector("#goodsSearchResults:not(.hidden)")
    page.locator("#goodsSearchResults li button").first.click()
    row1_id = page.evaluate("() => document.querySelectorAll('#goodsTableBody tr')[0].dataset.rowId")
    page.locator("#goodsTableBody tr:nth-child(1) button:has-text(\"更换\")").click()
    check("聚焦：『更换』后焦点落回该行（行1）搜索框而非最后一行", focused_row_id() == row1_id, str(focused_row_id()))
    fresh_page()

    # ================= 2. 登录 =================
    page.goto(BASE + "/index.html")
    page.fill("#username", "admin")
    page.fill("#password", "Admin-Test-2026")
    page.click("#loginForm button[type=submit], #loginForm [type=submit]")
    page.wait_for_url("**/dashboard*.html", timeout=15000)
    check("登录：跳转仪表盘", "dashboard" in page.url)

    # ================= 3. 申请管理：近期列表 + 详情弹窗 =================
    page.goto(BASE + "/request-admin.html")
    page.wait_for_selector("#recentBody tr")
    rows = page.locator("#recentBody tr")
    check("管理页：近期列表渲染出行", rows.count() >= 2)

    # 找第一张种子申请单（编号 -0001：两行备件）
    target = None
    for i in range(1, rows.count() + 1):
        tr = page.locator(f"#recentBody tr:nth-child({i})")
        if "-0001" in tr.inner_text():
            target = tr
            break
    check("管理页：列表含第一张种子申请单（-0001）", target is not None)
    if target:
        check("管理页：列表行显示申请仓库（一号仓）", "一号仓" in target.inner_text(), target.inner_text())
        refno = [t for t in target.inner_text().split() if t.startswith("APP-")][0]
        # 行内点击打开详情
        target.click()
        page.wait_for_selector("#detailModal:not(.hidden)")
        check("详情弹窗：打开成功", page.is_visible("#detailModal"))
        check("详情弹窗：显示申请编号", refno in page.locator("#detailRef").inner_text())
        check("详情弹窗：显示申请人/邮箱/状态",
              "浏览器测试" in page.locator("#detailInfo").inner_text()
              and "bw@test.com" in page.locator("#detailInfo").inner_text()
              and "待处理" in page.locator("#detailStatus").inner_text(),
              page.locator("#detailInfo").inner_text())
        check("详情弹窗：显示申请仓库（一号仓）",
              "申请仓库" in page.locator("#detailInfo").inner_text()
              and "一号仓" in page.locator("#detailInfo").inner_text(),
              page.locator("#detailInfo").inner_text())
        check("详情弹窗：事由描述完整显示", "Playwright 端到端验证" in page.locator("#detailDescription").inner_text(),
              page.locator("#detailDescription").inner_text())
        # 货物明细表：表头 + 两行
        thead = [t.inner_text().strip() for t in page.locator("table:has(tbody#detailItemsBody) thead th").all()]
        check("详情弹窗：明细表带表头（序号/货物名称/条码/规格型号/单位/数量）",
              all(h in thead for h in ["序号", "货物名称", "条码", "规格型号", "单位", "数量"]), thead)
        items = page.locator("#detailItemsBody tr")
        check("详情弹窗：明细 2 行", items.count() == 2, items.count())
        check("详情弹窗：明细含 测试轴承 x3 与 测试垫片 x2",
              "测试轴承" in items.nth(0).inner_text() and "3 个" in items.nth(0).inner_text()
              and "测试垫片" in items.nth(1).inner_text() and "2 片" in items.nth(1).inner_text(),
              items.nth(0).inner_text() + " || " + items.nth(1).inner_text())
        check("详情弹窗：待处理单显示通过/驳回按钮",
              page.is_visible("#detailApproveBtn") and page.is_visible("#detailRejectBtn"))

        # P2 回归：编辑已有明细时，原货物必须保持选中，允许只改数量后直接保存。
        items.nth(0).locator("button[data-item-action=edit]").click()
        page.fill("#detailItemQty", "4")
        page.click("#detailItemSaveBtn")
        wait_js(page, "document.querySelector('#detailItemsBody tr').innerText.includes('4 个')")
        check("详情弹窗：已有货物只改数量可直接保存（3→4）",
              "4 个" in page.locator("#detailItemsBody tr").first.inner_text(),
              page.locator("#detailItemsBody tr").first.inner_text())
        page.locator("#detailItemsBody tr").first.locator("button[data-item-action=edit]").click()
        page.fill("#detailItemQty", "3")
        page.click("#detailItemSaveBtn")
        wait_js(page, "document.querySelector('#detailItemsBody tr').innerText.includes('3 个')")
        check("详情弹窗：数量恢复为原值，后续审批基线不变",
              "3 个" in page.locator("#detailItemsBody tr").first.inner_text(),
              page.locator("#detailItemsBody tr").first.inner_text())

        # 弹窗内通过处理（v2：通过后扣减库存）
        page.click("#detailApproveBtn")
        page.wait_for_selector("#detailModal", state="hidden", timeout=5000)
        check("详情弹窗：处理后自动关闭", page.locator("#detailModal").evaluate("el => el.classList.contains('hidden')"))
        check("详情弹窗：通过确认对话框说明将扣减库存且不可撤销",
              len(dialogs) >= 1 and "扣减" in dialogs[-1] and "不可" in dialogs[-1], dialogs)
        # 列表状态更新
        wait_js(page,
                "Array.from(document.querySelectorAll('#recentBody tr')).some(tr => tr.innerText.includes('已通过'))")
        check("管理页：处理后列表状态更新为『已通过』", True)

    # ================= 3b. 通过即扣库存（v2）：扣减金额 / 状态机 / 不足拦截 / 驳回不动库存 =================
    # Playwright 的上下文级 extra_http_headers 会覆盖 fetch 的逐请求头（实测），
    # 故进入 API 侧断言前清空上下文头，让下方逐请求的 X-Real-IP 别名（.201/.202）真正生效
    ctx.set_extra_http_headers({})
    # X-Real-IP 别名：API 侧的搜索/提交与浏览器页面流（8.8.8.210）的限流分桶，避免 E2E 撞 429
    def api_call(method, path, body=None):
        return page.evaluate("""async ([m, u, b]) => {
            const resp = await fetch(u, {
                method: m,
                headers: Object.assign({'X-Real-IP': '8.8.8.201'}, b ? {'Content-Type': 'application/json'} : {}),
                body: b ? JSON.stringify(b) : undefined,
                credentials: 'include'
            });
            let data = null;
            try { data = await resp.json(); } catch (e) {}
            return { status: resp.status, data: data };
        }""", [method, path, body])

    def stock_of(barcode, wh=None):
        return page.evaluate("""async ([q, wh]) => {
            let url = 'api/public/goods-search?q=' + encodeURIComponent(q);
            if (wh) url += '&warehouse_id=' + encodeURIComponent(wh);
            const resp = await fetch(url, { credentials: 'include', headers: {'X-Real-IP': '8.8.8.202'} });
            if (!resp.ok) throw new Error('stock 查询失败: HTTP_' + resp.status + '（' + url + '）');
            const data = await resp.json().catch(() => null);
            if (!data) return null;
            const hit = data.find(g => g.barcode === q);
            return hit ? hit.available_stock : null;
        }""", [barcode, wh])

    wh_list = page.evaluate("""async () => {
        const resp = await fetch('api/public/warehouses', {
            credentials: 'include', headers: {'X-Real-IP': '8.8.8.202'}
        });
        return await resp.json().catch(() => null);
    }""") or []
    whid = wh_list[0]["id"] if wh_list else None
    whid2 = wh_list[1]["id"] if len(wh_list) > 1 else None
    check("仓库接口：public/warehouses 返回两仓（一号仓+二号仓）",
          whid is not None and whid2 is not None and len(wh_list) == 2, wh_list)

    # 取 -0001 申请单 id（用于重复处理验证）
    rid = page.evaluate("""() => {
        const trs = Array.from(document.querySelectorAll('#recentBody tr'));
        const t = trs.find(tr => tr.innerText.includes('-0001'));
        if (!t) return null;
        const btn = t.querySelector('button[data-act]');
        return btn ? parseInt(btn.dataset.id, 10) : null;
    }""")
    check("扣库存：取到 -0001 申请单 id", rid is not None, rid)

    # 通过后的库存变化（按仓库断言，评审 P1）：一号仓 轴承 12→9（扣 3）、垫片 5→2（归档单已扣 1 + 本单扣 2）；二号仓不受影响
    _v = stock_of("8888001", whid)
    check("扣库存：一号仓 轴承 12→9（通过扣减 3）", _v == 9.0, _v)
    _v = stock_of("8888003", whid)
    check("扣库存：一号仓 垫片 5→2（归档单扣 1 + 本单扣 2）", _v == 2.0, _v)
    _v = stock_of("8888002", whid2)
    check("扣库存：二号仓 密封圈不受影响（仍 100）", _v == 100.0, _v)

    # 重复通过 → 状态机拦截（409），库存不变
    if rid is not None:
        ra = api_call("POST", "api/requests/%d/status" % rid, {"status": "approved"})
        check("状态机：重复通过被拦截（409）", ra["status"] == 409, ra)
        check("状态机：拦截信息说明已处理",
              ra["data"] is not None and "已处理" in str(ra["data"].get("detail", "")), ra)
        _v = stock_of("8888001", whid)
        check("扣库存：重复通过失败后库存不变（一号仓轴承仍 9）", _v == 9.0, _v)

    # ---------- 出库单（产品需求：申请确认后出库，必须有出库单） ----------
    # -0001 通过（轴承 x3 + 垫片 x2 @一号仓）应生成一张 COMPLETED 出库单（出库单模块可见）
    ob_list = api_call("GET", "api/outbound-orders/")
    check("出库单：出库单列表可访问且非空",
          ob_list["status"] == 200 and isinstance(ob_list["data"], list) and len(ob_list["data"]) >= 1,
          ob_list["status"])
    ob = next((o for o in (ob_list["data"] or [])
               if isinstance(o, dict) and o.get("customer") == "浏览器测试" and o.get("status") == "COMPLETED"), None)
    check("出库单：-0001 生成出库单（COMPLETED / 客户=浏览器测试 / 一号仓）",
          ob is not None and ob.get("warehouse_name") == "一号仓", ob)
    if ob is not None:
        obd = api_call("GET", "api/outbound-orders/%s" % ob["id"])
        ob_items = (obd["data"] or {}).get("items") or []
        check("出库单：明细=轴承 3 + 垫片 2（与实际扣减一致）",
              obd["status"] == 200 and len(ob_items) == 2
              and any("轴承" in (i.get("goods_name") or "") and abs(i.get("quantity", 0) - 3) < 1e-6 for i in ob_items)
              and any("垫片" in (i.get("goods_name") or "") and abs(i.get("quantity", 0) - 2) < 1e-6 for i in ob_items),
              ob_items)
        check("出库单：总金额 = 3x10 + 2x8 = 46",
              abs(((obd["data"] or {}).get("total_amount") or 0) - 46) < 1e-6, obd["data"])
        # 管理页详情弹窗：出库单号展示
        for i in range(1, 12):
            tr = page.locator("#recentBody tr:nth-child(%d)" % i)
            if "-0001" in tr.inner_text():
                tr.click()
                break
        page.wait_for_selector("#detailModal:not(.hidden)")
        _info = page.locator("#detailInfo").inner_text()
        check("出库单：详情弹窗显示出库单号", "出库单" in _info and ob["order_no"] in _info, _info)
        check("出库单：弹窗提示出库单模块可查", "出库单模块可查" in _info, _info)
        page.keyboard.press("Escape")
        page.wait_for_selector("#detailModal", state="hidden")

    # ---------- 双仓审批（评审 P1：审批扣减仓库 = 申请仓库 = 页面展示仓库） ----------
    # 取 -0002 申请单 id（二号仓 密封圈 x50）
    r3_id = page.evaluate("""() => {
        const trs = Array.from(document.querySelectorAll('#recentBody tr'));
        const t = trs.find(tr => tr.innerText.includes('-0002'));
        if (!t) return null;
        const btn = t.querySelector('button[data-act]');
        return btn ? parseInt(btn.dataset.id, 10) : null;
    }""")
    check("双仓审批：取到二号仓申请单 id（-0002）", r3_id is not None, r3_id)
    if r3_id is not None:
        st_before = stock_of("8888002", whid2)
        r3 = api_call("POST", "api/requests/%d/status" % r3_id, {"status": "approved"})
        check("双仓审批：二号仓申请单通过（200）", r3["status"] == 200, r3)
        check("双仓审批：响应含申请仓库名（二号仓）",
              r3["data"] is not None and r3["data"].get("warehouse_name") == "二号仓", r3)
        _v = stock_of("8888002", whid2)
        check("双仓审批：二号仓 密封圈 100→50（扣 50）", _v == st_before - 50, _v)
        check("双仓审批：一号仓库存完全不受影响（轴承 9 / 垫片 2）",
              stock_of("8888001", whid) == 9.0 and stock_of("8888003", whid) == 2.0,
              (stock_of("8888001", whid), stock_of("8888003", whid)))
        r3b = api_call("POST", "api/requests/%d/status" % r3_id, {"status": "approved"})
        check("双仓审批：重复通过被拦截（409），不二次扣减",
              r3b["status"] == 409 and stock_of("8888002", whid2) == st_before - 50, (r3b, stock_of("8888002", whid2)))

    # 多仓环境未指定仓库的申请单 → 审批必须 400（无法确定扣哪个仓），库存不动
    r4_id = page.evaluate("""() => {
        const trs = Array.from(document.querySelectorAll('#recentBody tr'));
        const t = trs.find(tr => tr.innerText.includes('-0003'));
        if (!t) return null;
        const btn = t.querySelector('button[data-act]');
        return btn ? parseInt(btn.dataset.id, 10) : null;
    }""")
    check("多仓拦截：取到未指定仓库申请单 id（-0003）", r4_id is not None, r4_id)
    if r4_id is not None:
        r4 = api_call("POST", "api/requests/%d/status" % r4_id, {"status": "approved"})
        check("多仓拦截：未指定仓库审批被拒（400）", r4["status"] == 400, r4)
        check("多仓拦截：信息提示无法确定扣减仓库",
              r4["data"] is not None and "无法确定" in str(r4["data"].get("detail", "")), r4)
        check("多仓拦截：两仓库存均不变（轴承 9 / 密封圈 50）",
              stock_of("8888001", whid) == 9.0 and stock_of("8888002", whid2) == 50.0,
              (stock_of("8888001", whid), stock_of("8888002", whid2)))

    # 库存不足 → 通过被拦截（400），库存不变、申请单仍待处理
    nr = api_call("POST", "api/requests/", {
        "applicant_name": "API测试员", "contact": "api@test.com",
        "description": "超额申请（轴承 99，库存只有 9）",
        "warehouse_id": whid,
        "items": [{"barcode": "8888001", "quantity": 99}]})
    check("不足拦截：超额申请可提交（待处理）", nr["status"] == 201, nr)
    if nr["status"] == 201:
        over_id = nr["data"]["id"]
        ro = api_call("POST", "api/requests/%d/status" % over_id, {"status": "approved"})
        check("不足拦截：通过被拦截（400）", ro["status"] == 400, ro)
        check("不足拦截：信息含需要/现有数量",
              ro["data"] is not None and "库存不足" in str(ro["data"].get("detail", ""))
              and "需要" in str(ro["data"].get("detail", "")), ro)
        _v = stock_of("8888001", whid)
        check("不足拦截：库存不变（一号仓轴承仍 9）", _v == 9.0, _v)
        lst = api_call("GET", "api/requests/")
        row = [x for x in (lst["data"] or []) if x["id"] == over_id]
        check("不足拦截：申请单仍为待处理", bool(row) and row[0]["status"] == "pending", row)

    # 驳回 → 不动库存；重复驳回被拦截
    nr2 = api_call("POST", "api/requests/", {
        "applicant_name": "API测试员", "contact": "api2@test.com",
        "description": "驳回流程（垫片 1）",
        "warehouse_id": whid,
        "items": [{"barcode": "8888003", "quantity": 1}]})
    if nr2["status"] == 201:
        rej_id = nr2["data"]["id"]
        before = stock_of("8888003", whid)
        rr = api_call("POST", "api/requests/%d/status" % rej_id, {"status": "rejected"})
        check("驳回：驳回成功（200）", rr["status"] == 200, rr)
        check("驳回：库存不变（一号仓垫片仍 %s）" % before, stock_of("8888003", whid) == before, stock_of("8888003", whid))
        rr2 = api_call("POST", "api/requests/%d/status" % rej_id, {"status": "rejected"})
        check("状态机：重复驳回被拦截（409）", rr2["status"] == 409, rr2)

    # 无货物明细的申请单 → 通过仅留痕，不动库存
    nr3 = api_call("POST", "api/requests/", {
        "applicant_name": "API测试员", "contact": "api3@test.com",
        "description": "无货物明细的申请（通过仅留痕）",
        "warehouse_id": whid})
    if nr3["status"] == 201:
        noitem_id = nr3["data"]["id"]
        rn = api_call("POST", "api/requests/%d/status" % noitem_id, {"status": "approved"})
        check("无明细：通过成功（200，仅留痕）", rn["status"] == 200, rn)

    # ================= 4. 归档 Tab：只读详情 =================
    page.click("#tabArchived")
    page.wait_for_selector("#archivedBody tr")
    arow = page.locator("#archivedBody tr").first
    check("归档 Tab：有归档记录行", "APP-" in arow.inner_text())
    check("归档 Tab：归档行显示申请仓库（一号仓）", "一号仓" in arow.inner_text(), arow.inner_text())
    arow.click()
    page.wait_for_selector("#detailModal:not(.hidden)")
    check("归档详情：弹窗打开", page.is_visible("#detailModal"))
    info_txt = page.locator("#detailInfo").inner_text()
    check("归档详情：显示归档批次/归档时间", "归档批次" in info_txt and "归档时间" in info_txt, info_txt)
    check("归档详情：显示申请仓库（一号仓）", "一号仓" in page.locator("#detailInfo").inner_text(),
          page.locator("#detailInfo").inner_text())
    aitems = page.locator("#detailItemsBody tr")
    check("归档详情：明细 1 行（测试垫片 x1）",
          aitems.count() == 1 and "测试垫片" in aitems.nth(0).inner_text() and "1 片" in aitems.nth(0).inner_text(),
          aitems.first.inner_text() if aitems.count() else "empty")
    check("归档详情：归档单无处理按钮（只读）",
          not page.is_visible("#detailApproveBtn") and not page.is_visible("#detailRejectBtn"))
    page.keyboard.press("Escape")
    page.wait_for_selector("#detailModal", state="hidden", timeout=3000)
    check("详情弹窗：Esc 可关闭", True)

    # ================= 5. 查看按钮（CSP 禁止内联事件处理器，动态按钮须事件委托） =================
    # 背景故障：出库单/入库单页 onclick= 内联处理器被 CSP 静默拦截，点"查看"无任何反应。
    # 本段逐页真实点击"查看/打印"按钮，断言弹窗/面板出现，且全程无 CSP 违规。
    _csp_errs = []
    def _on_console(msg):
        if msg.type in ("error", "warning") and "Content Security Policy" in msg.text:
            _csp_errs.append(msg.text)
    page.on("console", _on_console)
    try:
        # 出库单
        page.goto(BASE + "/outbound.html")
        page.wait_for_selector("#orderTableBody tr", timeout=15000)
        page.locator("#orderTableBody button:has-text('查看')").first.click()
        page.wait_for_selector("#orderDetailModal:not(.hidden)", timeout=5000)
        _od = page.locator("#orderDetailContent").inner_text()
        check("出库单页：点'查看'弹窗打开且含单号/明细", "单号" in _od and "明细" in _od, _od[:80])
        page.locator("#closeDetailModal").click()
        page.wait_for_selector("#orderDetailModal", state="hidden", timeout=5000)
        # 入库单
        page.goto(BASE + "/inbound.html")
        page.wait_for_selector("#orderTableBody tr", timeout=15000)
        page.locator("#orderTableBody button:has-text('查看')").first.click()
        page.wait_for_selector("#orderDetailModal:not(.hidden)", timeout=5000)
        _id = page.locator("#orderDetailContent").inner_text()
        check("入库单页：点'查看'弹窗打开且含单号/明细", "单号" in _id and "明细" in _id, _id[:80])
        page.locator("#closeDetailModal").click()
        page.wait_for_selector("#orderDetailModal", state="hidden", timeout=5000)
        # 库存页
        page.goto(BASE + "/stock.html")
        page.wait_for_selector("#stockTableBody tr", timeout=15000)
        page.locator("#stockTableBody button:has-text('查看')").first.click()
        page.wait_for_selector("#stockDetailModal:not(.hidden)", timeout=5000)
        check("库存页：点'查看'弹窗打开", page.is_visible("#stockDetailModal"))
        page.locator("#closeDetailModal").click()
        page.wait_for_selector("#stockDetailModal", state="hidden", timeout=5000)
        # 库位页
        page.goto(BASE + "/location.html")
        page.wait_for_selector("#locationTableBody tr", timeout=15000)
        page.locator("#locationTableBody button:has-text('打印')").first.click()
        page.wait_for_selector("#printModal:not(.hidden)", timeout=5000)
        check("库位页：点'打印'弹窗打开", page.is_visible("#printModal"))
        page.locator("#cancelPrintBtn").click()
        page.wait_for_selector("#printModal", state="hidden", timeout=5000)
        # 盘点页
        ck = api_call("POST", "api/check-orders/", {"warehouse_id": whid})
        check("盘点页：可创建盘点单（供查看按钮验证）", ck["status"] in (200, 201), ck)
        page.goto(BASE + "/check.html")
        page.wait_for_selector("#checkOrdersTableBody tr", timeout=15000)
        page.locator("#checkOrdersTableBody button:has-text('查看')").first.click()
        page.wait_for_timeout(800)
        _ci = page.locator("#currentOrderInfo").inner_text()
        check("盘点页：点'查看'载入单据信息（单号-仓库）", "-" in _ci and len(_ci) > 3, _ci)
        # 全程无 CSP 违规（内联事件处理器被拦截的典型报错）
        check("CSP：5 个页面点击操作全程无 CSP 违规", not _csp_errs, _csp_errs[:2])
    finally:
        page.remove_listener("console", _on_console)

    browser.close()

fails = [n for n, ok in results if not ok]
print(f"\n===== Playwright E2E：{len(results) - len(fails)}/{len(results)} 通过 =====")
for n in fails:
    print("  失败:", n)
sys.exit(1 if fails else 0)
