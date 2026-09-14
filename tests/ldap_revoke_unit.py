#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""进程内单元测试：LDAP 配置加载（load_ldap_config_from_db）

黑盒 HTTP 测试无法观察"进程内全局配置"，本脚本直接 import main 模块验证：
1. 撤销（DB 配置清空/删除）且无环境变量时，上一次加载留下的旧全局值
   （旧服务器/凭据/搜索过滤器）必须被清除——每次调用从空值重新构造；
2. 环境变量优先级高于数据库配置；
3. 配置不完整时 ldap_authenticate 直接不可用（不尝试连接）。

安全设计（六轮评审 P0）：
本脚本**只使用独立临时库**（tempfile 创建、进程退出即删除），
**忽略 WMS_TEST_DB 环境变量**（若设置则提示被忽略）。
即使被单独运行，也不可能修改任何既有数据库——本脚本包含删除配置行的
操作（wipe_db_ldap），历史上若指向现有库会误删其 LDAP 配置。

用法：与 tests/run_regression.sh 相同环境（无 LDAP_* 环境变量即可）。
"""
import atexit
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)

# ---- 安全：只使用独立临时库，绝不触碰任何既有数据库（六轮 P0 修复）----
# 七轮 P1：仅提示"检测到并忽略"，**不打印变量值**——URL 可能含密码，
# 打印会泄露到终端/CI 日志。
if os.environ.get("WMS_TEST_DB"):
    print("NOTE | 检测到 WMS_TEST_DB —— 本单元测试只使用独立临时库，该值被忽略（按安全要求不打印其内容）。")
_fd, DB_PATH = tempfile.mkstemp(suffix=".db", prefix="ldap_revoke_unit_")
os.close(_fd)
os.remove(DB_PATH)  # 让 main 的 create_all 全新建库（文件不存在时建表）
atexit.register(lambda: os.path.exists(DB_PATH) and os.remove(DB_PATH) or None)
DB = "sqlite:///" + DB_PATH
os.environ["DATABASE_URL"] = DB
os.environ.pop("WMS_TEST_DB", None)  # 防止任何被 import 的代码读到指向既有库的值
os.environ.setdefault("SECRET_KEY", "test-secret-123")
for k in ("LDAP_SERVER", "LDAP_BASE_DN", "LDAP_ADMIN_DN", "LDAP_ADMIN_PASSWORD", "LDAP_USER_SEARCH_FILTER"):
    os.environ.pop(k, None)

os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)
import main  # noqa: E402  （import 会执行模块级初始化，建全新临时库的表）

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
