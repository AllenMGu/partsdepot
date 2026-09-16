#!/usr/bin/env python3
"""E2E 种子数据（纯标准库 urllib，无第三方依赖）：
仓库/库位/备件(一有一无库存) + 一张待处理申请单(2 行货物) + 一张已归档申请单。
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
    scan = req("POST", "/api/inventory/scan",
               {"goods_barcode": "8888001", "location_code": "A1", "type": "入库", "quantity": 12}, cookie)[0]
    assert scan["current_stock"] == 12.0, scan

    # 近期待处理：2 行货物
    r1 = req("POST", "/api/requests/", {
        "applicant_name": "浏览器测试", "department": "装配部", "contact": "bw@test.com",
        "description": "Playwright 端到端验证：轴承 3 个、密封圈 5 件",
        "items": [{"barcode": "8888001", "quantity": 3}, {"barcode": "8888002", "quantity": 5}],
    })[0]
    print("recent request:", r1["reference"])

    # 归档单：提交 → 回拨 40 天 → 通过 → 归档
    r2 = req("POST", "/api/requests/", {
        "applicant_name": "归档测试", "contact": "arch@test.com",
        "description": "用于验证归档详情弹窗", "items": [{"barcode": "8888001", "quantity": 1}],
    })[0]
    old = (datetime.datetime.now() - datetime.timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    c = sqlite3.connect(DB_PATH)
    c.execute("update requests set create_time=? where id=?", (old, r2["id"]))
    c.commit()
    c.close()
    st = req("POST", "/api/requests/%d/status" % r2["id"], {"status": "approved"}, cookie)[0]
    assert st["status"] == "approved", st
    arch = req("POST", "/api/requests/archive-now", {}, cookie)[0]
    print("archived:", arch)

    # 搜索接口应带可用库存
    search = json.loads(urllib.request.urlopen(BASE + "/api/public/goods-search?q=8888").read())
    stocks = {g["barcode"]: g["available_stock"] for g in search}
    assert stocks["8888001"] == 12.0 and stocks["8888002"] == 0.0, search
    print("search stocks:", stocks)
    print("SEED OK")


if __name__ == "__main__":
    main()
