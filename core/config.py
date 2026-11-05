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

# ===== 邮件通知（SMTP）=====
# 配置优先级：环境变量 > 数据库 config 表（见 core.mail.load_smtp_config_from_db）。
# SMTP_HOST 为空时邮件通知自动跳过（不影响申请/审批主流程）。
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
try:
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or 587)
except ValueError:
    SMTP_PORT = 587
SMTP_SECURITY = os.getenv("SMTP_SECURITY", "starttls").strip().lower()  # starttls / ssl / none
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", "").strip()
try:
    SMTP_TIMEOUT = int(os.getenv("SMTP_TIMEOUT", "15") or 15)
except ValueError:
    SMTP_TIMEOUT = 15
# 邮件正文中的系统访问地址（如 http://10.80.101.39:8000），留空则不附带链接
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
