#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""进程内单元测试：LDAP 配置加载（load_ldap_config_from_db）

黑盒 HTTP 测试无法观察"进程内全局配置"，本脚本直接 import main 模块验证：
1. 撤销（DB 配置清空/删除）且无环境变量时，上一次加载留下的旧全局值
   （旧服务器/凭据/搜索过滤器）必须被清除——每次调用从空值重新构造；
2. 环境变量优先级高于数据库配置。

用法：与 tests/run_regression.sh 相同环境（同一 sqlite DB、无 LDAP_* 环境变量）。
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
DB = os.environ.get("WMS_TEST_DB", os.path.join(HERE, "test.db"))

# DB 可能是裸文件路径（sqlite）或完整 URL（sqlite/postgres），统一成 SQLAlchemy URL
if "://" not in DB:
    DB = "sqlite:///" + DB
os.environ["DATABASE_URL"] = DB
os.environ.setdefault("SECRET_KEY", "test-secret-123")
for k in ("LDAP_SERVER", "LDAP_BASE_DN", "LDAP_ADMIN_DN", "LDAP_ADMIN_PASSWORD", "LDAP_USER_SEARCH_FILTER"):
    os.environ.pop(k, None)

os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)
import main  # noqa: E402  （import 会执行模块级初始化，无 ADMIN_* 时自举为 no-op）

def wipe_db_ldap(db):
    db.query(main.Config).filter(main.Config.key.like("ldap%")).delete(synchronize_session=False)
    db.commit()

failures = 0

# --- 1. 撤销配置后：旧全局值必须被清除 ---
main.LDAP_SERVER = "ldap://stale.example:389"
main.LDAP_BASE_DN = "dc=stale,dc=com"
main.LDAP_ADMIN_DN = "cn=stale,dc=stale,dc=com"
main.LDAP_ADMIN_PASSWORD = "stale-pass"
main.LDAP_USER_SEARCH_FILTER = "(&(sAMAccountName={})(memberOf=CN=stale,DC=stale,DC=com))"

db = main.SessionLocal()
try:
    wipe_db_ldap(db)
    main.load_ldap_config_from_db(db)
    vals = (main.LDAP_SERVER, main.LDAP_BASE_DN, main.LDAP_ADMIN_DN,
            main.LDAP_ADMIN_PASSWORD, main.LDAP_USER_SEARCH_FILTER)
    if all(v == "" for v in vals):
        print("PASS | 撤销DB配置后，旧全局值被清除（每次从空值重新构造）")
    else:
        failures += 1
        print(f"FAIL | 撤销后仍有旧全局值残留: {vals}")
finally:
    db.close()

# --- 2. 环境变量优先级高于数据库配置 ---
os.environ["LDAP_SERVER"] = "ldap://env.example:389"
db = main.SessionLocal()
try:
    c = main.Config(key="ldap_server", value="ldap://db.example:389", description="test")
    db.add(c); db.commit()
    main.LDAP_SERVER = "ldap://stale2.example:389"
    main.load_ldap_config_from_db(db)
    if main.LDAP_SERVER == "ldap://env.example:389":
        print("PASS | 环境变量优先级 > 数据库配置")
    else:
        failures += 1
        print(f"FAIL | env 未生效: {main.LDAP_SERVER!r}")
finally:
    wipe_db_ldap(db)
    db.close()
    os.environ.pop("LDAP_SERVER", None)

# --- 3. 配置不完整时 ldap_authenticate 直接不可用（不尝试连接） ---
main.LDAP_SERVER = ""
main.LDAP_BASE_DN = "dc=x,dc=com"
main.LDAP_USER_SEARCH_FILTER = "(&(uid={}))"
ok, info = main.ldap_authenticate("someone", "pass")
if ok is False and info is None:
    print("PASS | 缺 LDAP_SERVER 时 ldap_authenticate 返回不可用（不尝试连接）")
else:
    failures += 1
    print(f"FAIL | ldap_authenticate 未按预期不可用: {ok}, {info}")

sys.exit(1 if failures else 0)
