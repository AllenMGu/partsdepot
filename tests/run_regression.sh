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

rm -f "$DB"
echo "==> 启动测试服务 (port $PORT, fresh sqlite DB)"
env -u LDAP_SERVER -u LDAP_BASE_DN -u LDAP_ADMIN_DN -u LDAP_ADMIN_PASSWORD -u LDAP_USER_SEARCH_FILTER \
  SECRET_KEY=test-secret-123 \
  DATABASE_URL="sqlite:///$PWD/$DB" \
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
WMS_TEST_BASE="http://127.0.0.1:$PORT" WMS_TEST_DB="$PWD/$DB" WMS_TEST_LOG="$PWD/$LOG" \
  "$PY" tests/test_suite.py
SUITE_RC=$?

echo "==> 运行 LDAP 配置加载进程内测试"
WMS_TEST_DB="$PWD/$DB" "$PY" tests/ldap_revoke_unit.py
UNIT_RC=$?

if [ $SUITE_RC -eq 0 ] && [ $UNIT_RC -eq 0 ]; then
  echo "==> 全部回归测试通过"
else
  echo "==> 存在失败（HTTP 套件 rc=$SUITE_RC，进程内 rc=$UNIT_RC）"
fi
exit $(( SUITE_RC != 0 || UNIT_RC != 0 ))
