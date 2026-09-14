"""数据库引擎、会话工厂与声明式基类。"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from core.config import DATABASE_URL

# ===== 原 main.py 块: engine/Base =====
# ------------------- 数据库初始化 -------------------
_engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    # 仅 SQLite：FastAPI 的异步端点与同步依赖运行在不同工作线程，
    # 需要共享同一连接避免跨线程报错（PostgreSQL 等无需此设置）
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
    from sqlalchemy.pool import StaticPool
    _engine_kwargs["poolclass"] = StaticPool
engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
