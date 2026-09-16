#!/usr/bin/env python3
"""Playwright 端到端验证（Chromium）：
1. request.html 申请提交：添加行 → 行内搜索备件 → 候选/选中行显示可用库存 → 超库存软提示 → 提交
1b. 多行异步搜索竞态（乱序响应不错行）/ 重渲染保留搜索词 / 精确聚焦到该行
2. index.html 登录 → request-admin.html 申请管理：点击申请单 → 详情弹窗（表头信息 + 带表头的货物明细表）
   → 弹窗内通过处理 → 归档 Tab 点击归档单 → 只读详情
"""
import os
import sys
import tempfile


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

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.set_default_timeout(15000)
    dialogs = []
    page.on("dialog", lambda d: (d.accept(), dialogs.append(d.message)))

    # ================= 1. 申请提交页 =================
    page.goto(BASE + "/request.html")
    page.wait_for_selector("#addGoodsRowBtn")
    check("提交页：可见『添加货物行』按钮", page.is_visible("#addGoodsRowBtn"))
    check("提交页：初始无货物行", page.query_selector_all("#goodsTableBody tr") == [])

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
        check("详情弹窗：事由描述完整显示", "Playwright 端到端验证" in page.locator("#detailDescription").inner_text(),
              page.locator("#detailDescription").inner_text())
        # 货物明细表：表头 + 两行
        thead = [t.inner_text().strip() for t in page.locator("table:has(tbody#detailItemsBody) thead th").all()]
        check("详情弹窗：明细表带表头（序号/货物名称/条码/规格型号/单位/数量）",
              thead == ["序号", "货物名称", "条码", "规格型号", "单位", "数量"], thead)
        items = page.locator("#detailItemsBody tr")
        check("详情弹窗：明细 2 行", items.count() == 2, items.count())
        check("详情弹窗：明细含 测试轴承 x3 与 测试密封圈 x5",
              "测试轴承" in items.nth(0).inner_text() and "3 个" in items.nth(0).inner_text()
              and "测试密封圈" in items.nth(1).inner_text() and "5 件" in items.nth(1).inner_text(),
              items.nth(0).inner_text() + " || " + items.nth(1).inner_text())
        check("详情弹窗：待处理单显示通过/驳回按钮",
              page.is_visible("#detailApproveBtn") and page.is_visible("#detailRejectBtn"))

        # 弹窗内通过处理
        page.click("#detailApproveBtn")
        page.wait_for_selector("#detailModal", state="hidden", timeout=5000)
        check("详情弹窗：处理后自动关闭", page.locator("#detailModal").evaluate("el => el.classList.contains('hidden')"))
        check("详情弹窗：通过确认对话框出现", any("通过" in d for d in dialogs), dialogs)
        # 列表状态更新
        page.wait_for_function(
            "Array.from(document.querySelectorAll('#recentBody tr')).some(tr => tr.innerText.includes('已通过'))",
            timeout=5000)
        check("管理页：处理后列表状态更新为『已通过』", True)

    # ================= 4. 归档 Tab：只读详情 =================
    page.click("#tabArchived")
    page.wait_for_selector("#archivedBody tr")
    arow = page.locator("#archivedBody tr").first
    check("归档 Tab：有归档记录行", "APP-" in arow.inner_text())
    arow.click()
    page.wait_for_selector("#detailModal:not(.hidden)")
    check("归档详情：弹窗打开", page.is_visible("#detailModal"))
    info_txt = page.locator("#detailInfo").inner_text()
    check("归档详情：显示归档批次/归档时间", "归档批次" in info_txt and "归档时间" in info_txt, info_txt)
    aitems = page.locator("#detailItemsBody tr")
    check("归档详情：明细 1 行（测试轴承 x1）",
          aitems.count() == 1 and "测试轴承" in aitems.nth(0).inner_text() and "1 个" in aitems.nth(0).inner_text(),
          aitems.first.inner_text() if aitems.count() else "empty")
    check("归档详情：归档单无处理按钮（只读）",
          not page.is_visible("#detailApproveBtn") and not page.is_visible("#detailRejectBtn"))
    page.keyboard.press("Escape")
    page.wait_for_selector("#detailModal", state="hidden", timeout=3000)
    check("详情弹窗：Esc 可关闭", True)

    browser.close()

fails = [n for n, ok in results if not ok]
print(f"\n===== Playwright E2E：{len(results) - len(fails)}/{len(results)} 通过 =====")
for n in fails:
    print("  失败:", n)
sys.exit(1 if fails else 0)
