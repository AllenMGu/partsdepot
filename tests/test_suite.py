#!/usr/bin/env python3
# PR #1 评审修复项回归测试（本地 sqlite + uvicorn）
# 覆盖：P0-1 出库汇总/并发重复提交 | P0-2 盘点基线冲突 | P1-3 扫码单事务
#      P1-4 LDAP 未配置降级 | P1-5 零仓库新用户 | P1-6 库位管理员专属
#      P1-7 入库编辑 500 | P1-8 单号撞号 | JWT 30min | 管理员自举 | 静态托管
import base64, json, os, sqlite3, sys, threading, time, urllib.parse, urllib.request, urllib.error

BASE = os.environ.get("WMS_TEST_BASE", "http://127.0.0.1:8091")
DB = os.environ.get("WMS_TEST_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "test.db"))
LOG = os.environ.get("WMS_TEST_LOG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.log"))

def _db_url():
    """DB 可能是裸文件路径（sqlite）或完整 URL（sqlite/postgres），统一成 SQLAlchemy URL。"""
    if "://" in DB:
        return DB
    return "sqlite:///" + os.path.abspath(DB)

def db_execute(sql, params=()):
    """方言无关的直连 DB 执行（SQLite / PostgreSQL 均可），返回结果行。"""
    from sqlalchemy import create_engine, text
    eng = create_engine(_db_url())
    try:
        with eng.begin() as conn:
            res = conn.execute(text(sql), params)
            return res.fetchall() if getattr(res, "returns_rows", True) else []
    finally:
        eng.dispose()

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'} | {name}" + (f" | {detail}" if detail else ""))

def req(method, path, body=None, token=None, expect_error=False, form=False):
    url = BASE + path
    if body is not None:
        data = (urllib.parse.urlencode(body) if form else json.dumps(body)).encode()
    else:
        data = None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/x-www-form-urlencoded" if form else "application/json")
    if token: r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            raw = resp.read().decode()
            try: return resp.status, json.loads(raw)
            except Exception: return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try: parsed = json.loads(raw)
        except Exception: parsed = raw
        return e.code, parsed
    except Exception as e:
        return -1, str(e)

# ---------- 0. 服务就绪 ----------
for _ in range(50):
    try:
        s, _ = req("GET", "/docs")
        if s == 200: break
    except Exception: pass
    time.sleep(0.3)
else:
    print("FATAL: server not ready"); sys.exit(2)

# ---------- 1. 管理员自举 + JWT 30 分钟 ----------
s, b = req("POST", "/api/token", {"username": "admin", "password": "Admin-Test-2026"}, form=True)
check("自举：fresh DB + ADMIN_* 创建管理员并登录", s == 200 and b.get("access_token"), f"status={s}")
admin = b.get("access_token", "")

payload = json.loads(base64.urlsafe_b64decode(admin.split(".")[1] + "=" * (-len(admin.split(".")[1]) % 4)))
drift = abs(payload.get("exp", 0) - (time.time() + 1800))
check("JWT 默认有效期 30 分钟", drift < 120, f"exp距现在 {payload.get('exp',0)-time.time():.0f}s (期望≈1800)")

# ---------- 2. 基础数据 ----------
s, w1 = req("POST", "/api/warehouses/", {"code": "W1", "name": "一号仓"}, admin)
s2, w2 = req("POST", "/api/warehouses/", {"code": "W2", "name": "二号仓"}, admin)
check("创建仓库 W1/W2", s == 200 and s2 == 200)
W1, W2 = (w1 or {}).get("id"), (w2 or {}).get("id")

# 新建仓库会自动授权给所有管理员；自举发生在建仓之前时把 W1 切为管理员当前仓库
s, tok_b = req("POST", "/api/token", {"username": "admin", "password": "Admin-Test-2026"}, form=True)
ADMIN_ID = ((tok_b or {}).get("user") or {}).get("id")
s, b = req("POST", f"/api/users/{ADMIN_ID}/switch-warehouse?warehouse_id={W1}", token=admin)
check("管理员切换当前仓库 W1", s == 200, f"status={s} body={b}")

s, l1 = req("POST", "/api/locations/", {"warehouse_id": W1, "location_code": "L1", "name": "库位1"}, admin)
s2, l2 = req("POST", "/api/locations/", {"warehouse_id": W2, "location_code": "L2", "name": "库位2"}, admin)
check("创建库位 L1(W1)/L2(W2)", s == 200 and s2 == 200)
L1_ID, L2_ID = (l1 or {}).get("id"), (l2 or {}).get("id")

s, g = req("POST", "/api/goods/", {"barcode": "G001", "name": "测试物料", "price": 10}, admin)
check("创建物料 G001(price=10)", s == 200)

s, u = req("POST", "/api/users/", {"username": "op1", "password": "Op-Test-2026",
    "full_name": "操作员", "warehouse_ids": [W1], "role": "operator"}, admin)
check("创建操作员 op1(仅W1)", s == 200, f"status={s}")
s, b = req("POST", "/api/token", {"username": "op1", "password": "Op-Test-2026"}, form=True)
check("op1 登录", s == 200)
op = b.get("access_token", "")

# ---------- 3. P1-6 库位管理权限 ----------
s, b = req("POST", "/api/locations/", {"warehouse_id": W1, "location_code": "L9", "name": "越权库位"}, op)
check("P1-6 操作员新建库位 → 403", s == 403, f"status={s}")
s, b = req("PUT", f"/api/locations/{L1_ID}", {"name": "越权改名"}, op)
check("P1-6 操作员修改库位 → 403", s == 403, f"status={s}")
s, b = req("PUT", f"/api/locations/{L1_ID}", {"warehouse_id": W2}, admin)
check("P1-6 管理员跨仓库改库位 → 400", s == 400, f"status={s} detail={b}")
s, b = req("PUT", f"/api/locations/{L1_ID}", {"name": "库位1-改"}, admin)
check("P1-6 管理员同仓库改名 → 200", s == 200 and (b or {}).get("name") == "库位1-改", f"status={s}")

# ---------- 4. P1-4 LDAP 未配置降级 ----------
s, b = req("POST", "/api/token", {"username": "ghost-ldap", "password": "whatever"}, form=True)
check("P1-4 LDAP 未配置时未知用户登录 → 401(非500)", s == 401, f"status={s}")
s, cfg = req("GET", "/api/ldap/config", token=admin)
vals = {k: v for k, v in (cfg or {}).items() if k.startswith("ldap_")} or {}
no_internal = not any(("10.80.101" in str(v) or "cutiatx" in str(v)) for v in vals.values())
check("P1-4 配置接口无内部环境值", s == 200 and no_internal, f"cfg={cfg}")
n = db_execute("select count(*) from config where key like 'ldap%'")[0][0]
check("P1-4 get_ldap_config 不再写回内置默认值(无ldap_*配置行)", n == 0, f"ldap_* config rows={n}")

# ---------- 5. P1-7 入库单/明细编辑 ----------
s, i1 = req("POST", "/api/inbound-orders/", {"supplier": "SupA", "remark": "r1"}, admin)
check("P1-7 创建入库单 I1", s == 200, f"status={s} body={i1}")
I1 = (i1 or {}).get("id")
s, it1 = req("POST", f"/api/inbound-orders/{I1}/items",
             {"goods_barcode": "G001", "location_code": "L1", "quantity": 5, "unit_price": 12}, admin)
check("P1-7 添加明细(显式单价12)", s == 200, f"status={s} body={it1}")
s, b = req("PUT", f"/api/inbound-orders/{I1}", {"supplier": "SupB", "remark": "r2"}, admin)
check("P1-7 编辑表头不再 500 → 200", s == 200 and (b or {}).get("order_no") == (i1 or {}).get("order_no"),
      f"status={s} body={b}")
s, it2 = req("POST", f"/api/inbound-orders/{I1}/items",
             {"goods_barcode": "G001", "location_code": "L1", "quantity": 3}, admin)
check("P1-7 明细省略单价 → 回退物料价10", s == 200 and (it2 or {}).get("unit_price") == 10,
      f"status={s} body={it2}")
IT2 = (it2 or {}).get("id")
s, b = req("PUT", f"/api/inbound-orders/{I1}/items/{IT2}",
           {"goods_barcode": "G001", "location_code": "L1", "quantity": 4}, admin)
check("P1-7 编辑明细(省略单价)不再 500 → 200", s == 200 and (b or {}).get("total_price") == 40,
      f"status={s} body={b}")
s, lst = req("GET", "/api/inbound-orders/", token=admin)
tot = next((o.get("total_amount") for o in (lst or []) if o.get("id") == I1), None)
check("P1-7 表头总额=5*12+4*10=100", tot == 100, f"total={tot}")

# ---------- 6. P0-1 出库汇总校验 + 重复提交 ----------
def stock_of(barcode="G001", loc="L1"):
    s, rows = req("GET", f"/api/stock/?goods_barcode={barcode}", token=op)
    if not isinstance(rows, list):
        rows = []
    for r in rows:
        if isinstance(r, dict) and r.get("goods_barcode") == barcode and r.get("location_code") == loc:
            return r.get("quantity")
    return 0

s, b = req("POST", "/api/inventory/scan", {"goods_barcode": "G001", "location_code": "L1",
                                           "type": "入库", "quantity": 10}, op)
check("扫码入库 10", s == 200 and stock_of() == 10, f"status={s} stock={stock_of()}")

s, o1 = req("POST", "/api/outbound-orders/", {"customer": "C1"}, admin)
O1 = (o1 or {}).get("id")
req("POST", f"/api/outbound-orders/{O1}/items", {"goods_barcode": "G001", "location_code": "L1", "quantity": 7}, admin)
req("POST", f"/api/outbound-orders/{O1}/items", {"goods_barcode": "G001", "location_code": "L1", "quantity": 7}, admin)
s, b = req("POST", f"/api/outbound-orders/{O1}/submit", {}, admin)
check("P0-1 拆单 7+7 > 库存10 → 400", s == 400, f"status={s} detail={b}")
check("P0-1 拆单失败后库存未变(10)", stock_of() == 10, f"stock={stock_of()}")

s, o2 = req("POST", "/api/outbound-orders/", {"customer": "C2"}, admin)
O2 = (o2 or {}).get("id")
req("POST", f"/api/outbound-orders/{O2}/items", {"goods_barcode": "G001", "location_code": "L1", "quantity": 5}, admin)
s, b = req("POST", f"/api/outbound-orders/{O2}/submit", {}, admin)
check("P0-1 出库 5 → 200", s == 200, f"status={s}")
check("P0-1 库存 10→5", stock_of() == 5, f"stock={stock_of()}")

s, o3 = req("POST", "/api/outbound-orders/", {"customer": "C3"}, admin)
O3 = (o3 or {}).get("id")
req("POST", f"/api/outbound-orders/{O3}/items", {"goods_barcode": "G001", "location_code": "L1", "quantity": 5}, admin)
s, b = req("POST", f"/api/outbound-orders/{O3}/submit", {}, admin)
check("P0-1 出库 5 → 200(库存清零)", s == 200 and stock_of() == 0, f"status={s} stock={stock_of()}")

s, o4 = req("POST", "/api/outbound-orders/", {"customer": "C4"}, admin)
O4 = (o4 or {}).get("id")
req("POST", f"/api/outbound-orders/{O4}/items", {"goods_barcode": "G001", "location_code": "L1", "quantity": 1}, admin)
s, b = req("POST", f"/api/outbound-orders/{O4}/submit", {}, admin)
check("P0-1 库存0出库1 → 400 且不出现负库存", s == 400 and stock_of() == 0, f"status={s} stock={stock_of()}")

s, b = req("POST", f"/api/outbound-orders/{O2}/submit", {}, admin)
check("P0-1 已提交单据重复提交 → 400", s == 400, f"status={s}")

# 并发重复提交
req("POST", "/api/inventory/scan", {"goods_barcode": "G001", "location_code": "L1", "type": "入库", "quantity": 10}, op)
s, o5 = req("POST", "/api/outbound-orders/", {"customer": "C5"}, admin)
O5 = (o5 or {}).get("id")
req("POST", f"/api/outbound-orders/{O5}/items", {"goods_barcode": "G001", "location_code": "L1", "quantity": 5}, admin)
out = {}
def submit_once(k):
    code, body = req("POST", f"/api/outbound-orders/{O5}/submit", {}, admin)
    out[k] = code
ts = [threading.Thread(target=submit_once, args=(i,)) for i in range(2)]
[t.start() for t in ts]; [t.join() for t in ts]
codes = sorted(out.values())
n_success = codes.count(200)
final = stock_of()
expected = 10 - 5 * n_success  # 初始10，每次成功扣5
check("P0-1 并发重复提交不变量：库存非负且扣减与成功数一致",
      final >= 0 and final == expected,
      f"codes={codes} n_success={n_success} final={final} expected={expected} (PG行锁下应恰好1个200→库存5)")

# ---------- 7. P0-2 盘点基线冲突 ----------
s, c1 = req("POST", "/api/check-orders/", {"warehouse_id": W1}, op)
check("创建盘点单 C1", s == 200, f"status={s} body={c1}")
C1 = (c1 or {}).get("id")
s, b = req("POST", "/api/check-orders/items/", {"header_id": C1, "goods_barcode": "G001",
                                                "location_code": "L1", "check_quantity": 8}, op)
check("C1 录入(基线=5, 实盘8)", s == 200, f"status={s} body={b}")
s, b = req("POST", "/api/inventory/scan", {"goods_barcode": "G001", "location_code": "L1",
                                           "type": "入库", "quantity": 2}, op)
check("盘点期间入库 2 → 库存 7", s == 200 and stock_of() == 7, f"stock={stock_of()}")
s, b = req("POST", f"/api/check-orders/{C1}/complete", {}, op)
check("P0-2 基线(5)≠当前(7) → 409 要求重盘", s == 409, f"status={s} detail={b}")
check("P0-2 冲突后库存未被覆盖(仍=7)", stock_of() == 7, f"stock={stock_of()}")
s, b = req("POST", "/api/check-orders/items/", {"header_id": C1, "goods_barcode": "G001",
                                                "location_code": "L1", "check_quantity": 9}, op)
check("重盘录入(新基线=7, 实盘9)", s == 200, f"status={s}")
s, b = req("POST", f"/api/check-orders/{C1}/complete", {}, op)
check("P0-2 无冲突时正常过账 → 库存=9", s == 200 and stock_of() == 9, f"status={s} stock={stock_of()}")

s, c2 = req("POST", "/api/check-orders/", {"warehouse_id": W1}, op)
C2 = (c2 or {}).get("id")
req("POST", "/api/check-orders/items/", {"header_id": C2, "goods_barcode": "G001",
                                         "location_code": "L1", "check_quantity": 2}, op)
s, b = req("POST", f"/api/check-orders/{C2}/complete", {}, op)
check("P0-2 负差异盘点(9→2)正常", s == 200 and stock_of() == 2, f"status={s} stock={stock_of()}")

# ---------- 7b. 四轮：盘点完成"无库存行"建行路径 ----------
# 场景：该 (货物,库位) 从未有库存行（基线=0），盘点实盘 N → 完成时新建库存行；
# 重复完成必须被拒（单头锁串行化 + 状态复核），不得二次过账。
s, g2 = req("POST", "/api/goods/", {"barcode": "G002", "name": "测试物料2", "price": 20}, admin)
check("四轮 创建物料 G002", s == 200, f"status={s} body={g2}")
s, l4 = req("POST", "/api/locations/", {"warehouse_id": W1, "location_code": "L4", "name": "库位4"}, admin)
check("四轮 创建库位 L4(W1)", s == 200, f"status={s} body={l4}")
s, c3 = req("POST", "/api/check-orders/", {"warehouse_id": W1}, op)
C3 = (c3 or {}).get("id")
s, b = req("POST", "/api/check-orders/items/", {"header_id": C3, "goods_barcode": "G002",
                                                "location_code": "L4", "check_quantity": 6}, op)
check("四轮 C3 录入(基线=0 无库存行, 实盘6)", s == 200 and (b or {}).get("actual_quantity") == 0,
      f"status={s} body={b}")
s, b = req("POST", f"/api/check-orders/{C3}/complete", {}, op)
check("四轮 盘点完成新建库存行 → 库存=6", s == 200 and stock_of("G002", "L4") == 6,
      f"status={s} stock={stock_of('G002', 'L4')}")
s, b = req("POST", f"/api/check-orders/{C3}/complete", {}, op)
check("四轮 重复完成同一盘点单 → 400 且不二次过账", s == 400 and stock_of("G002", "L4") == 6,
      f"status={s} detail={b} stock={stock_of('G002', 'L4')}")

# ---------- 8. P1-8 单号取最大尾号 ----------
s, i2 = req("POST", "/api/inbound-orders/", {"supplier": "S2"}, admin)
s2, i3 = req("POST", "/api/inbound-orders/", {"supplier": "S3"}, admin)
check("P1-8 创建 I2/I3", s == 200 and s2 == 200)
I2, I3 = (i2 or {}).get("id"), (i3 or {}).get("id")
s, b = req("DELETE", f"/api/inbound-orders/{I1}", {}, admin)
check("P1-8 删除最早草稿 I1", s == 200, f"status={s} body={b}")
s, i4 = req("POST", "/api/inbound-orders/", {"supplier": "S4"}, admin)
# 取当前所有 IN 单的最大尾号 +1（扫码入库也会占用 IN 号段，故 I2/I3 不一定是 002/003）
s_all, all_in = req("GET", "/api/inbound-orders/", token=admin)
i4_no = (i4 or {}).get("order_no", "")
existing_nos = [o.get("order_no") for o in (all_in or [])
                if isinstance(o, dict) and o.get("order_no") and o.get("order_no") != i4_no]
def suffix(no):
    d = no[2:10]  # IN + 8位日期
    return int(no[10:]) if len(no) > 10 and no[10:].isdigit() else 0
max_existing = max([suffix(n) for n in existing_nos], default=0)
check("P1-8 删除最早草稿后新建=全局最大尾号+1(不复用、不撞号)",
      s == 200 and suffix((i4 or {}).get("order_no", "")) == max_existing + 1
      and (i4 or {}).get("order_no") not in [(i2 or {}).get("order_no"), (i3 or {}).get("order_no")],
      f"I2={(i2 or {}).get('order_no')} I3={(i3 or {}).get('order_no')} I4={(i4 or {}).get('order_no')} max_existing={max_existing}")

# ---------- 9. P1-5 新用户零仓库 ----------
s, u = req("POST", "/api/users/", {"username": "zero1", "password": "Z-Test-2026",
                                   "full_name": "零权限", "warehouse_ids": [], "role": "operator"}, admin)
check("P1-5 创建零仓库用户", s == 200, f"status={s}")
ZERO1 = (u or {}).get("id")
s, b = req("POST", "/api/token", {"username": "zero1", "password": "Z-Test-2026"}, form=True)
check("P1-5 零权限用户可登录", s == 200, f"status={s}")
s, lst = req("GET", f"/api/users/{ZERO1}/warehouses", token=admin)
check("P1-5 零权限用户仓库列表为空", s == 200 and lst == [], f"warehouses={lst}")
ztoken = b.get("access_token", "")
s, b = req("POST", "/api/inventory/scan", {"goods_barcode": "G001", "location_code": "L1",
                                           "type": "入库", "quantity": 1}, ztoken)
check("P1-5 零权限用户扫码 → 403", s == 403, f"status={s} detail={b}")

# ---------- 10. 静态托管 ----------
s, body = req("GET", "/", token=None)
check("README 对齐：GET / 返回前端 HTML", s == 200 and body and "<html" in str(body).lower(), f"status={s}")

# ---------- 11. 三轮 P0：编辑入库明细跨仓归属校验 ----------
s, i5 = req("POST", "/api/inbound-orders/", {"supplier": "SupC"}, admin)
I5 = (i5 or {}).get("id")
s, it5 = req("POST", f"/api/inbound-orders/{I5}/items",
             {"goods_barcode": "G001", "location_code": "L1", "quantity": 2}, admin)
IT5 = (it5 or {}).get("id")
check("三轮P0 建入库单+明细(W1/L1)", s == 200, f"status={s} body={it5}")
s, b = req("PUT", f"/api/inbound-orders/{I5}/items/{IT5}",
           {"goods_barcode": "G001", "location_code": "L2", "quantity": 2}, admin)
check("三轮P0 编辑入库明细到 W2 库位 → 400(拒跨仓)", s == 400, f"status={s} detail={b}")
s, b = req("PUT", f"/api/inbound-orders/{I5}/items/{IT5}",
           {"goods_barcode": "G001", "location_code": "L1", "quantity": 2, "unit_price": 11}, admin)
check("三轮P0 编辑入库明细到同仓库位 → 200", s == 200 and (b or {}).get("total_price") == 22,
      f"status={s} body={b}")

# ---------- 12. 三轮 P1：出库明细编辑（单价可选 + 跨仓校验） ----------
s, o6 = req("POST", "/api/outbound-orders/", {"customer": "C6"}, admin)
O6 = (o6 or {}).get("id")
s, it6 = req("POST", f"/api/outbound-orders/{O6}/items",
             {"goods_barcode": "G001", "location_code": "L1", "quantity": 1, "unit_price": 20}, admin)
IT6 = (it6 or {}).get("id")
check("三轮P1 建出库单+明细(显式单价20)", s == 200, f"status={s} body={it6}")
s, b = req("PUT", f"/api/outbound-orders/{O6}/items/{IT6}",
           {"goods_barcode": "G001", "location_code": "L1", "quantity": 3}, admin)
check("三轮P1 编辑出库明细(省略单价)→ 200(原 500)", s == 200, f"status={s} body={b}")
s, b = req("PUT", f"/api/outbound-orders/{O6}/items/{IT6}",
           {"goods_barcode": "G001", "location_code": "L2", "quantity": 3}, admin)
check("三轮P1 编辑出库明细到 W2 库位 → 400(拒跨仓)", s == 400, f"status={s} detail={b}")

# ---------- 13. 三轮 P1：入库单同一(货物,库位)多条明细 ----------
s, l3 = req("POST", "/api/locations/", {"warehouse_id": W1, "location_code": "L3", "name": "库位3"}, admin)
L3_ID = (l3 or {}).get("id")
check("三轮P1 建新库位 L3(无库存)", s == 200, f"status={s}")
s, i7 = req("POST", "/api/inbound-orders/", {"supplier": "SupD"}, admin)
I7 = (i7 or {}).get("id")
s, it7a = req("POST", f"/api/inbound-orders/{I7}/items",
              {"goods_barcode": "G001", "location_code": "L3", "quantity": 3}, admin)
s2, it7b = req("POST", f"/api/inbound-orders/{I7}/items",
               {"goods_barcode": "G001", "location_code": "L3", "quantity": 4}, admin)
check("三轮P1 同组合加两条明细", s == 200 and s2 == 200, f"a={s} b={s2}")
s, b = req("POST", f"/api/inbound-orders/{I7}/submit", {}, admin)
check("三轮P1 提交同组合两条明细 → 200(原唯一约束 500)", s == 200, f"status={s} detail={b}")
def stock_of_loc(loc):
    s, rows = req("GET", "/api/stock/?goods_barcode=G001", token=op)
    for r in (rows or []):
        if isinstance(r, dict) and r.get("location_code") == loc:
            return r.get("quantity")
    return 0
check("三轮P1 同组合库存 = 3+4 = 7", stock_of_loc("L3") == 7, f"stock={stock_of_loc('L3')}")

# ---------- 14. 三轮 P1：LDAP 配置撤销后不可再用旧值 ----------
s, b = req("PUT", "/api/ldap/config", {
    "ldap_server": "ldap://127.0.0.1:9", "ldap_base_dn": "dc=none,dc=none",
    "ldap_admin_dn": "cn=none", "ldap_admin_password": "none",
    "ldap_user_search_filter": "(&(uid={}))"}, admin)
check("三轮P1 写入 LDAP 配置(假服务器)", s == 200, f"status={s} body={b}")
s, cfg = req("GET", "/api/ldap/config", token=admin)
check("三轮P1 配置接口回显已写入值", s == 200 and (cfg or {}).get("ldap_server") == "ldap://127.0.0.1:9", f"cfg={cfg}")
# 未知用户登录：应尝试 LDAP（假服务器连接失败）→ 401 而非 500
s, b = req("POST", "/api/token", {"username": "ghost-3rd", "password": "x"}, form=True)
check("三轮P1 有配置时未知用户登录 → 401(非500)", s == 401, f"status={s}")
# 撤销：删除 DB 中的 ldap_* 配置行
db_execute("delete from config where key like 'ldap%'")
s, cfg = req("GET", "/api/ldap/config", token=admin)
all_none = all((cfg or {}).get(k) in (None, "") for k in
               ["ldap_server","ldap_base_dn","ldap_admin_dn","ldap_admin_password","ldap_user_search_filter"])
check("三轮P1 撤销后配置接口全部为空/None", s == 200 and all_none, f"cfg={cfg}")
s, b = req("POST", "/api/token", {"username": "ghost-3rdb", "password": "x"}, form=True)
check("三轮P1 撤销后未知用户登录仍安全 401", s == 401, f"status={s} detail={b}")

# ---------- 汇总 ----------
fails = [r for r in results if not r[1]]
print(f"\n===== 汇总：{len(results)-len(fails)}/{len(results)} 通过 =====")
for name, _, detail in fails:
    print(f"  失败: {name} {detail}")
sys.exit(1 if fails else 0)
