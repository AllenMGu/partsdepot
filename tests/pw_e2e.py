#!/usr/bin/env python3
"""Playwright 端到端验证（系统 Chromium）：
1. request.html 申请提交：添加行 → 行内搜索备件 → 候选/选中行显示可用库存 → 超库存软提示 → 提交
2. index.html 登录 → request-admin.html 申请管理：点击申请单 → 详情弹窗（表头信息 + 带表头的货物明细表）
   → 弹窗内通过处理 → 归档 Tab 点击归档单 → 只读详情
"""
import os
import sys

# 浏览器需要可写的 HOME 与浏览器缓存（沙箱内默认 HOME 只读）
_WS_HOME = "/data/dsh/home/库存管理/.pw_home"
os.makedirs(_WS_HOME, exist_ok=True)
os.environ["HOME"] = _WS_HOME
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/data/dsh/home/库存管理/.pw_browsers"

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
