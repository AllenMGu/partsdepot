#!/usr/bin/env python3
# PostgreSQL 并发复测（评审要求）
#
# 目的：在真实 PostgreSQL 上验证并发建行/并发完成/并发"明细写入∥提交"路径。
# 关键点：先 DROP 掉 stock 的 (warehouse,goods,location) 复合唯一约束，
#         模拟"生产库尚无该约束"的现状 —— 这样若应用层锁失效，就会真的写出重复库存行。
#         测试断言"恰好一条库存行"，证明防重复来自代码（行级锁 + 咨询锁），而非数据库约束。
#
# 场景：A 并发首次入库同组合 | B 并发重复完成盘点 | C 盘点完成∥首次扫码入库
#       D 入库 新增明细∥提交 | E 盘点 录入明细∥完成 | F 出库 新增明细∥提交 | G 入库 编辑明细∥提交
#       D~G 断言"终态单据不得出现未过账/未扣减的新明细"；H/I 验证跨流程锁顺序：
#       H 申请审批∥手工出库，I 申请审批∥扫码出库。
#       新明细要么随提交/完成一起过账，要么被 400 拒绝 —— 二者必居其一，不允许中间态。
#
# 安全：启动前先过 tests/test_db_guard.py 护栏 —— 目标库必须是可丢弃的空测试库
#       （黑名单库名硬拒；核心表非空拒；WMS_ALLOW_NONEMPTY_TEST_DB=1 可豁免非空检查）。
#       **切勿传入生产连接串。**
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

# ---------- 测试库安全护栏（防误伤含真实数据的库） ----------
# 本脚本会 DROP stock 复合唯一约束并写入业务数据；目标库必须是可丢弃的空测试库。
sys.path.insert(0, HERE)
from test_db_guard import guard as _db_guard, GuardError as _GuardError
try:
    _name, _ne = _db_guard(PGURL, include_users=True)
except _GuardError as _e:
    print(f"FATAL: 测试库安全护栏拒绝执行\n{_e}", file=sys.stderr)
    raise SystemExit(3)

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

    # ---------- 场景 D-G 通用查询助手 ----------
    def stock_qty_of(barcode, loc):
        r = db("SELECT COALESCE(sum(quantity),0), count(*) FROM stock s JOIN goods g ON s.goods_id=g.id JOIN locations l ON s.location_id=l.id WHERE g.barcode=:b AND l.location_code=:l",
               {"b": barcode, "l": loc}, fetch=True)
        return (r[0][0] or 0), r[0][1]

    def items_count(table, order_id):
        return db(f"SELECT count(*) FROM {table} WHERE header_id=:i", {"i": order_id}, fetch=True)[0][0]

    def header_status(table, order_id):
        return db(f"SELECT status FROM {table} WHERE id=:i", {"i": order_id}, fetch=True)[0][0]

    # ============================================================
    # 场景 D：入库"新增明细" ∥ "提交" 强制交错 —— 不允许出现已提交单据挂着未过账明细
    # ============================================================
    print("\n--- 场景 D：入库 新增明细 ∥ 提交 ---")
    s, g4 = req("POST","/api/goods/",{"barcode":"G004","name":"物料4","price":40}, admin)
    s, l4 = req("POST","/api/locations/",{"warehouse_id":W1,"location_code":"L4","name":"库位4"}, admin)
    s, oD = req("POST","/api/inbound-orders/",{"supplier":"SD"}, admin); OD=(oD or {}).get("id")
    req("POST", f"/api/inbound-orders/{OD}/items", {"goods_barcode":"G004","location_code":"L4","quantity":2}, admin)
    check("场景D 入库单(1条明细 qty=2)就绪", OD, f"OD={OD} items={items_count('inbound_order_item', OD)}")

    resD = {}
    barD = threading.Barrier(2)
    def addD():
        barD.wait()
        s, b = req("POST", f"/api/inbound-orders/{OD}/items", {"goods_barcode":"G004","location_code":"L4","quantity":3}, admin)
        resD["add"] = (s,b)
    def submitD():
        barD.wait()
        s, b = req("POST", f"/api/inbound-orders/{OD}/submit", {}, admin)
        resD["submit"] = (s,b)
    t1 = threading.Thread(target=addD); t2 = threading.Thread(target=submitD)
    t1.start(); t2.start(); t1.join(); t2.join()
    qtyD, rowsD = stock_qty_of("G004", "L4")
    nD = items_count("inbound_order_item", OD)
    stD = header_status("inbound_order_header", OD)
    codesD = {k: v[0] for k, v in resD.items()}
    okD = all(v[0] != 500 for v in resD.values())
    consistentD = (
        (resD["add"][0] == 200 and resD["submit"][0] == 200 and nD == 2 and qtyD == 5)
        or (resD["add"][0] == 400 and resD["submit"][0] == 200 and nD == 1 and qtyD == 2)
    )
    check("场景D 无 500", okD, f"codes={codesD} status={stD} items={nD} stock={qtyD}")
    check("场景D 结果一致(新明细要么随提交过账、要么被 400 拒绝)", consistentD,
          f"codes={codesD} status={stD} items={nD} stock={qtyD}(期望 2+3=5 或 2)")

    # ============================================================
    # 场景 E：盘点"录入明细" ∥ "完成" 强制交错 —— 已完成单据不允许出现未过账新明细
    # ============================================================
    print("\n--- 场景 E：盘点 录入明细 ∥ 完成 ---")
    s, l5 = req("POST","/api/locations/",{"warehouse_id":W1,"location_code":"L5","name":"库位5"}, admin)
    s, l6 = req("POST","/api/locations/",{"warehouse_id":W1,"location_code":"L6","name":"库位6"}, admin)
    s, cE = req("POST","/api/check-orders/",{"warehouse_id":W1}, op); CE=(cE or {}).get("id")
    req("POST","/api/check-orders/items/",{"header_id":CE,"goods_barcode":"G004","location_code":"L5","check_quantity":5}, op)
    check("场景E 盘点单(明细1: G004/L5 实盘5)就绪", CE, f"CE={CE} items={items_count('check_order_item', CE)}")

    resE = {}
    barE = threading.Barrier(2)
    def addE():
        barE.wait()
        s, b = req("POST","/api/check-orders/items/",{"header_id":CE,"goods_barcode":"G004","location_code":"L6","check_quantity":3}, op)
        resE["add"] = (s,b)
    def completeE():
        barE.wait()
        s, b = req("POST", f"/api/check-orders/{CE}/complete", {}, op)
        resE["complete"] = (s,b)
    t1 = threading.Thread(target=addE); t2 = threading.Thread(target=completeE)
    t1.start(); t2.start(); t1.join(); t2.join()
    qE5, rE5 = stock_qty_of("G004", "L5")
    qE6, rE6 = stock_qty_of("G004", "L6")
    nE = items_count("check_order_item", CE)
    stE = header_status("check_order_header", CE)
    codesE = {k: v[0] for k, v in resE.items()}
    okE = all(v[0] != 500 for v in resE.values())
    consistentE = (
        (resE["add"][0] == 200 and resE["complete"][0] == 200 and nE == 2 and qE5 == 5 and qE6 == 3)
        or (resE["add"][0] == 400 and resE["complete"][0] == 200 and nE == 1 and qE5 == 5 and qE6 == 0)
    )
    check("场景E 无 500", okE, f"codes={codesE} status={stE} items={nE} stock5={qE5} stock6={qE6}")
    check("场景E 结果一致(新明细要么随完成过账、要么被 400 拒绝)", consistentE,
          f"codes={codesE} status={stE} items={nE} stock5={qE5} stock6={qE6}")

    # ============================================================
    # 场景 F：出库"新增明细" ∥ "提交" 强制交错 —— 不允许出现已提交单据挂着未扣减明细
    # ============================================================
    print("\n--- 场景 F：出库 新增明细 ∥ 提交 ---")
    s, g5 = req("POST","/api/goods/",{"barcode":"G005","name":"物料5","price":50}, admin)
    s, l7 = req("POST","/api/locations/",{"warehouse_id":W1,"location_code":"L7","name":"库位7"}, admin)
    s, b = req("POST","/api/inventory/scan",{"goods_barcode":"G005","location_code":"L7","type":"入库","quantity":4}, op)
    s, oF = req("POST","/api/outbound-orders/",{"supplier":"SF","customer":"CF"}, admin); OF=(oF or {}).get("id")
    req("POST", f"/api/outbound-orders/{OF}/items", {"goods_barcode":"G005","location_code":"L7","quantity":1}, admin)
    check("场景F 出库单(1条明细 qty=1, 库存=4)就绪", OF and s == 200, f"OF={OF} stock={stock_qty_of('G005','L7')[0]}")

    resF = {}
    barF = threading.Barrier(2)
    def addF():
        barF.wait()
        s, b = req("POST", f"/api/outbound-orders/{OF}/items", {"goods_barcode":"G005","location_code":"L7","quantity":2}, admin)
        resF["add"] = (s,b)
    def submitF():
        barF.wait()
        s, b = req("POST", f"/api/outbound-orders/{OF}/submit", {}, admin)
        resF["submit"] = (s,b)
    t1 = threading.Thread(target=addF); t2 = threading.Thread(target=submitF)
    t1.start(); t2.start(); t1.join(); t2.join()
    qtyF, _ = stock_qty_of("G005", "L7")
    nF = items_count("outbound_order_item", OF)
    stF = header_status("outbound_order_header", OF)
    codesF = {k: v[0] for k, v in resF.items()}
    okF = all(v[0] != 500 for v in resF.values())
    consistentF = (
        (resF["add"][0] == 200 and resF["submit"][0] == 200 and nF == 2 and qtyF == 4 - 3)
        or (resF["add"][0] == 400 and resF["submit"][0] == 200 and nF == 1 and qtyF == 4 - 1)
    )
    check("场景F 无 500", okF, f"codes={codesF} status={stF} items={nF} stock={qtyF}")
    check("场景F 结果一致(新明细要么随提交扣减、要么被 400 拒绝)", consistentF,
          f"codes={codesF} status={stF} items={nF} stock={qtyF}(期望 4-3=1 或 4-1=3)")

    # ============================================================
    # 场景 G：入库"编辑明细" ∥ "提交" 强制交错 —— 编辑要么生效并过账、要么被 400 拒绝
    # ============================================================
    print("\n--- 场景 G：入库 编辑明细 ∥ 提交 ---")
    s, g6 = req("POST","/api/goods/",{"barcode":"G006","name":"物料6","price":60}, admin)
    s, l8 = req("POST","/api/locations/",{"warehouse_id":W1,"location_code":"L8","name":"库位8"}, admin)
    s, oG = req("POST","/api/inbound-orders/",{"supplier":"SG"}, admin); OG=(oG or {}).get("id")
    s, itG = req("POST", f"/api/inbound-orders/{OG}/items", {"goods_barcode":"G006","location_code":"L8","quantity":2}, admin)
    item_id_G = (itG or {}).get("id")
    check("场景G 入库单(1条明细 qty=2, 可编辑)就绪", OG and item_id_G, f"OG={OG} item={item_id_G}")

    resG = {}
    barG = threading.Barrier(2)
    def editG():
        barG.wait()
        s, b = req("PUT", f"/api/inbound-orders/{OG}/items/{item_id_G}", {"goods_barcode":"G006","location_code":"L8","quantity":9}, admin)
        resG["edit"] = (s,b)
    def submitG():
        barG.wait()
        s, b = req("POST", f"/api/inbound-orders/{OG}/submit", {}, admin)
        resG["submit"] = (s,b)
    t1 = threading.Thread(target=editG); t2 = threading.Thread(target=submitG)
    t1.start(); t2.start(); t1.join(); t2.join()
    qtyG, _ = stock_qty_of("G006", "L8")
    stG = header_status("inbound_order_header", OG)
    codesG = {k: v[0] for k, v in resG.items()}
    okG = all(v[0] != 500 for v in resG.values())
    consistentG = (
        (resG["edit"][0] == 200 and resG["submit"][0] == 200 and qtyG == 9)
        or (resG["edit"][0] == 400 and resG["submit"][0] == 200 and qtyG == 2)
    )
    check("场景G 无 500", okG, f"codes={codesG} status={stG} stock={qtyG}")
    check("场景G 结果一致(编辑要么生效并过账=9、要么被 400 拒绝后按 2 过账)", consistentG,
          f"codes={codesG} status={stG} stock={qtyG}(期望 9 或 2)")

    # ============================================================
    # 场景 H：申请审批 ∥ 手工出库（明细反序）——跨流程必须使用同一 Stock.id 锁顺序
    # ============================================================
    print("\n--- 场景 H：申请审批 与 手工出库反序并发 ---")
    req("POST","/api/goods/",{"barcode":"G007","name":"物料7","price":70}, admin)
    req("POST","/api/goods/",{"barcode":"G008","name":"物料8","price":80}, admin)
    req("POST","/api/locations/",{"warehouse_id":W1,"location_code":"L9","name":"库位9"}, admin)
    req("POST","/api/locations/",{"warehouse_id":W1,"location_code":"L10","name":"库位10"}, admin)
    req("POST","/api/inventory/scan",{"goods_barcode":"G007","location_code":"L9","type":"入库","quantity":10}, op)
    req("POST","/api/inventory/scan",{"goods_barcode":"G008","location_code":"L10","type":"入库","quantity":10}, op)
    s, appH = req("POST", "/api/requests/", {
        "applicant_name":"并发申请", "contact":"pg-h@test.com", "description":"跨流程锁顺序",
        "warehouse_id":W1,
        "items":[{"barcode":"G007","quantity":1},{"barcode":"G008","quantity":1}],
    }); RH=(appH or {}).get("id")
    s, outH = req("POST","/api/outbound-orders/",{"customer":"并发出库"}, admin); OH=(outH or {}).get("id")
    # 与申请单货物顺序相反
    req("POST", f"/api/outbound-orders/{OH}/items", {"goods_barcode":"G008","location_code":"L10","quantity":1}, admin)
    req("POST", f"/api/outbound-orders/{OH}/items", {"goods_barcode":"G007","location_code":"L9","quantity":1}, admin)
    check("场景H 申请单与反序手工出库单就绪", RH and OH, f"request={RH} outbound={OH}")

    resH = {}
    barH = threading.Barrier(2)
    def approveH():
        barH.wait()
        resH["approve"] = req("POST", f"/api/requests/{RH}/status", {"status":"approved"}, admin)
    def submitH():
        barH.wait()
        resH["outbound"] = req("POST", f"/api/outbound-orders/{OH}/submit", {}, admin)
    t1 = threading.Thread(target=approveH); t2 = threading.Thread(target=submitH)
    t1.start(); t2.start(); t1.join(); t2.join()
    qH7, _ = stock_qty_of("G007", "L9")
    qH8, _ = stock_qty_of("G008", "L10")
    codesH = {k: v[0] for k, v in resH.items()}
    check("场景H 两条跨流程并发均成功（无 deadlock/500）",
          codesH == {"approve": 200, "outbound": 200}, f"codes={codesH} bodies={resH}")
    check("场景H 两种流程各扣1，G007/G008 最终均为8",
          qH7 == 8 and qH8 == 8, f"G007={qH7} G008={qH8}")

    # ============================================================
    # 场景 I：申请审批 ∥ 扫码出库——两条路径都必须先锁库存、再锁 OUT 号段
    # ============================================================
    print("\n--- 场景 I：申请审批 与 扫码出库并发 ---")
    req("POST","/api/goods/",{"barcode":"G009","name":"物料9","price":90}, admin)
    req("POST","/api/locations/",{"warehouse_id":W1,"location_code":"L11","name":"库位11"}, admin)
    req("POST","/api/inventory/scan",{"goods_barcode":"G009","location_code":"L11","type":"入库","quantity":10}, op)
    s, appI = req("POST", "/api/requests/", {
        "applicant_name":"扫码并发", "contact":"pg-i@test.com", "description":"库存锁与号段锁顺序",
        "warehouse_id":W1, "items":[{"barcode":"G009","quantity":1}],
    }); RI=(appI or {}).get("id")
    check("场景I 申请单与扫码库存就绪", RI, f"request={RI} stock={stock_qty_of('G009','L11')[0]}")

    resI = {}
    barI = threading.Barrier(2)
    def approveI():
        barI.wait()
        resI["approve"] = req("POST", f"/api/requests/{RI}/status", {"status":"approved"}, admin)
    def scanI():
        barI.wait()
        resI["scan"] = req("POST", "/api/inventory/scan", {
            "goods_barcode":"G009", "location_code":"L11", "type":"出库", "quantity":1,
        }, op)
    t1 = threading.Thread(target=approveI); t2 = threading.Thread(target=scanI)
    t1.start(); t2.start(); t1.join(); t2.join()
    qI, _ = stock_qty_of("G009", "L11")
    codesI = {k: v[0] for k, v in resI.items()}
    check("场景I 审批与扫码出库均成功（无号段锁/库存锁死锁）",
          codesI == {"approve": 200, "scan": 200}, f"codes={codesI} bodies={resI}")
    check("场景I 两条路径各扣1，G009 最终为8", qI == 8, f"G009={qI}")

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
