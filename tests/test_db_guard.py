#!/usr/bin/env python3
"""测试库安全护栏：运行回归/并发测试前，校验目标库是"可丢弃的空测试库"。

回归/并发测试会对目标库做破坏性操作：
  - test_suite.py 会删除 config 表的 ldap_* 行（LDAP 撤销场景）
  - 两个套件都会新建/删除仓库、货物、库位、库存、单据数据
  - pg_concurrency.py 还会 DROP stock 的 (仓库,货物,库位) 复合唯一约束

因此这些脚本**绝不能**指向生产库或含真实数据的库。护栏规则：
  1. 数据库名在黑名单（warehouse_db=已知生产库名、postgres=默认维护库）→ 直接拒绝，
     **任何环境变量都无法豁免**；
  2. 核心业务表（用户/仓库/货物/库位/库存/配置/单据）任一有数据 → 拒绝，
     除非显式设置 WMS_ALLOW_NONEMPTY_TEST_DB=1（表示已确认目标库可整体丢弃）；
  3. 无法连接目标库 → 无法确认其为空库，安全起见拒绝（fail closed）；
  4. SQLite / PostgreSQL 方言无关。

CLI 用法：
  python tests/test_db_guard.py <DATABASE_URL> [--allow-users-row]
    --allow-users-row：服务已自举 admin 之后调用时用（users 表允许 1 行管理员）
"""
import os
import sys

# 已知生产库名 / 默认维护库 —— 硬性黑名单（WMS_ALLOW_NONEMPTY_TEST_DB 不豁免）
BLOCKED_DB_NAMES = {"warehouse_db", "postgres"}

# 核心业务表：任一非空即视为"不是空测试库"
CORE_TABLES = [
    "users", "warehouses", "goods", "locations", "stock", "config",
    "inbound_order_header", "outbound_order_header", "check_order_header",
]

class GuardError(Exception):
    pass

def guard(url, include_users=True):
    """校验目标库为空测试库；不通过则抛 GuardError。返回 (db_name, nonempty) 供日志。"""
    from sqlalchemy.engine import make_url
    from sqlalchemy import create_engine, inspect, text

    u = make_url(url)
    name = u.database or ""
    if u.drivername.startswith("sqlite"):
        name = os.path.basename(name) if name else ""

    if name in BLOCKED_DB_NAMES:
        raise GuardError(
            f"目标库名 '{name}' 在黑名单中（已知生产库/默认维护库），测试脚本拒绝使用。\n"
            "      请另建一个可整体丢弃的测试数据库（建议配受限账号，仅授权该测试库）后重试。")

    if os.environ.get("WMS_ALLOW_NONEMPTY_TEST_DB") == "1":
        print("!! 警告: WMS_ALLOW_NONEMPTY_TEST_DB=1 —— 已跳过非空库检查。请确认目标库可整体丢弃。")
        return name, []

    eng = create_engine(url)
    try:
        try:
            tables = set(inspect(eng).get_table_names())
            nonempty = []
            for t in CORE_TABLES:
                if not include_users and t == "users":
                    continue
                if t not in tables:
                    continue
                with eng.connect() as conn:
                    n = conn.execute(text(f'SELECT count(*) FROM "{t}"')).fetchone()[0]
                if n:
                    nonempty.append(f"{t}({n}行)")
            if nonempty:
                raise GuardError(
                    "目标库不是空测试库，拒绝执行（本套测试会删除 LDAP 配置行、建删业务数据，"
                    "并发专项还会 DROP stock 复合唯一约束）。\n"
                    f"      目标库: {name if name else '(未能解析库名；为防凭据泄露不打印完整 URL)'}\n"
                    f"      非空表: {', '.join(nonempty)}\n"
                    "      请另建一个可整体丢弃的空测试数据库（建议配受限账号，仅授权该测试库）后重试。\n"
                    "      如已确认该库可整体丢弃，可设 WMS_ALLOW_NONEMPTY_TEST_DB=1 跳过本检查。")
        except GuardError:
            raise
        except Exception as e:
            # 七轮 P1：只保留异常类型，不打印异常原文——原文可能含连接串/凭据
            raise GuardError(
                f"无法连接目标库以完成安全校验（{type(e).__name__}）。\n"
                "      无法确认目标库是否为空测试库，为安全起见拒绝执行。")
    finally:
        eng.dispose()
    return name, []

def main():
    args = [a for a in sys.argv[1:]]
    allow_users = "--allow-users-row" in args
    args = [a for a in args if a != "--allow-users-row"]
    if len(args) != 1:
        print("用法: python tests/test_db_guard.py <DATABASE_URL> [--allow-users-row]", file=sys.stderr)
        sys.exit(2)
    try:
        name, nonempty = guard(args[0], include_users=not allow_users)
    except GuardError as e:
        print(f"FATAL: 测试库安全护栏拒绝执行\n{e}", file=sys.stderr)
        sys.exit(3)
    print(f"护栏通过: 目标库 '{name}' 为空测试库")

if __name__ == "__main__":
    main()
