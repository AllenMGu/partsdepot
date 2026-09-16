#!/usr/bin/env bash
# Playwright 端到端验证一键运行：全新 scratch DB 起服务 → 种子数据 → 浏览器测试 → 清理
# 依赖：fastapi、uvicorn、playwright（Python 包）+ 已安装的 Chromium
#       （本机: PLAYWRIGHT_BROWSERS_PATH=... playwright install chromium；CI 见 .github/workflows/ci.yml）
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PW_E2E_PORT:-8099}"
DB="$(pwd)/.pwtest.db"
LOG="$(pwd)/.pwtest_server.log"

# Python 解释器：显式 PYTHON > 仓库 .venv > 系统 python3（CI）
if [ -z "${PYTHON:-}" ]; then
  if [ -x .venv/bin/python ]; then
    PYTHON=".venv/bin/python"
  else
    PYTHON="python3"
  fi
fi
"$PYTHON" -c "import fastapi, uvicorn, playwright" 2>/dev/null || {
  echo "缺少依赖：请先 pip install -r requirements.txt && pip install playwright" >&2
  exit 1
}

rm -f "$DB"
# SQLite 绝对路径 URL 需要 4 个斜杠（sqlite:/// + /abs/path）
SECRET_KEY=pwtest-secret DATABASE_URL="sqlite:///$DB" \
  ADMIN_USERNAME=admin ADMIN_PASSWORD=Admin-Test-2026 \
  "$PYTHON" -m uvicorn main:app --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:$PORT/docs" >/dev/null 2>&1 && break
  sleep 0.5
done

"$PYTHON" tests/seed_pw_e2e.py "$PORT" "$DB"
"$PYTHON" tests/pw_e2e.py
