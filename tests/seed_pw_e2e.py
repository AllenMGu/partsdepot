#!/usr/bin/env python3
"""E2E 种子数据（纯标准库 urllib，无第三方依赖）：
双仓环境（评审 P1：申请页库存展示必须与审批扣减仓库一致，E2E 必须覆盖双仓）：
- 一号仓 W1：轴承 8888001 x12、垫片 8888003 x5（密封圈 0）
- 二号仓 W2：密封圈 8888002 x100（轴承 0、垫片 0）
申请单：
- r1 待处理：轴承 x3 + 垫片 x2 @ W1（审批 → W1 轴承 12→9、W1 垫片 5→2）
- r2 已归档：垫片 x1 @ W1（40 天前，审批时垫片 5→4）
- r3 待处理：密封圈 x50 @ W2（审批 → W2 密封圈 100→50，W1 不动）
- r4 待处理：轴承 x1，未指定仓库（多仓环境下审批必须 400 拒绝）
"""
import json
import sqlite3
import sys
import urllib.request
import urllib.error
import datetime
from pathlib import Path

BASE = "http://127.0.0.1:%s" % (sys.argv[1] if len(sys.argv) > 1 else "8099")
DB_PATH = sys.argv[2] if len(sys.argv) > 2 else str(Path(__file__).resolve().parent.parent / ".pwtest.db")
ADMIN_PW = sys.argv[3] if len(sys.argv) > 3 else "Admin-Test-2026"


def req(method, path, body=None, cookie=None, form=False):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        if form:
            data = body.encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r) as resp:
        raw = resp.read()
        set_cookie = resp.headers.get("Set-Cookie") or ""
        return json.loads(raw) if raw else None, set_cookie


def get_json(path):
    return json.loads(urllib.request.urlopen(BASE + path).read())


def get_status(path):
    try:
        urllib.request.urlopen(BASE + path)
        return 200
    except urllib.error.HTTPError as e:
        return e.code


def main():
    # 登录，拿 HttpOnly Cookie
    _, cookie = req("POST", "/api/token", "username=admin&password=" + ADMIN_PW, form=True)
    cookie = cookie.split(";")[0]

    # 双仓：一号仓 W1 + 二号仓 W2（各自一个库位）
    wid = req("POST", "/api/warehouses/", {"code": "W1", "name": "一号仓"}, cookie)[0]["id"]
    req("POST", "/api/locations/", {"warehouse_id": wid, "location_code": "A1", "name": "库位A1"}, cookie)
    wid2 = req("POST", "/api/warehouses/", {"code": "W2", "name": "二号仓"}, cookie)[0]["id"]
    req("POST", "/api/locations/", {"warehouse_id": wid2, "location_code": "A2", "name": "库位A2"}, cookie)

    req("POST", "/api/goods/", {"barcode": "8888001", "name": "测试轴承", "spec": "6204", "unit": "个", "price": 10}, cookie)
    req("POST", "/api/goods/", {"barcode": "8888002", "name": "测试密封圈", "spec": "NBR-35", "unit": "件", "price": 5}, cookie)
    req("POST", "/api/goods/", {"barcode": "8888003", "name": "测试垫片", "spec": "DN50", "unit": "片", "price": 8}, cookie)

    # W1：轴承 12、垫片 5；W2：密封圈 100（刻意错位：每货在一仓为 0，另一仓 >0）
    scan = req("POST", "/api/inventory/scan",
               {"goods_barcode": "8888001", "location_code": "A1", "type": "入库", "quantity": 12}, cookie)[0]
    assert scan["current_stock"] == 12.0, scan
    scan = req("POST", "/api/inventory/scan",
               {"goods_barcode": "8888003", "location_code": "A1", "type": "入库", "quantity": 5}, cookie)[0]
    assert scan["current_stock"] == 5.0, scan
    scan = req("POST", "/api/inventory/scan",
               {"goods_barcode": "8888002", "location_code": "A2", "type": "入库", "quantity": 100}, cookie)[0]
    assert scan["current_stock"] == 100.0, scan

    # 搜索接口按仓库汇总（评审 P1 双仓一致性：W1=0、W2>0 时选 W1 必须显示 0）
    def stock_of(barcode, wid_x=None):
        path = "/api/public/goods-search?q=" + barcode
        if wid_x is not None:
            path += "&warehouse_id=" + str(wid_x)
        rows = get_json(path)
        hit = next((g for g in rows if g["barcode"] == barcode), None)
        return float(hit["available_stock"]) if hit else -1.0

    assert stock_of("8888001") == 12.0 and stock_of("8888002") == 100.0 and stock_of("8888003") == 5.0, "全仓合计"
    assert stock_of("8888001", wid) == 12.0 and stock_of("8888002", wid) == 0.0 and stock_of("8888003", wid) == 5.0, "W1 汇总"
    assert stock_of("8888001", wid2) == 0.0 and stock_of("8888002", wid2) == 100.0 and stock_of("8888003", wid2) == 0.0, "W2 汇总"
    assert get_status("/api/public/goods-search?q=8888001&warehouse_id=99999") == 400, "失效仓库应 400"
    print("search stocks (dual-wh): W1 轴承12/密封圈0/垫片5, W2 轴承0/密封圈100/垫片0, 全仓合计 12/100/5")

    # 公开仓库列表接口（申请页仓库下拉用）：两仓
    whs = get_json("/api/public/warehouses")
    assert whs and len(whs) == 2 and whs[0]["id"] == wid and whs[0]["name"] == "一号仓", whs
    assert whs[1]["id"] == wid2 and whs[1]["name"] == "二号仓", whs
    print("public warehouses:", whs)

    # 近期待处理：2 行货物 @ W1（轴承 x3 + 垫片 x2；审批通过时从 W1 扣减）
    r1 = req("POST", "/api/requests/", {
        "applicant_name": "浏览器测试", "department": "装配部", "contact": "bw@test.com",
        "description": "Playwright 端到端验证：轴承 3 个、垫片 2 片（审批通过将从一号仓扣减库存）",
        "warehouse_id": wid,
        "items": [{"barcode": "8888001", "quantity": 3}, {"barcode": "8888003", "quantity": 2}],
    })[0]
    print("recent request:", r1["reference"])

    # W2 的待处理单（密封圈 x50：审批通过时从二号仓扣减，与 W1 互不影响）
    r3 = req("POST", "/api/requests/", {
        "applicant_name": "双仓测试", "contact": "dw@test.com",
        "description": "Playwright 双仓验证：密封圈 50 件（审批通过将从二号仓扣减库存）",
        "warehouse_id": wid2,
        "items": [{"barcode": "8888002", "quantity": 50}],
    })[0]
    print("W2 request:", r3["reference"])

    # 多仓环境未指定仓库的待处理单（审批必须 400：无法确定扣哪个仓）
    r4 = req("POST", "/api/requests/", {
        "applicant_name": "多仓测试", "contact": "mw@test.com",
        "description": "Playwright 多仓拦截验证：未指定申请仓库（审批应被拒绝）",
        "items": [{"barcode": "8888001", "quantity": 1}],
    })[0]
    print("no-warehouse request:", r4["reference"])

    # 归档单：提交 → 回拨 40 天 → 通过（W1 垫片 5→4）→ 归档
    r2 = req("POST", "/api/requests/", {
        "applicant_name": "归档测试", "contact": "arch@test.com",
        "description": "用于验证归档详情弹窗（垫片 1 片，已通过并扣减库存）",
        "warehouse_id": wid,
        "items": [{"barcode": "8888003", "quantity": 1}],
    })[0]
    old = (datetime.datetime.now() - datetime.timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    c = sqlite3.connect(DB_PATH)
    c.execute("update requests set create_time=? where id=?", (old, r2["id"]))
    c.commit()
    c.close()
    st = req("POST", "/api/requests/%d/status" % r2["id"], {"status": "approved"}, cookie)[0]
    assert st["status"] == "approved", st
    # 审批已扣减：W1 垫片 5 → 4（W2 密封圈不受影响）
    # 用管理端 /api/stock/ 核对（独立于公开搜索限流桶，避免 E2E 全程挤爆 30 次/分钟）
    def admin_stock(barcode, wid_x):
        rows = req("GET", "/api/stock/?goods_barcode=%s&warehouse_id=%d" % (barcode, wid_x),
                   None, cookie)[0]
        return sum(float(r["quantity"]) for r in rows if r.get("goods_barcode") == barcode)

    assert admin_stock("8888003", wid) == 4.0, "W1 垫片应 5→4"
    assert admin_stock("8888002", wid2) == 100.0, "W2 密封圈不应变动"
    print("after r2 approval: W1 垫片 =", admin_stock("8888003", wid), ", W2 密封圈 =", admin_stock("8888002", wid2))
    arch = req("POST", "/api/requests/archive-now", {}, cookie)[0]
    print("archived:", arch)

    # 归档单应保留申请仓库
    arch_rows = req("GET", "/api/requests/archive/", None, cookie)[0]
    assert arch_rows and arch_rows[0]["warehouse_id"] == wid and arch_rows[0]["warehouse_name"] == "一号仓", arch_rows
    print("archive row warehouse ok:", arch_rows[0]["warehouse_id"], arch_rows[0]["warehouse_name"])
    print("SEED OK")


if __name__ == "__main__":
    main()
