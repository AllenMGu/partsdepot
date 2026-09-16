#!/usr/bin/env python3
"""E2E 种子数据（纯标准库 urllib，无第三方依赖）：
仓库/库位/备件(轴承有库存、密封圈无库存、垫片有库存) + 一张待处理申请单(2 行货物) + 一张已归档申请单。

库存扣减规则（v2 通过即扣库存）下的种子设计：
- 轴承 8888001 库存 12：近期待处理单 r1 申请 3（可通过，审批时扣 3 → 9）
- 垫片 8888003 库存 5：r1 申请 2；归档单 r2 申请 1（r2 审批时扣 1 → 4，之后 r1 再扣 2 → 2）
- 密封圈 8888002 库存 0：仅用于"无库存"展示与页面提交（不审批）
"""
import json
import sqlite3
import sys
import urllib.request
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


def main():
    # 登录，拿 HttpOnly Cookie
    _, cookie = req("POST", "/api/token", "username=admin&password=" + ADMIN_PW, form=True)
    cookie = cookie.split(";")[0]

    wid = req("POST", "/api/warehouses/", {"code": "W1", "name": "一号仓"}, cookie)[0]["id"]
    req("POST", "/api/locations/", {"warehouse_id": wid, "location_code": "A1", "name": "库位A1"}, cookie)
    req("POST", "/api/goods/", {"barcode": "8888001", "name": "测试轴承", "spec": "6204", "unit": "个", "price": 10}, cookie)
    req("POST", "/api/goods/", {"barcode": "8888002", "name": "测试密封圈", "spec": "NBR-35", "unit": "件", "price": 5}, cookie)
    req("POST", "/api/goods/", {"barcode": "8888003", "name": "测试垫片", "spec": "DN50", "unit": "片", "price": 8}, cookie)
    scan = req("POST", "/api/inventory/scan",
               {"goods_barcode": "8888001", "location_code": "A1", "type": "入库", "quantity": 12}, cookie)[0]
    assert scan["current_stock"] == 12.0, scan
    scan = req("POST", "/api/inventory/scan",
               {"goods_barcode": "8888003", "location_code": "A1", "type": "入库", "quantity": 5}, cookie)[0]
    assert scan["current_stock"] == 5.0, scan

    # 搜索接口应带可用库存（在任何审批扣减之前断言初始值：轴承 12 / 密封圈 0 / 垫片 5）
    search = json.loads(urllib.request.urlopen(BASE + "/api/public/goods-search?q=8888").read())
    stocks = {g["barcode"]: g["available_stock"] for g in search}
    assert stocks["8888001"] == 12.0 and stocks["8888002"] == 0.0 and stocks["8888003"] == 5.0, search
    print("search stocks (initial):", stocks)

    # 公开仓库列表接口（申请页仓库下拉用）
    whs = json.loads(urllib.request.urlopen(BASE + "/api/public/warehouses").read())
    assert whs and whs[0]["id"] == wid and whs[0]["name"] == "一号仓", whs
    print("public warehouses:", whs)

    # 近期待处理：2 行货物（轴承 x3 + 垫片 x2；均可通过，E2E 审批时扣库存）
    r1 = req("POST", "/api/requests/", {
        "applicant_name": "浏览器测试", "department": "装配部", "contact": "bw@test.com",
        "description": "Playwright 端到端验证：轴承 3 个、垫片 2 片（审批通过将从一号仓扣减库存）",
        "warehouse_id": wid,
        "items": [{"barcode": "8888001", "quantity": 3}, {"barcode": "8888003", "quantity": 2}],
    })[0]
    print("recent request:", r1["reference"])

    # 归档单：提交 → 回拨 40 天 → 通过（垫片 5→4）→ 归档
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
    # 审批已扣减：垫片 5 → 4
    search = json.loads(urllib.request.urlopen(BASE + "/api/public/goods-search?q=8888003").read())
    assert search and search[0]["available_stock"] == 4.0, search
    print("after r2 approval: 8888003 stock =", search[0]["available_stock"])
    arch = req("POST", "/api/requests/archive-now", {}, cookie)[0]
    print("archived:", arch)

    # 归档单应保留申请仓库
    arch_rows = req("GET", "/api/requests/archive/", None, cookie)[0]
    assert arch_rows and arch_rows[0]["warehouse_id"] == wid and arch_rows[0]["warehouse_name"] == "一号仓", arch_rows
    print("archive row warehouse ok:", arch_rows[0]["warehouse_id"], arch_rows[0]["warehouse_name"])
    print("SEED OK")


if __name__ == "__main__":
    main()
