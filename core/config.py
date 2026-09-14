"""核心配置：环境变量、安全参数、日志与启动校验。"""

import logging
import os


# ===== 原 main.py 块: 配置项 =====
# ------------------- 配置项 -------------------
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
ALGORITHM = "HS256"
# JWT 有效期（分钟）：默认 30 分钟（见 README）；
# 需要小程序长会话等场景时用 ACCESS_TOKEN_EXPIRE_MINUTES 显式覆盖
try:
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30") or 30)
except ValueError:
    ACCESS_TOKEN_EXPIRE_MINUTES = 30
MAX_IMPORT_FILE_SIZE_MB = int(os.getenv("MAX_IMPORT_FILE_SIZE_MB", "10"))
MAX_IMPORT_FILE_SIZE_BYTES = MAX_IMPORT_FILE_SIZE_MB * 1024 * 1024
MAX_LOGIN_FAILURES = 5
LOGIN_LOCK_MINUTES = 15
LOGIN_FAILURE_WINDOW_MINUTES = 60

# ===== 原 main.py 块: DATABASE_URL =====
# 数据库配置
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# ===== 原 main.py 块: 日志+启动校验 =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
if not SECRET_KEY:
    raise RuntimeError("Missing required environment variable: SECRET_KEY")
if not DATABASE_URL:
    raise RuntimeError("Missing required environment variable: DATABASE_URL")
