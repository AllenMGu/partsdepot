#!/usr/bin/env python3
# PR #1 评审修复项回归测试（本地 sqlite + uvicorn）
# 覆盖：P0-1 出库汇总/并发重复提交 | P0-2 盘点基线冲突 | P1-3 扫码单事务
#      P1-4 LDAP 未配置降级 | P1-5 零仓库新用户 | P1-6 库位管理员专属
#      P1-7 入库编辑 500 | P1-8 单号撞号 | JWT 30min | 管理员自举 | 静态托管
#      五轮：终态单据(已提交/已完成)拒绝一切明细写入(顺序不变量) | 测试库安全护栏
import base64, json, os, re, sqlite3, sys, threading, time, urllib.parse, urllib.request, urllib.error

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

def req(method, path, body=None, token=None, expect_error=False, form=False, headers=None):
    url = BASE + path
    if body is not None:
        data = (urllib.parse.urlencode(body) if form else json.dumps(body)).encode()
    else:
        data = None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/x-www-form-urlencoded" if form else "application/json")
    if token: r.add_header("Authorization", "Bearer " + token)
    for _k, _v in (headers or {}).items(): r.add_header(_k, _v)
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

# ---------- 0a. 测试库安全护栏（防误伤含真实数据的库） ----------
# 本套件会删除 config 表 ldap_* 行、建删业务数据；目标库必须是可丢弃的空测试库。
# 服务自举的 admin 属正常，故 users 表不参与非空判定（include_users=False）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_db_guard import guard as _db_guard, GuardError as _GuardError
try:
    _name, _ne = _db_guard(_db_url(), include_users=False)
except _GuardError as _e:
    print(f"FATAL: 测试库安全护栏拒绝执行\n{_e}")
    sys.exit(3)

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

s, g = req("POST", "/api/goods/", {"barcode": "G001", "name": "测试物料", "spec": "规格-1", "unit": "台", "price": 10}, admin)
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

# ---------- 13b. 五轮：单据终态后任何明细写入 → 400（顺序不变量；并发交错版见 pg_concurrency D~G） ----------
s, b = req("POST", f"/api/inbound-orders/{I7}/items",
           {"goods_barcode": "G001", "location_code": "L3", "quantity": 2}, admin)
check("五轮 入库：已提交单据新增明细 → 400", s == 400, f"status={s} detail={b}")
check("五轮 入库：已提交单据库存未被该请求改变(仍=7)", stock_of_loc("L3") == 7, f"stock={stock_of_loc('L3')}")
iid7 = (it7a or {}).get("id")
s, b = req("PUT", f"/api/inbound-orders/{I7}/items/{iid7}",
           {"goods_barcode": "G001", "location_code": "L3", "quantity": 9}, admin)
check("五轮 入库：已提交单据编辑明细 → 400", s == 400, f"status={s} detail={b}")
s, b = req("POST", f"/api/outbound-orders/{O2}/items",
           {"goods_barcode": "G001", "location_code": "L1", "quantity": 1}, admin)
check("五轮 出库：已提交单据新增明细 → 400", s == 400, f"status={s} detail={b}")
s, b = req("POST", "/api/check-orders/items/", {"header_id": C3, "goods_barcode": "G002",
                                                "location_code": "L4", "check_quantity": 1}, op)
check("五轮 盘点：已完成单据录入明细 → 400", s == 400, f"status={s} detail={b}")
check("五轮 盘点：已完成单据库存未被该请求改变(仍=6)", stock_of("G002", "L4") == 6, f"stock={stock_of('G002','L4')}")

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

# ---------- 15. 货物联动（PR #6）：公开搜索 + 带货物提交 + 归档拷贝 ----------
# 15a. 公开搜索端点（免登录，不得暴露单价）
s, b = req("GET", "/api/public/goods-search?q=")
check("货物搜索：空关键字 → 400", s == 400, f"status={s}")
s, hits = req("GET", "/api/public/goods-search?q=G001")
first = (hits or [None])[0] if isinstance(hits, list) else None
check("货物搜索：条码模糊命中", s == 200 and isinstance(first, dict) and first.get("barcode") == "G001", f"status={s} hits={hits}")
check("货物搜索：返回字段仅 barcode/name/spec/unit/available_stock（无单价）",
      isinstance(first, dict) and set(first.keys()) == {"barcode", "name", "spec", "unit", "available_stock"},
      f"keys={list(first.keys()) if isinstance(first, dict) else first}")
# 可用库存（产品需求：申请页选备件时显示可用库存）= 该货物全仓库存合计
s_stock, stock_rows = req("GET", "/api/stock/?goods_barcode=G001", token=admin)
_total = sum(r.get("quantity", 0) for r in (stock_rows or []) if isinstance(r, dict) and r.get("goods_barcode") == "G001")
check("货物搜索：available_stock = 当前全仓库存合计",
      isinstance(first, dict) and abs(float(first.get("available_stock") or 0) - _total) < 1e-6,
      f"search={first} stock_total={_total}")
s, hits = req("GET", "/api/public/goods-search?q=" + urllib.parse.quote("测试物料"))
check("货物搜索：名称搜索命中", s == 200 and isinstance(hits, list) and any(h.get("barcode") == "G001" for h in hits), f"status={s} hits={hits}")
s, hits = req("GET", "/api/public/goods-search?q=does-not-exist-xyz")
check("货物搜索：无匹配 → 空列表", s == 200 and hits == [], f"status={s} hits={hits}")
# 15b. 搜索限流：独立 IP 桶（X-Real-IP 模拟另一客户端），1 分钟内 30 次/IP
_rl_headers = {"X-Real-IP": "9.9.9.99"}
_rl_codes = [req("GET", "/api/public/goods-search?q=G001", headers=_rl_headers)[0] for _ in range(31)]
check("货物搜索限流：同一 IP 第 31 次 → 429", _rl_codes[:30] == [200] * 30 and _rl_codes[30] == 429, f"codes={_rl_codes}")
# 15c. 带货物提交（免登录，多行明细）+ 管理员可见 + 后端校验
# 契约：每行只传 条码+数量；名称/规格/单位由后端按条码查库填充（防伪造）
def _first_item(r):
    items = (r or {}).get("items")
    return items[0] if isinstance(items, list) and items else {}

s, b = req("POST", "/api/requests/", {
    "applicant_name": "货物测试", "contact": "user@example.com",
    "description": "带货物申请", "warehouse_id": W1,
    "items": [{"barcode": "G001", "quantity": 2}]})
check("带货物提交：明细行 条码+数量 → 201", s == 201 and isinstance(b, dict) and b.get("id"), f"status={s} body={b}")
rid = (b or {}).get("id")
check("带货物提交：响应含 id/reference/message", isinstance(b, dict) and b.get("id") and b.get("reference") and b.get("message"), f"body={b}")
s, b = req("POST", "/api/requests/", {
    "applicant_name": "货物测试", "contact": "user@example.com",
    "description": "多行申请", "items": [{"barcode": "G001", "quantity": 1}, {"barcode": "G001", "quantity": 3}]})
check("带货物提交：2 个明细行 → 201（多行）", s == 201, f"status={s} body={b}")
_multi_id = (b or {}).get("id")
s, b = req("POST", "/api/requests/", {
    "applicant_name": "货物测试", "contact": "user@example.com",
    "description": "x", "items": [{"barcode": "G001"}]})
check("带货物提交：有条码无数量 → 422", s == 422, f"status={s} body={b}")
s, b = req("POST", "/api/requests/", {
    "applicant_name": "货物测试", "contact": "user@example.com",
    "description": "x", "items": [{"barcode": "G001", "quantity": 0}]})
check("带货物提交：数量=0 → 422（gt=0）", s == 422, f"status={s} body={b}")
s, b = req("POST", "/api/requests/", {
    "applicant_name": "货物测试", "contact": "user@example.com",
    "description": "x", "items": [{"quantity": 3}]})
check("带货物提交：有数量无条码 → 422（防脏数据）", s == 422, f"status={s} body={b}")
s, b = req("POST", "/api/requests/", {
    "applicant_name": "货物测试", "contact": "13900001111",
    "description": "邮箱校验"})
check("邮箱校验：手机号 → 422", s == 422, f"status={s} body={b}")
s, b = req("POST", "/api/requests/", {
    "applicant_name": "货物测试", "contact": "abc",
    "description": "邮箱校验"})
check("邮箱校验：任意文本 → 422", s == 422, f"status={s} body={b}")
s, b = req("POST", "/api/requests/", {
    "applicant_name": "货物测试", "contact": "user@example.com",
    "description": "x", "items": [{"barcode": "ZZ-NO-SUCH-BARCODE", "quantity": 1}]})
check("带货物提交：不存在的条码 → 422（防假条码）", s == 422, f"status={s} body={b}")
s, b = req("POST", "/api/requests/", {
    "applicant_name": "货物测试", "contact": "user@example.com",
    "description": "防伪造", "items": [{"barcode": "G001", "quantity": 1,
    "name": "伪造名称", "spec": "伪造规格", "unit": "伪造单位"}]})
check("防伪造：真实条码+客户端假名称 → 仍 201（多余字段被忽略）", s == 201, f"status={s} body={b}")
_spoof_id = (b or {}).get("id")
s, rows = req("GET", "/api/requests/", token=admin)
_r = next((r for r in (rows or []) if isinstance(r, dict) and r.get("id") == rid), None)
check("管理员列表：货物明细可见（后端按条码查库填充）",
      isinstance(_r, dict) and isinstance(_r.get("items"), list) and len(_r["items"]) == 1
      and _first_item(_r).get("barcode") == "G001" and _first_item(_r).get("name") == "测试物料"
      and _first_item(_r).get("unit") == "台" and _first_item(_r).get("quantity") == 2, f"row={_r}")
_rmulti = next((r for r in (rows or []) if isinstance(r, dict) and r.get("id") == _multi_id), None)
check("管理员列表：多行明细 2 条且数量正确",
      isinstance(_rmulti, dict) and isinstance(_rmulti.get("items"), list) and len(_rmulti["items"]) == 2
      and [i.get("quantity") for i in _rmulti["items"]] == [1, 3], f"row={_rmulti}")
_r2 = next((r for r in (rows or []) if isinstance(r, dict) and r.get("id") == _spoof_id), None)
check("防伪造：落库名称=数据库真实名称，客户端伪造字段被忽略",
      isinstance(_r2, dict) and isinstance(_r2.get("items"), list)
      and _first_item(_r2).get("name") == "测试物料" and _first_item(_r2).get("spec") == "规格-1"
      and _first_item(_r2).get("unit") == "台", f"row={_r2}")
# v2 仓库字段：指定仓库的申请单在列表响应中带 warehouse_id + warehouse_name
check("仓库字段：列表行含 warehouse_id/warehouse_name（一号仓）",
      isinstance(_r, dict) and _r.get("warehouse_id") == W1 and _r.get("warehouse_name") == "一号仓",
      f"row={ {k: _r.get(k) for k in ('id','warehouse_id','warehouse_name')} if isinstance(_r, dict) else _r }")
# 公开仓库列表端点（申请页仓库下拉用）：仅 id/code/name 非敏感字段
s, whs = req("GET", "/api/public/warehouses")
check("仓库接口：public/warehouses 返回 W1/W2，字段仅 id/code/name",
      s == 200 and isinstance(whs, list) and len(whs) == 2
      and all(set(w.keys()) == {"id", "code", "name"} for w in whs)
      and any(w.get("id") == W1 and w.get("name") == "一号仓" for w in whs), f"status={s} whs={whs}")
s, whs = req("GET", "/api/public/warehouses", headers={"X-Real-IP": "9.9.9.98"})
check("仓库接口：X-Real-IP 限流桶独立（另一 IP 不受影响）", s == 200 and isinstance(whs, list), f"status={s}")
# 15d. 归档拷贝：回拨提交时间越过阈值(30天) → 通过 → 立即归档
# v2：通过 = 从申请仓库（W1）扣减库存——审批前后 W1 的 G001 库存恰好减少 2
from datetime import datetime as _dt, timedelta as _td
_old = (_dt.now() - _td(days=40)).strftime("%Y-%m-%d %H:%M:%S")

def _w1_g001_total():
    s_r, r_rows = req("GET", "/api/stock/?goods_barcode=G001", token=admin)
    return sum(r.get("quantity", 0) for r in (r_rows or [])
               if isinstance(r, dict) and r.get("goods_barcode") == "G001" and r.get("warehouse_id") == W1)

_w1_before = _w1_g001_total()
check("扣库存前置：W1 现有 G001 库存 ≥ 2（该申请可通过）", _w1_before >= 2, f"w1_total={_w1_before}")
db_execute("update requests set create_time = :ts where id = :i", {"ts": _old, "i": rid})
s, b = req("POST", f"/api/requests/{rid}/status", {"status": "approved"}, admin)
check("归档测试：通过带货物的申请单（通过即扣库存）", s == 200, f"status={s} body={b}")
check("状态更新响应：items 为真实明细（契约一致，非空数组）",
      s == 200 and isinstance(b, dict) and isinstance(b.get("items"), list) and len(b["items"]) == 1
      and b["items"][0].get("barcode") == "G001" and b["items"][0].get("quantity") == 2, f"body={b}")
_w1_after = _w1_g001_total()
check("扣库存：通过后 W1 G001 库存恰好减少 2", abs((_w1_before - _w1_after) - 2) < 1e-6,
      f"before={_w1_before} after={_w1_after}")
_recs = db_execute(
    "SELECT quantity, remark FROM inventory_records WHERE type='OUT' AND remark LIKE '%通过扣减%'")
check("扣库存：写出库流水（数量=2，备注含申请单号 APP-）",
      bool(_recs) and any(abs(r[0] - 2) < 1e-6 and "APP-" in (r[1] or "") for r in _recs),
      f"records={_recs}")
# 状态机：重复通过被拦截（409），且库存不再变动
s, b = req("POST", f"/api/requests/{rid}/status", {"status": "approved"}, admin)
check("状态机：重复通过 → 409（已处理不可再处理）", s == 409 and "已处理" in str(b), f"status={s} body={b}")
check("状态机：重复通过被拦截后库存不再变动", _w1_g001_total() == _w1_after, f"now={_w1_g001_total()} expect={_w1_after}")
s, b = req("POST", "/api/requests/archive-now", {}, admin)
check("归档测试：立即归档 → archived ≥ 1", s == 200 and isinstance(b, dict) and (b.get("archived") or 0) >= 1, f"status={s} body={b}")
s, arows = req("GET", "/api/requests/archive/?page=1&page_size=10", token=admin)
_a = next((a for a in (arows or []) if isinstance(a, dict) and a.get("original_id") == rid), None)
check("归档拷贝：货物明细字段完整",
      isinstance(_a, dict) and isinstance(_a.get("items"), list) and len(_a["items"]) == 1
      and _first_item(_a).get("barcode") == "G001" and _first_item(_a).get("name") == "测试物料"
      and _first_item(_a).get("spec") == "规格-1" and _first_item(_a).get("unit") == "台"
      and _first_item(_a).get("quantity") == 2, f"row={_a}")
check("归档拷贝：仓库字段保留（warehouse_id/warehouse_name）",
      isinstance(_a, dict) and _a.get("warehouse_id") == W1 and _a.get("warehouse_name") == "一号仓",
      f"row={ {k: _a.get(k) for k in ('original_id','warehouse_id','warehouse_name')} if isinstance(_a, dict) else _a }")
s, rows = req("GET", "/api/requests/", token=admin)
check("归档：该申请单已从近期列表移走", all(isinstance(r, dict) and r.get("id") != rid for r in (rows or [])), f"rows={[r.get('id') for r in (rows or [])]}")
_orphan = db_execute("SELECT COUNT(*) FROM request_items WHERE request_id = :rid", {"rid": rid})
check("P1 回归：归档后 request_items 无孤儿行（原单明细已删，归档表有拷贝）",
      _orphan and _orphan[0][0] == 0, f"orphan_rows={_orphan}")
# 15e. 归档列表分页参数生效
s, a1 = req("GET", "/api/requests/archive/?page=1&page_size=1", token=admin)
check("归档分页：page_size=1 → 恰好 1 条", s == 200 and isinstance(a1, list) and len(a1) == 1, f"status={s} len={len(a1) if isinstance(a1, list) else a1}")

# 15f. v2 通过即扣库存：不足拦截 / 多仓未指定 / 驳回不动库存 / 无明细仅留痕
# 注：15c 已用掉本 IP 提交限流额度（10 次/10 分钟），新增提交走不同 X-Real-IP 桶
_H1 = {"X-Real-IP": "8.8.8.81"}
_H2 = {"X-Real-IP": "8.8.8.82"}
_H3 = {"X-Real-IP": "8.8.8.83"}
_H4 = {"X-Real-IP": "8.8.8.84"}
_w1_floor = _w1_g001_total()

# 库存不足：申请量远超 W1 现有 → 通过被拒（400），库存不变，申请单仍待处理
s, b = req("POST", "/api/requests/", {
    "applicant_name": "库存测试", "contact": "stock@test.com",
    "description": "超额申请", "warehouse_id": W1,
    "items": [{"barcode": "G001", "quantity": 999999}]}, headers=_H1)
check("不足拦截：超额申请可提交（201，待处理）", s == 201 and isinstance(b, dict) and b.get("id"), f"status={s} body={b}")
_over_id = (b or {}).get("id")
s, b = req("POST", f"/api/requests/{_over_id}/status", {"status": "approved"}, admin)
check("不足拦截：通过被拒（400，信息含需要/现有）",
      s == 400 and "库存不足" in str(b) and "需要" in str(b) and "现有" in str(b), f"status={s} body={b}")
check("不足拦截：库存不变", _w1_g001_total() == _w1_floor, f"now={_w1_g001_total()} floor={_w1_floor}")
s, rows = req("GET", "/api/requests/", token=admin)
_over_row = next((r for r in (rows or []) if isinstance(r, dict) and r.get("id") == _over_id), None)
check("不足拦截：申请单仍为待处理（可再处理）",
      isinstance(_over_row, dict) and _over_row.get("status") == "pending", f"row={_over_row}")

# 多仓（W1/W2 均启用）+ 申请单未指定仓库 → 无法确定扣哪个仓，通过被拒
s, b = req("POST", "/api/requests/", {
    "applicant_name": "仓库测试", "contact": "wh@test.com",
    "description": "未指定仓库的申请",
    "items": [{"barcode": "G001", "quantity": 1}]}, headers=_H2)
check("多仓拦截：未指定仓库的申请可提交（201）", s == 201 and isinstance(b, dict) and b.get("id"), f"status={s} body={b}")
_nowh_id = (b or {}).get("id")
s, b = req("POST", f"/api/requests/{_nowh_id}/status", {"status": "approved"}, admin)
check("多仓拦截：多启用仓且未指定仓库 → 通过被拒（400，提示重新提交）",
      s == 400 and "仓库" in str(b) and "无法确定" in str(b), f"status={s} body={b}")
check("多仓拦截：库存不变", _w1_g001_total() == _w1_floor, f"now={_w1_g001_total()}")

# 驳回：不动库存；重复驳回被拦截（409）
s, b = req("POST", "/api/requests/", {
    "applicant_name": "驳回测试", "contact": "rej@test.com",
    "description": "驳回流程", "warehouse_id": W1,
    "items": [{"barcode": "G001", "quantity": 1}]}, headers=_H3)
check("驳回：申请可提交（201）", s == 201 and isinstance(b, dict) and b.get("id"), f"status={s} body={b}")
_rej_id = (b or {}).get("id")
s, b = req("POST", f"/api/requests/{_rej_id}/status", {"status": "rejected"}, admin)
check("驳回：驳回成功（200）且库存不变",
      s == 200 and _w1_g001_total() == _w1_floor, f"status={s} body={b} stock={_w1_g001_total()}")
s, b = req("POST", f"/api/requests/{_rej_id}/status", {"status": "rejected"}, admin)
check("状态机：重复驳回 → 409", s == 409 and "已处理" in str(b), f"status={s} body={b}")

# 无货物明细：通过仅留痕，不动库存、不写流水
s, b = req("POST", "/api/requests/", {
    "applicant_name": "无明细", "contact": "ni@test.com",
    "description": "无货物明细", "warehouse_id": W1}, headers=_H4)
check("无明细：申请可提交（201）", s == 201 and isinstance(b, dict) and b.get("id"), f"status={s} body={b}")
_ni_id = (b or {}).get("id")
s, b = req("POST", f"/api/requests/{_ni_id}/status", {"status": "approved"}, admin)
check("无明细：通过成功（200，仅留痕不动库存）",
      s == 200 and _w1_g001_total() == _w1_floor, f"status={s} body={b} stock={_w1_g001_total()}")

# 15g. 按仓库搜索一致性（评审 P1：申请页库存必须与审批扣减仓库一致）
# 双仓场景：G003 仅在 W2 有库存（W2=5、W1=0）→ 选 W1 搜索必须显示 0，而非全仓合计
s, g3 = req("POST", "/api/goods/", {"barcode": "G003", "name": "双仓测试物料", "price": 5}, admin)
check("双仓：创建 G003", s == 200, f"status={s} body={g3}")
s, b = req("POST", "/api/inventory/scan", {"goods_barcode": "G003", "location_code": "L2", "type": "入库", "quantity": 5}, admin)
check("双仓：G003 入库 5 @W2/L2", s == 200, f"status={s} body={b}")
def _search_stock(barcode, wid=None):
    url = "/api/public/goods-search?q=" + urllib.parse.quote(barcode)
    if wid is not None:
        url += "&warehouse_id=" + str(wid)
    s_r, rows = req("GET", url)
    if s_r != 200 or not isinstance(rows, list):
        return "ERR:" + str(s_r)
    hit = next((r for r in rows if isinstance(r, dict) and r.get("barcode") == barcode), None)
    return float(hit.get("available_stock") or 0) if hit else "MISS"
check("双仓：G003 选 W2 → 可用 5", _search_stock("G003", W2) == 5, f"got={_search_stock('G003', W2)}")
check("双仓：G003 选 W1 → 可用 0（不得显示全仓合计 5）", _search_stock("G003", W1) == 0, f"got={_search_stock('G003', W1)}")
check("双仓：G003 不传仓库（兼容旧客户端）→ 全仓合计 5", _search_stock("G003") == 5, f"got={_search_stock('G003')}")
check("双仓：G001 选 W2 → 可用 0（G001 仅在 W1）", _search_stock("G001", W2) == 0, f"got={_search_stock('G001', W2)}")
s, b = req("GET", "/api/public/goods-search?q=G003&warehouse_id=99999")
check("按仓搜索：不存在的仓库 → 400", s == 400, f"status={s} body={b}")

# 15h. 反序明细双单并发审批（评审 P1/P2：统一锁顺序防死锁）
# A=[G001,G002] B=[G002,G001] 同仓 W1、各 x1：未排序锁序时两单反序持锁可致 PG 死锁→500；
# 修复为 sorted(货物id) 统一全局加锁顺序。PG 上真并发（双线程+屏障）；
# SQLite 单共享连接（StaticPool）无行锁、无死锁形态，顺序执行并断言同一契约。
s, b = req("POST", "/api/inventory/scan", {"goods_barcode": "G001", "location_code": "L1", "type": "入库", "quantity": 10}, admin)
check("反序并发：前置补库 G001@W1 +10", s == 200, f"status={s} body={b}")
def _w1_g002_total():
    s_r, r_rows = req("GET", "/api/stock/?goods_barcode=G002", token=admin)
    return sum(r.get("quantity", 0) for r in (r_rows or [])
               if isinstance(r, dict) and r.get("goods_barcode") == "G002" and r.get("warehouse_id") == W1)
_g1_before = _w1_g001_total()
_g2_before = _w1_g002_total()
check("反序并发：前置 W1 库存充足（G001≥2 且 G002≥2）", _g1_before >= 2 and _g2_before >= 2,
      f"g1={_g1_before} g2={_g2_before}")
_H5 = {"X-Real-IP": "8.8.8.85"}
_H6 = {"X-Real-IP": "8.8.8.86"}
s, bA = req("POST", "/api/requests/", {
    "applicant_name": "并发甲", "contact": "ca@test.com", "description": "反序并发 A",
    "warehouse_id": W1, "items": [{"barcode": "G001", "quantity": 1}, {"barcode": "G002", "quantity": 1}]}, headers=_H5)
check("反序并发：A 单（明细 G001→G002）可提交", s == 201 and isinstance(bA, dict) and bA.get("id"), f"status={s} body={bA}")
_cA = (bA or {}).get("id")
s, bB = req("POST", "/api/requests/", {
    "applicant_name": "并发乙", "contact": "cb@test.com", "description": "反序并发 B",
    "warehouse_id": W1, "items": [{"barcode": "G002", "quantity": 1}, {"barcode": "G001", "quantity": 1}]}, headers=_H6)
check("反序并发：B 单（明细 G002→G001，与 A 反序）可提交", s == 201 and isinstance(bB, dict) and bB.get("id"), f"status={s} body={bB}")
_cB = (bB or {}).get("id")
_is_pg = str(os.environ.get("WMS_DATABASE_URL", "")).startswith("postgres")
def _do_approve(rid, out, key, barrier):
    if barrier is not None:
        barrier.wait()
    out[key] = req("POST", f"/api/requests/{rid}/status", {"status": "approved"}, admin)
if _is_pg:
    _bar = threading.Barrier(2)
    _out = {}
    t1 = threading.Thread(target=_do_approve, args=(_cA, _out, "A", _bar))
    t2 = threading.Thread(target=_do_approve, args=(_cB, _out, "B", _bar))
    t1.start(); t2.start(); t1.join(30); t2.join(30)
    resA = _out.get("A", (-1, {"detail": "线程超时未返回"}))
    resB = _out.get("B", (-1, {"detail": "线程超时未返回"}))
else:
    resA = req("POST", f"/api/requests/{_cA}/status", {"status": "approved"}, admin)
    resB = req("POST", f"/api/requests/{_cB}/status", {"status": "approved"}, admin)
check("反序并发：两单均审批成功（无 500/死锁）",
      resA[0] == 200 and resB[0] == 200, f"A={resA[0]} {resA[1]} B={resB[0]} {resB[1]}")
check("反序并发：两单均为 approved 终态",
      isinstance(resA[1], dict) and resA[1].get("status") == "approved"
      and isinstance(resB[1], dict) and resB[1].get("status") == "approved",
      f"A={resA[1] if isinstance(resA[1], dict) else resA[1]} B={resB[1] if isinstance(resB[1], dict) else resB[1]}")
check("反序并发：G001 两单各扣 1、共扣 2（无丢失更新/超扣）", abs((_g1_before - _w1_g001_total()) - 2) < 1e-6,
      f"before={_g1_before} after={_w1_g001_total()}")
check("反序并发：G002 两单各扣 1、共扣 2（无丢失更新/超扣）", abs((_g2_before - _w1_g002_total()) - 2) < 1e-6,
      f"before={_g2_before} after={_w1_g002_total()}")
check("反序并发：审批响应含 warehouse_name（与列表契约一致）",
      isinstance(resA[1], dict) and resA[1].get("warehouse_name") == "一号仓"
      and isinstance(resB[1], dict) and resB[1].get("warehouse_name") == "一号仓",
      f"A={resA[1].get('warehouse_name') if isinstance(resA[1], dict) else '-'} B={resB[1].get('warehouse_name') if isinstance(resB[1], dict) else '-'}")

# 15i. 提交后仓库被停用 → 审批拒绝（评审 P2：审批时再次校验启用状态）
_H7 = {"X-Real-IP": "8.8.8.87"}
_floor_i = _w1_g001_total()
s, b = req("POST", "/api/requests/", {
    "applicant_name": "停用测试", "contact": "off@test.com", "description": "停用仓库审批",
    "warehouse_id": W1, "items": [{"barcode": "G001", "quantity": 1}]}, headers=_H7)
check("停用：启用时提交成功（201）", s == 201 and isinstance(b, dict) and b.get("id"), f"status={s} body={b}")
_off_id = (b or {}).get("id")
s, b = req("PUT", f"/api/warehouses/{W1}", {"is_active": False}, admin)
check("停用：W1 停用成功", s == 200 and (b or {}).get("is_active") in (False, 0), f"status={s} body={b}")
s, b = req("POST", f"/api/requests/{_off_id}/status", {"status": "approved"}, admin)
check("停用：审批被拒（400，提示已停用）", s == 400 and "停用" in str(b), f"status={s} body={b}")
check("停用：库存不变", _w1_g001_total() == _floor_i, f"now={_w1_g001_total()} floor={_floor_i}")
s, b = req("GET", "/api/public/goods-search?q=G001&warehouse_id=" + str(W1))
check("停用：按停用仓库搜索 → 400", s == 400, f"status={s} body={b}")
s, rows = req("GET", "/api/requests/", token=admin)
_off_row = next((r for r in (rows or []) if isinstance(r, dict) and r.get("id") == _off_id), None)
check("停用：申请单仍为待处理（可改仓重提/再处理）",
      isinstance(_off_row, dict) and _off_row.get("status") == "pending", f"row={_off_row}")
s, b = req("PUT", f"/api/warehouses/{W1}", {"is_active": True}, admin)
check("停用：恢复 W1 启用（环境复原）", s == 200 and (b or {}).get("is_active") in (True, 1), f"status={s} body={b}")

# 15j. 批量按仓库存查询（评审 P2 跟进：切仓批量刷新，1 次调用替代 N 次 goods-search）
def _lookup(wid, codes):
    return req("POST", "/api/public/stock-lookup", {
        "warehouse_id": wid,
        "barcodes": codes.split(",") if isinstance(codes, str) else codes,
    })
def _lk(rows, code):
    hit = next((r for r in (rows or []) if isinstance(r, dict) and r.get("barcode") == code), None)
    return float(hit.get("available_stock") or 0) if hit else "MISS"
s, rows = _lookup(W2, "G003,G001")
check("批量：W2 [G003,G001] → 200 且按输入顺序返回", s == 200 and isinstance(rows, list) and [r.get("barcode") for r in rows] == ["G003", "G001"], f"status={s} rows={rows}")
check("批量：W2 G003 → 5（与按仓搜索一致）", _lk(rows, "G003") == 5, f"got={_lk(rows, 'G003')}")
check("批量：W2 G001 → 0（G001 仅在 W1，不得串仓）", _lk(rows, "G001") == 0, f"got={_lk(rows, 'G001')}")
check("批量：字段仅 barcode/available_stock（无名称/单价泄露）",
      isinstance(rows, list) and rows and all(set(r.keys()) == {"barcode", "available_stock"} for r in rows),
      f"keys={list(rows[0].keys()) if rows else '-'}")
_w1_g1_now = _w1_g001_total()
s, rows = _lookup(W1, "G001,G003")
check("批量：W1 G001 → 与 W1 实际库存一致", s == 200 and _lk(rows, "G001") == _w1_g1_now, f"got={_lk(rows, 'G001')} actual={_w1_g1_now}")
check("批量：W1 G003 → 0（与按仓搜索一致）", _lk(rows, "G003") == 0, f"got={_lk(rows, 'G003')}")
s, rows = _lookup(W2, "G003,G003")
check("批量：重复条码去重（输入序保留）", s == 200 and isinstance(rows, list) and [r.get("barcode") for r in rows] == ["G003"], f"rows={rows}")
s, rows = _lookup(W2, "G003,NOPE-999")
check("批量：未知条码 → 200 且 available_stock=0", s == 200 and _lk(rows, "NOPE-999") == 0, f"status={s} rows={rows}")
s, b = _lookup(99999, "G001")
check("批量：不存在的仓库 → 400", s == 400, f"status={s} body={b}")
s, b = _lookup(W2, "")
check("批量：空条码 → 422", s == 422, f"status={s} body={b}")
s, b = _lookup(W2, ",,")
check("批量：空白条码 → 422", s == 422, f"status={s} body={b}")
s, b = _lookup(W2, ",".join(["B%03d" % i for i in range(201)]))
check("批量：超过 200 条码 → 422", s == 422, f"status={s} body={b}")
s, b = _lookup(W2, "B" * 101)
check("批量：单条码超 100 字符 → 422", s == 422, f"status={s} body={b}")
s, b = req("GET", "/api/public/stock-lookup?warehouse_id=" + str(W2) + "&barcodes=G003")
check("批量：旧 GET 接口已关闭（避免超长 URL）", s in (404, 405), f"status={s} body={b}")
s, b = req("PUT", f"/api/warehouses/{W2}", {"is_active": False}, admin)
check("批量：停用 W2 前置成功", s == 200, f"status={s} body={b}")
s, b = _lookup(W2, "G003")
check("批量：停用仓库 → 400（与按仓搜索一致）", s == 400, f"status={s} body={b}")
s, b = req("PUT", f"/api/warehouses/{W2}", {"is_active": True}, admin)
check("批量：恢复 W2 启用（环境复原）", s == 200 and (b or {}).get("is_active") in (True, 1), f"status={s} body={b}")
# 限流：与 goods-search 同桶；50 个唯一条码每次消耗 5 配额，独立别名 IP 验证
_rl2_headers = {"X-Real-IP": "8.8.8.88"}
_rl2_body = {"warehouse_id": W2, "barcodes": ["ENUM-%02d" % i for i in range(50)]}
_rl2_codes = [req("POST", "/api/public/stock-lookup", _rl2_body, headers=_rl2_headers)[0] for _ in range(7)]
check("批量限流：50 条计 5 配额，同一 IP 第 7 次 → 429", _rl2_codes[:6] == [200] * 6 and _rl2_codes[6] == 429, f"codes={_rl2_codes}")

# 15k. 审批通过 → 自动生成出库单（产品需求：确认后出库必须有出库单）
import re as _re
_HK1 = {"X-Real-IP": "8.8.8.91"}
_HK2 = {"X-Real-IP": "8.8.8.92"}

def _outbound_count():
    s_r, r_rows = req("GET", "/api/outbound-orders/", token=admin)
    return len(r_rows) if isinstance(r_rows, list) else -1

_before_cnt = _outbound_count()
_w1_before_k = _w1_g001_total()
s, b = req("POST", "/api/requests/", {
    "applicant_name": "出库单测试", "contact": "out@test.com",
    "description": "审批通过生成出库单", "warehouse_id": W1,
    "items": [{"barcode": "G001", "quantity": 2}]}, headers=_HK1)
check("出库单：带货物申请可提交（201）", s == 201 and isinstance(b, dict) and b.get("id"), f"status={s} body={b}")
_kid = (b or {}).get("id")
s, b = req("POST", f"/api/requests/{_kid}/status", {"status": "approved"}, admin)
_k_no = (b or {}).get("outbound_order_no") if isinstance(b, dict) else None
check("出库单：审批通过 → 200，响应含 outbound_order_no（OUT+日期+3位序号）",
      s == 200 and bool(_re.fullmatch(r"OUT\d{11}", _k_no or "")), f"status={s} no={_k_no} body={b}")
check("出库单：扣减照常（W1 G001 恰减 2）", abs((_w1_before_k - _w1_g001_total()) - 2) < 1e-6,
      f"before={_w1_before_k} after={_w1_g001_total()}")
s, b = req("GET", "/api/outbound-orders/", token=admin)
_kord = next((o for o in (b or []) if isinstance(o, dict) and o.get("order_no") == _k_no), None)
check("出库单：出库单列表可见（COMPLETED / 仓库=一号仓 / 客户=申请人）",
      isinstance(_kord, dict) and _kord.get("status") == "COMPLETED"
      and _kord.get("warehouse_id") == W1 and _kord.get("customer") == "出库单测试", f"order={_kord}")
_koid = (_kord or {}).get("id")
s, b = req("GET", f"/api/outbound-orders/{_koid}", token=admin)
_kit = (b or {}).get("items") if isinstance(b, dict) else None
check("出库单：明细与实际扣减一致（G001 x2、单价10、金额20）",
      isinstance(_kit, list) and len(_kit) >= 1
      and all(i.get("goods_barcode") == "G001" for i in _kit)
      and any(abs(i.get("quantity", 0) - 2) < 1e-6 for i in _kit)
      and abs(((b or {}).get("total_amount") or 0) - 20) < 1e-6, f"body={b}")
_recs = db_execute("SELECT remark FROM inventory_records WHERE type='OUT' AND remark LIKE :pat", {"pat": f"%{_k_no}%"})
check("出库单：出库流水备注含出库单号（申请单↔流水↔出库单可追溯）", bool(_recs), f"recs={_recs}")
s, rows = req("GET", "/api/requests/", token=admin)
_krow = next((r for r in (rows or []) if isinstance(r, dict) and r.get("id") == _kid), None)
check("出库单：近期列表行含 outbound_order_no（管理页详情弹窗展示）",
      isinstance(_krow, dict) and _krow.get("outbound_order_no") == _k_no, f"row={_krow}")
# 无明细：通过仅留痕，不生成出库单
s, b = req("POST", "/api/requests/", {
    "applicant_name": "出库单测试", "contact": "out2@test.com",
    "description": "无货物明细", "warehouse_id": W1}, headers=_HK2)
_ni2 = (b or {}).get("id")
s, b = req("POST", f"/api/requests/{_ni2}/status", {"status": "approved"}, admin)
check("出库单：无明细通过仅留痕，不生成出库单（outbound_order_no 为空）",
      s == 200 and ((b or {}).get("outbound_order_no") in (None, "")), f"status={s} body={b}")
check("出库单：无明细审批未新增出库单", _outbound_count() == _before_cnt + 1,
      f"before={_before_cnt} now={_outbound_count()}")
# 库存不足：400 且同事务不留下半成品出库单
s, b = req("POST", "/api/requests/", {
    "applicant_name": "出库单测试", "contact": "out3@test.com",
    "description": "超额", "warehouse_id": W1,
    "items": [{"barcode": "G001", "quantity": 999999}]}, headers=_HK2)
_ov2 = (b or {}).get("id")
s, b = req("POST", f"/api/requests/{_ov2}/status", {"status": "approved"}, admin)
check("出库单：库存不足 → 400 且未生成出库单（整体回滚）",
      s == 400 and _outbound_count() == _before_cnt + 1, f"status={s} body={b} cnt={_outbound_count()}")
# 重复审批：409，仍只有一张出库单（不重复生成）
s, b = req("POST", f"/api/requests/{_kid}/status", {"status": "approved"}, admin)
check("出库单：重复审批 → 409，出库单仍一张（不重单）",
      s == 409 and _outbound_count() == _before_cnt + 1, f"status={s} body={b}")
# 归档：归档行保留出库单号
db_execute("update requests set create_time = :ts where id = :i", {"ts": _old, "i": _kid})
s, b = req("POST", "/api/requests/archive-now", {}, admin)
check("出库单：归档执行成功", s == 200 and (b or {}).get("archived", 0) >= 1, f"status={s} body={b}")
s, arows = req("GET", "/api/requests/archive/?page=1&page_size=50", token=admin)
_ka = next((a for a in (arows or []) if isinstance(a, dict) and a.get("original_id") == _kid), None)
check("出库单：归档行保留 outbound_order_no（归档后详情仍可查）",
      isinstance(_ka, dict) and _ka.get("outbound_order_no") == _k_no, f"row={_ka}")

# 15l. 前端静态守卫：所有页面禁止内联事件处理器（onclick= 等）
# 背景：页面 CSP 仅放行 self/CDN/内联脚本哈希，浏览器会直接拦截内联事件处理器
# （点击无反应、无任何报错，见出库单/入库单"查看"失效故障）。动态按钮一律
# data-act 属性 + 容器事件委托（addEventListener），此守卫防止回归。
import glob as _glob
_bad_inline = []
for _pf in sorted(_glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "*.html"))):
    _ptext = open(_pf, encoding="utf-8").read()
    for _attr in ("onclick", "onsubmit", "onchange", "oninput", "onkeyup", "onkeydown", "onfocus", "onblur", "onload"):
        # 排除 JS 属性赋值（如 window.onload = ...）：属性名前必须是空白/引号等，不能是 . 或标识符字符
        if re.search(r'(?<![\w.-])%s\s*=' % _attr, _ptext, re.I):
            _bad_inline.append(os.path.basename(_pf) + ":" + _attr)
check("前端：所有页面零内联事件处理器（CSP 会静默拦截 onclick= 等，动态按钮须用事件委托）",
      not _bad_inline, f"offenders={_bad_inline}")

# 15m. 小程序申请页必须与 Web 多仓契约同步：加载仓库、按仓搜索、提交 warehouse_id，
# 切仓时使用 POST JSON 批量刷新库存。防止后续只改 Web 又让小程序退回“可提交但无法审批”。
_mini_apply = open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "wechat-miniprogram", "pages", "apply", "index.js"),
    encoding="utf-8",
).read()
_mini_contract = all(token in _mini_apply for token in (
    'url: "/public/warehouses"',
    'data: { q: kw, warehouse_id: this.data.warehouseId }',
    'url: "/public/stock-lookup"',
    'method: "POST"',
    'warehouse_id: this.data.warehouseId',
))
check("小程序：多仓申请契约完整（仓库列表/按仓搜索/POST批量库存/提交warehouse_id）",
      _mini_contract)

# ---------- 汇总 ----------
fails = [r for r in results if not r[1]]
print(f"\n===== 汇总：{len(results)-len(fails)}/{len(results)} 通过 =====")
for name, _, detail in fails:
    print(f"  失败: {name} {detail}")
sys.exit(1 if fails else 0)
