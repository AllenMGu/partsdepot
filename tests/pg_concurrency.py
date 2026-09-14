#!/usr/bin/env python3
# PostgreSQL 并发复测（评审要求）
#
# 目的：在真实 PostgreSQL 上验证并发建行/并发完成路径。
# 关键点：先 DROP 掉 stock 的 (warehouse,goods,location) 复合唯一约束，
#         模拟"生产库尚无该约束"的现状 —— 这样若应用层锁失效，就会真的写出重复库存行。
#         测试断言"恰好一条库存行"，证明防重复来自代码（行级锁 + 咨询锁），而非数据库约束。
#
# 前置：一个空的 PostgreSQL 测试库（WMS_DATABASE_URL），服务未启动（本脚本自行启动）。
# 用法：WMS_DATABASE_URL="postgresql://user:pw@host/db" .venv/bin/python tests/pg_concurrency.py

import json, os, sys, threading, time, urllib.parse, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
PGURL = os.environ.get("WMS_DATABASE_URL")
if not PGURL or "://" not in PGURL or not PGURL.startswith("postgres"):
    print("FATAL: 需要 WMS_DATABASE_URL 指向一个空的 PostgreSQL 测试库")
    sys.exit(2)
BASE = os.environ.get("WMS_TEST_BASE", "http://127.0.0.1:8091")

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'} | {name}" + (f" | {detail}" if detail else ""))

def req(method, path, body=None, token=None, form=False):
    url = BASE + path
    if body is not None:
        data = (urllib.parse.urlencode(body) if form else json.dumps(body)).encode()
    else:
        data = None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/x-www-form-urlencoded" if form else "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return e.code, parsed
    except Exception as e:
        return -1, str(e)

# ---------- 直连 DB（方言无关） ----------
from sqlalchemy import create_engine, text
ENG = create_engine(PGURL)
def db(sql, params=(), fetch=False):
    with ENG.connect() as conn:
        res = conn.execute(text(sql), params)
        if fetch:
            return res.fetchall()
    return None

# ---------- 启动服务 ----------
import subprocess
PORT = "8091"
env = dict(os.environ)
env.update({
    "DATABASE_URL": PGURL,
    "SECRET_KEY": "test-secret-123",
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD": "Admin-Test-2026",
})
for k in ("LDAP_SERVER","LDAP_BASE_DN","LDAP_ADMIN_DN","LDAP_ADMIN_PASSWORD","LDAP_USER_SEARCH_FILTER"):
    env.pop(k, None)
logf = open(os.path.join(HERE, "pg_conc_server.log"), "w")
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host","127.0.0.1","--port",PORT],
                       cwd=APP_DIR, env=env, stdout=logf, stderr=subprocess.STDOUT)
fatal = 0
try:
    ready = 0
    for _ in range(80):
        try:
            code = req("GET", "/docs")[0]
            if code == 200:
                ready = 1
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not ready:
        print("FATAL: 服务未就绪"); print(logf.read()[-3000:]); fatal = 2
        raise SystemExit(2)

    # ---------- 模拟生产库：移除 stock 复合唯一约束 ----------
    rows = db("SELECT conname FROM pg_constraint WHERE conrelid='stock'::regclass AND contype='u'", fetch=True)
    for (cname,) in rows:
        db(f'ALTER TABLE stock DROP CONSTRAINT "{cname}"')
    left = db("SELECT count(*) FROM pg_constraint WHERE conrelid='stock'::regclass AND contype='u'", fetch=True)[0][0]
    check("前置：stock 复合唯一约束已移除（模拟生产库现状）", left == 0, f"remaining unique={left}")

    # ---------- 基础数据 ----------
    s, b = req("POST", "/api/token", {"username":"admin","password":"Admin-Test-2026"}, form=True)
    admin = (b or {}).get("access_token","")
    check("admin 登录", s==200 and admin, f"status={s}")
    s, w1 = req("POST","/api/warehouses/",{"code":"W1","name":"一号仓"}, admin); W1=(w1 or {}).get("id")
    s, g1 = req("POST","/api/goods/",{"barcode":"G001","name":"物料","price":10}, admin)
    s, l1 = req("POST","/api/locations/",{"warehouse_id":W1,"location_code":"L1","name":"库位1"}, admin)
    check("基础数据(仓/物/位)就绪", all(x==200 for x in (s,)), f"W1={W1}")
    s, u = req("POST","/api/users/",{"username":"op1","password":"Op-Test-2026","full_name":"操作员","warehouse_ids":[W1],"role":"operator"}, admin)
    s, b = req("POST","/api/token",{"username":"op1","password":"Op-Test-2026"}, form=True)
    op = (b or {}).get("access_token","")

    # 管理员切到 W1
    s, tok = req("POST","/api/token",{"username":"admin","password":"Admin-Test-2026"}, form=True)
    aid = ((tok or {}).get("user") or {}).get("id")
    req("POST", f"/api/users/{aid}/switch-warehouse?warehouse_id={W1}", token=admin)

    def stock_row_count():
        return db("SELECT count(*) FROM stock", fetch=True)[0][0]
    def stock_qty():
        r = db("SELECT COALESCE(sum(quantity),0) FROM stock", fetch=True)
        return r[0][0]

    # ============================================================
    # 场景 A：并发"首次入库"同一 (货物,库位) —— 断言恰好一条库存行
    # ============================================================
    print("\n--- 场景 A：两线程并发首次入库同一 (货物,库位) ---")
    s, iA = req("POST","/api/inbound-orders/",{"supplier":"SA"}, admin); IA=(iA or {}).get("id")
    s, iB = req("POST","/api/inbound-orders/",{"supplier":"SB"}, admin); IB=(iB or {}).get("id")
    req("POST", f"/api/inbound-orders/{IA}/items", {"goods_barcode":"G001","location_code":"L1","quantity":3}, admin)
    req("POST", f"/api/inbound-orders/{IB}/items", {"goods_barcode":"G001","location_code":"L1","quantity":4}, admin)
    check("场景A 两张入库单(各1条明细, 同组合)就绪", IA and IB, f"IA={IA} IB={IB}")

    resA = {}
    def submitA(oid):
        s, b = req("POST", f"/api/inbound-orders/{oid}/submit", {}, admin)
        resA[oid] = (s, b)
    threads = [threading.Thread(target=submitA, args=(x,)) for x in (IA, IB)]
    [t.start() for t in threads]; [t.join() for t in threads]
    statuses = sorted(resA.values(), key=lambda v: v[0])
    rowsA, qtyA = stock_row_count(), stock_qty()
    check("场景A 两提交都成功(200)", all(v[0]==200 for v in resA.values()),
          f"statuses={[v[0] for v in resA.values()]}")
    check("场景A 恰好 1 条库存行(并发首入不重复建行)", rowsA == 1, f"stock_rows={rowsA}")
    check("场景A 库存合计=3+4=7", qtyA == 7, f"qty={qtyA}")

    # ============================================================
    # 场景 B：并发"完成同一盘点单"(基线0、无库存行) —— 断言恰好一条库存行
    # ============================================================
    print("\n--- 场景 B：两线程并发完成同一盘点单(无库存行, 实盘6) ---")
    # 用一个全新 (货物,库位) 组合，保证无库存行
    s, g2 = req("POST","/api/goods/",{"barcode":"G002","name":"物料2","price":20}, admin)
    s, l2 = req("POST","/api/locations/",{"warehouse_id":W1,"location_code":"L2","name":"库位2"}, admin)
    s, cB = req("POST","/api/check-orders/",{"warehouse_id":W1}, op); CB=(cB or {}).get("id")
    req("POST","/api/check-orders/items/",{"header_id":CB,"goods_barcode":"G002","location_code":"L2","check_quantity":6}, op)
    check("场景B 盘点单(无库存行组合, 实盘6)就绪", CB, f"CB={CB}")

    resB = {}
    def completeB():
        s, b = req("POST", f"/api/check-orders/{CB}/complete", {}, op)
        resB.setdefault("r", []).append((s,b))
    threads = [threading.Thread(target=completeB) for _ in range(2)]
    [t.start() for t in threads]; [t.join() for t in threads]
    codesB = sorted(x[0] for x in resB.get("r",[]))
    # 只统计 G002/L2 这一组合的库存行
    rowsB = db("SELECT count(*) FROM stock s JOIN goods g ON s.goods_id=g.id JOIN locations l ON s.location_id=l.id WHERE g.barcode='G002' AND l.location_code='L2'", fetch=True)[0][0]
    check("场景B 恰好一个完成成功(另一个被 400 拒绝)", codesB.count(200)==1 and codesB.count(400)==1, f"codes={codesB}")
    check("场景B 该组合恰好 1 条库存行(并发完成不重复建行)", rowsB == 1, f"stock_rows={rowsB}")

    # ============================================================
    # 场景 C：盘点完成 ∥ 首次扫码入库 并发同组合 —— 断言不重复建行、不 500
    # ============================================================
    print("\n--- 场景 C：盘点完成 与 首次扫码入库 并发同 (货物,库位) ---")
    s, g3 = req("POST","/api/goods/",{"barcode":"G003","name":"物料3","price":30}, admin)
    s, l3 = req("POST","/api/locations/",{"warehouse_id":W1,"location_code":"L3","name":"库位3"}, admin)
    s, cC = req("POST","/api/check-orders/",{"warehouse_id":W1}, op); CC=(cC or {}).get("id")
    req("POST","/api/check-orders/items/",{"header_id":CC,"goods_barcode":"G003","location_code":"L3","check_quantity":5}, op)
    check("场景C 盘点单(无库存行组合, 实盘5)就绪", CC, f"CC={CC}")

    resC = {}
    barrier = threading.Barrier(2)
    def completeC():
        barrier.wait()
        s, b = req("POST", f"/api/check-orders/{CC}/complete", {}, op)
        resC["complete"] = (s,b)
    def scanC():
        barrier.wait()
        s, b = req("POST","/api/inventory/scan",{"goods_barcode":"G003","location_code":"L3","type":"入库","quantity":2}, op)
        resC["scan"] = (s,b)
    t1 = threading.Thread(target=completeC); t2 = threading.Thread(target=scanC)
    t1.start(); t2.start(); t1.join(); t2.join()
    rowsC = db("SELECT count(*) FROM stock s JOIN goods g ON s.goods_id=g.id JOIN locations l ON s.location_id=l.id WHERE g.barcode='G003' AND l.location_code='L3'", fetch=True)[0][0]
    no500 = all(v[0] != 500 for v in resC.values())
    codesC = {k: v[0] for k, v in resC.items()}
    check("场景C 无 500(不触发唯一约束冲突)", no500, f"codes={codesC}")
    check("场景C 该组合恰好 1 条库存行(盘点∥扫码不重复建行)", rowsC == 1, f"stock_rows={rowsC} codes={codesC}")

finally:
    srv.terminate()
    try:
        srv.wait(timeout=5)
    except Exception:
        srv.kill()
    logf.close()

    passed = sum(1 for _,c,_ in results if c)
    total = len(results)
    print(f"\n===== PG 并发复测：{passed}/{total} 通过 =====")
    for name, c, d in results:
        if not c:
            print(f"  失败: {name} | {d}")
    if fatal:
        sys.exit(fatal)
    sys.exit(0 if passed == total else 1)
