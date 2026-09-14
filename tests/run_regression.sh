#!/usr/bin/env bash
# 回归测试一键运行（评审可独立复现）
# 前置：pip install -r requirements.txt（sqlite 测试无需 PostgreSQL）
# 用法：bash tests/run_regression.sh
set -u
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"
PORT="${PORT:-8091}"
DB="tests/regression.db"
LOG="tests/server.log"

if [ -n "${WMS_DATABASE_URL:-}" ]; then
  DB_URL="$WMS_DATABASE_URL"
  DBREF="$DB_URL"
  # 安全护栏：外部传入的库必须是"可丢弃的空测试库"（会删 LDAP 配置行/建删业务数据）
  "$PY" tests/test_db_guard.py "$DB_URL" || exit 2
  echo "==> 启动测试服务 (port $PORT, PostgreSQL: 行级锁/咨询锁语义真正生效)"
else
  DB_URL="sqlite:///$PWD/$DB"
  DBREF="$PWD/$DB"
  rm -f "$DB"
  echo "==> 启动测试服务 (port $PORT, fresh sqlite DB)"
fi

env -u LDAP_SERVER -u LDAP_BASE_DN -u LDAP_ADMIN_DN -u LDAP_ADMIN_PASSWORD -u LDAP_USER_SEARCH_FILTER \
  SECRET_KEY=test-secret-123 \
  DATABASE_URL="$DB_URL" \
  ADMIN_USERNAME=admin ADMIN_PASSWORD=Admin-Test-2026 \
  "$PY" -m uvicorn main:app --host 127.0.0.1 --port "$PORT" > "$LOG" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT

ready=0
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/docs" 2>/dev/null || true)
  if [ "$code" = "200" ]; then ready=1; break; fi
  sleep 0.5
done
[ "$ready" = "1" ] || { echo "FATAL: 服务未就绪"; tail -20 "$LOG"; exit 2; }

echo "==> 运行 HTTP 回归套件"
WMS_TEST_BASE="http://127.0.0.1:$PORT" WMS_TEST_DB="$DBREF" WMS_TEST_LOG="$PWD/$LOG" \
  "$PY" tests/test_suite.py
SUITE_RC=$?

echo "==> 运行 LDAP 配置加载进程内测试（只使用独立临时库，不得修改目标测试库）"
# 六轮回归：在目标测试库插入标记行——单元测试若误触碰目标库（删除 ldap% 配置行）此标记即丢失
SECRET_KEY=test-secret-123 DATABASE_URL="$DB_URL" "$PY" - <<'EOF'
import sys; sys.path.insert(0, ".")
import main
db = main.SessionLocal()
if not db.query(main.Config).filter(main.Config.key == "ldap_round6_marker").first():
    db.add(main.Config(key="ldap_round6_marker", value="must-survive-ldap-revoke-unit",
                       description="round6 marker: unit test must not touch target DB"))
    db.commit()
db.close()
EOF
WMS_TEST_DB="$DBREF" "$PY" tests/ldap_revoke_unit.py
UNIT_RC=$?
# 标记行必须仍在（证明单元测试只使用独立临时库、未修改目标库）
SECRET_KEY=test-secret-123 DATABASE_URL="$DB_URL" "$PY" - <<'EOF'
import sys; sys.path.insert(0, ".")
import main
db = main.SessionLocal()
row = db.query(main.Config).filter(main.Config.key == "ldap_round6_marker").first()
db.close()
sys.exit(0 if row and row.value == "must-survive-ldap-revoke-unit" else 1)
EOF
MARK_RC=$?
if [ $MARK_RC -ne 0 ]; then
  echo "FAIL | ldap_revoke_unit.py 修改了目标测试库（必须只使用独立临时库）"
  exit 1
fi

if [ $SUITE_RC -eq 0 ] && [ $UNIT_RC -eq 0 ]; then
  echo "==> 全部回归测试通过"
else
  echo "==> 存在失败（HTTP 套件 rc=$SUITE_RC，进程内 rc=$UNIT_RC）"
fi
exit $(( SUITE_RC != 0 || UNIT_RC != 0 ))
