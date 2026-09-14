from fastapi import FastAPI, HTTPException, Depends, status, Form, APIRouter, Depends, File, UploadFile, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import StreamingResponse, Response
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Enum, UniqueConstraint, CheckConstraint, func, or_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from passlib.context import CryptContext
from jose import JWTError, jwt
from ldap3.utils.conv import escape_filter_chars
import enum
import logging
import zlib
import secrets
import pandas as pd
import io
import ldap3
import os
import json

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

# LDAP配置：全部通过环境变量或数据库配置表显式配置。
# 代码不再内置任何内部环境默认值（服务器地址/域/群组路径）。
# 缺少服务器、Base DN 或搜索过滤器时，LDAP 登录直接不可用（见 ldap_authenticate），
# 而不是静默降级为更宽松的过滤器。
LDAP_SERVER = os.getenv("LDAP_SERVER", "").strip()
LDAP_BASE_DN = os.getenv("LDAP_BASE_DN", "").strip()
LDAP_ADMIN_DN = os.getenv("LDAP_ADMIN_DN", "").strip()
LDAP_ADMIN_PASSWORD = os.getenv("LDAP_ADMIN_PASSWORD", "").strip()
LDAP_USER_SEARCH_FILTER = os.getenv("LDAP_USER_SEARCH_FILTER", "").strip()

router = APIRouter()

# 数据库配置
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# 密码加密上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
AUTH_COOKIE_NAME = "access_token"
AUTH_COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "lax")
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
if not SECRET_KEY:
    raise RuntimeError("Missing required environment variable: SECRET_KEY")
if not DATABASE_URL:
    raise RuntimeError("Missing required environment variable: DATABASE_URL")

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

# ------------------- 枚举定义 -------------------
class UserRole(str, enum.Enum):
    ADMIN = "admin"       # 仓库管理员，可管理所有数据
    OPERATOR = "operator" # 操作员，仅可做出入库和盘点

class InventoryType(str, enum.Enum):
    IN = "入库"
    OUT = "出库"

# ------------------- 数据库模型 -------------------
# 0. 配置表
class Config(Base):
    __tablename__ = "config"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, index=True, comment="配置项名称")
    value = Column(String(500), comment="配置项值")
    description = Column(String(500), comment="配置项描述")
    create_time = Column(DateTime, default=datetime.now)
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now)

# 1. 仓库表
class Warehouse(Base):
    __tablename__ = "warehouses"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, index=True, comment="仓库编码")
    name = Column(String(100), comment="仓库名称")
    address = Column(String(200), comment="仓库地址")
    is_active = Column(Boolean, default=True, comment="是否启用")
    create_time = Column(DateTime, default=datetime.now)

# 添加用户-仓库关联表（多对多）
class UserWarehouse(Base):
    __tablename__ = "user_warehouses"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), comment="用户ID")
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="仓库ID")
    is_default = Column(Boolean, default=False, comment="是否默认仓库")
    create_time = Column(DateTime, default=datetime.now)

# 2. 用户表
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, comment="用户名")
    hashed_password = Column(String(100), comment="加密密码")
    full_name = Column(String(100), comment="真实姓名")
    role = Column(Enum(UserRole), default=UserRole.OPERATOR, comment="角色")
    is_active = Column(Boolean, default=True, comment="是否启用")
    is_ldap_user = Column(Boolean, default=False, comment="是否是LDAP用户")
    create_time = Column(DateTime, default=datetime.now)
    current_warehouse_id = Column(Integer, nullable=True, comment="当前选择的仓库ID")
    
    # 多对多关联
    warehouses = relationship("Warehouse", secondary="user_warehouses",
                            backref="users", lazy="dynamic")

# 3. 库位表（关联仓库）
class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="所属仓库ID")
    location_code = Column(String(50), unique=True, index=True, comment="库位编码")
    name = Column(String(100), comment="库位名称")
    is_active = Column(Boolean, default=True, comment="是否启用")
    create_time = Column(DateTime, default=datetime.now)
    
    # 关联关系
    warehouse = relationship("Warehouse")

# 4. 货物表（全局货物，多仓库共享）
class Goods(Base):
    __tablename__ = "goods"
    id = Column(Integer, primary_key=True, index=True)
    barcode = Column(String(100), unique=True, index=True, comment="货物条码")
    name = Column(String(100), comment="货物名称")
    spec = Column(String(100), comment="规格型号")
    unit = Column(String(20), comment="单位")
    price = Column(Float, comment="单价")
    create_time = Column(DateTime, default=datetime.now)

# 5. 库存表（关联仓库+库位+货物）
class Stock(Base):
    __tablename__ = "stock"
    id = Column(Integer, primary_key=True, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="仓库ID")
    goods_id = Column(Integer, ForeignKey("goods.id"), comment="货物ID")
    location_id = Column(Integer, ForeignKey("locations.id"), comment="库位ID")
    quantity = Column(Float, default=0, comment="库存数量")
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 复合唯一索引，防止相同仓库、货物、库位的重复记录
    __table_args__ = (
        UniqueConstraint('warehouse_id', 'goods_id', 'location_id', name='_warehouse_goods_location_uc'),
        CheckConstraint('quantity >= 0', name='stock_quantity_non_negative'),
    )

    # 关联关系
    warehouse = relationship("Warehouse")
    goods = relationship("Goods")
    location = relationship("Location")

# 6. 出入库记录表
class InventoryRecord(Base):
    __tablename__ = "inventory_records"
    id = Column(Integer, primary_key=True, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="仓库ID")
    goods_id = Column(Integer, ForeignKey("goods.id"), comment="货物ID")
    location_id = Column(Integer, ForeignKey("locations.id"), comment="库位ID")
    type = Column(Enum(InventoryType), comment="类型：入库/出库")
    quantity = Column(Float, comment="数量")
    operator_id = Column(Integer, ForeignKey("users.id"), comment="操作员ID")
    remark = Column(String(500), comment="备注")
    create_time = Column(DateTime, default=datetime.now)
    
    # 关联关系
    warehouse = relationship("Warehouse")
    goods = relationship("Goods")
    location = relationship("Location")
    operator = relationship("User")

# 7. 盘点单表头
class CheckOrderHeader(Base):
    __tablename__ = "check_order_header"
    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String(50), unique=True, index=True, comment="盘点单号")
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="仓库ID")
    operator_id = Column(Integer, ForeignKey("users.id"), comment="操作员ID")
    remark = Column(String(500), comment="备注")
    status = Column(String(20), default="DRAFT", comment="状态: DRAFT-草稿, IN_PROGRESS-盘点中, COMPLETED-已完成")
    create_time = Column(DateTime, default=datetime.now)
    start_time = Column(DateTime, comment="开始时间")
    complete_time = Column(DateTime, comment="完成时间")

    # 关联关系
    warehouse = relationship("Warehouse")
    operator = relationship("User")
    items = relationship("CheckOrderItem", back_populates="header", cascade="all, delete-orphan")

# 8. 盘点单明细
class CheckOrderItem(Base):
    __tablename__ = "check_order_item"
    id = Column(Integer, primary_key=True, index=True)
    header_id = Column(Integer, ForeignKey("check_order_header.id"), comment="单据头ID")
    goods_id = Column(Integer, ForeignKey("goods.id"), comment="货物ID")
    location_id = Column(Integer, ForeignKey("locations.id"), comment="库位ID")
    check_quantity = Column(Float, comment="盘点数量")
    actual_quantity = Column(Float, comment="系统库存数量")
    diff_quantity = Column(Float, comment="差异数量")
    create_time = Column(DateTime, default=datetime.now)

    # 关联关系
    header = relationship("CheckOrderHeader", back_populates="items")
    goods = relationship("Goods")
    location = relationship("Location")

# 9. 盘点记录表（保留历史记录）
class CheckRecord(Base):
    __tablename__ = "check_records"
    id = Column(Integer, primary_key=True, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="仓库ID")
    goods_id = Column(Integer, ForeignKey("goods.id"), comment="货物ID")
    location_id = Column(Integer, ForeignKey("locations.id"), comment="库位ID")
    check_quantity = Column(Float, comment="盘点数量")
    actual_quantity = Column(Float, comment="实际库存数量")
    operator_id = Column(Integer, ForeignKey("users.id"), comment="操作员ID")
    check_time = Column(DateTime, default=datetime.now)

    # 关联关系
    warehouse = relationship("Warehouse")
    goods = relationship("Goods")
    location = relationship("Location")
    operator = relationship("User")

# 8. 入库单表头
class InboundOrderHeader(Base):
    __tablename__ = "inbound_order_header"
    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String(50), unique=True, index=True, comment="入库单号")
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="仓库ID")
    supplier = Column(String(200), comment="供应商")
    operator_id = Column(Integer, ForeignKey("users.id"), comment="操作员ID")
    total_amount = Column(Float, default=0, comment="总金额")
    remark = Column(String(500), comment="备注")
    status = Column(String(20), default="DRAFT", comment="状态")
    create_time = Column(DateTime, default=datetime.now)
    submit_time = Column(DateTime, comment="提交时间")
    complete_time = Column(DateTime, comment="完成时间")
    
    # 关联关系
    warehouse = relationship("Warehouse")
    operator = relationship("User")
    items = relationship("InboundOrderItem", back_populates="header", cascade="all, delete-orphan")

# 9. 入库单明细
class InboundOrderItem(Base):
    __tablename__ = "inbound_order_item"
    id = Column(Integer, primary_key=True, index=True)
    header_id = Column(Integer, ForeignKey("inbound_order_header.id"), comment="单据头ID")
    goods_id = Column(Integer, ForeignKey("goods.id"), comment="货物ID")
    location_id = Column(Integer, ForeignKey("locations.id"), comment="库位ID")
    quantity = Column(Float, comment="数量")
    unit_price = Column(Float, comment="单价")
    total_price = Column(Float, comment="总价")
    remark = Column(String(500), comment="备注")
    create_time = Column(DateTime, default=datetime.now)
    
    # 关联关系
    header = relationship("InboundOrderHeader", back_populates="items")
    goods = relationship("Goods")
    location = relationship("Location")

# 10. 出库单表头
class OutboundOrderHeader(Base):
    __tablename__ = "outbound_order_header"
    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String(50), unique=True, index=True, comment="出库单号")
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="仓库ID")
    customer = Column(String(200), comment="客户")
    operator_id = Column(Integer, ForeignKey("users.id"), comment="操作员ID")
    total_amount = Column(Float, default=0, comment="总金额")
    remark = Column(String(500), comment="备注")
    status = Column(String(20), default="DRAFT", comment="状态")
    create_time = Column(DateTime, default=datetime.now)
    submit_time = Column(DateTime, comment="提交时间")
    complete_time = Column(DateTime, comment="完成时间")
    
    # 关联关系
    warehouse = relationship("Warehouse")
    operator = relationship("User")
    items = relationship("OutboundOrderItem", back_populates="header", cascade="all, delete-orphan")

# 11. 出库单明细
class OutboundOrderItem(Base):
    __tablename__ = "outbound_order_item"
    id = Column(Integer, primary_key=True, index=True)
    header_id = Column(Integer, ForeignKey("outbound_order_header.id"), comment="单据头ID")
    goods_id = Column(Integer, ForeignKey("goods.id"), comment="货物ID")
    location_id = Column(Integer, ForeignKey("locations.id"), comment="库位ID")
    quantity = Column(Float, comment="数量")
    unit_price = Column(Float, comment="单价")
    total_price = Column(Float, comment="总价")
    remark = Column(String(500), comment="备注")
    create_time = Column(DateTime, default=datetime.now)
    
    # 关联关系
    header = relationship("OutboundOrderHeader", back_populates="items")
    goods = relationship("Goods")
    location = relationship("Location")

# 创建所有表
Base.metadata.create_all(bind=engine)

# ------------------- FastAPI应用初始化 -------------------
app = FastAPI(title="多仓库管理系统API")

# 跨域配置（可用环境变量 CORS_ORIGINS 逗号分隔覆盖；默认保持原白名单）
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost,http://127.0.0.1").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

# ------------------- 工具函数 -------------------
# 获取数据库会话
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 密码验证
def verify_password(plain_password: str, hashed_password: str):
    return pwd_context.verify(plain_password, hashed_password)

# 密码加密
def get_password_hash(password: str):
    return pwd_context.hash(password)

def _login_fail_key(username: str) -> str:
    return f"login_fail:{(username or '').strip().lower()}"

def _parse_lock_time(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None

def get_login_fail_state(db: Session, username: str) -> Dict[str, Any]:
    key = _login_fail_key(username)
    record = db.query(Config).filter(Config.key == key).first()
    if not record or not record.value:
        return {"count": 0, "locked_until": None, "window_start": None}
    try:
        payload = json.loads(record.value)
        return {
            "count": int(payload.get("count", 0)),
            "locked_until": _parse_lock_time(payload.get("locked_until", "")),
            "window_start": _parse_lock_time(payload.get("window_start", ""))
        }
    except Exception:
        return {"count": 0, "locked_until": None, "window_start": None}

def save_login_fail_state(
    db: Session,
    username: str,
    count: int,
    locked_until: Optional[datetime],
    window_start: Optional[datetime]
):
    key = _login_fail_key(username)
    payload = {
        "count": max(0, int(count)),
        "locked_until": locked_until.isoformat() if locked_until else "",
        "window_start": window_start.isoformat() if window_start else ""
    }
    record = db.query(Config).filter(Config.key == key).first()
    if record:
        record.value = json.dumps(payload, ensure_ascii=False)
        record.update_time = datetime.now()
    else:
        record = Config(
            key=key,
            value=json.dumps(payload, ensure_ascii=False),
            description="登录失败计数与锁定状态"
        )
        db.add(record)
    db.commit()

def clear_login_fail_state(db: Session, username: str):
    key = _login_fail_key(username)
    record = db.query(Config).filter(Config.key == key).first()
    if not record:
        return
    db.delete(record)
    db.commit()

def ensure_not_locked(db: Session, username: str):
    state = get_login_fail_state(db, username)
    locked_until = state.get("locked_until")
    window_start = state.get("window_start")
    now = datetime.utcnow()
    if locked_until and locked_until > now:
        raise HTTPException(
            status_code=423,
            detail=f"密码错误次数过多，账号已锁定，请于 {locked_until.strftime('%Y-%m-%d %H:%M:%S')} 后重试"
        )
    if locked_until and locked_until <= now:
        clear_login_fail_state(db, username)
        return

    if window_start and (now - window_start) > timedelta(minutes=LOGIN_FAILURE_WINDOW_MINUTES):
        clear_login_fail_state(db, username)

def handle_login_failure(db: Session, username: str):
    state = get_login_fail_state(db, username)
    now = datetime.utcnow()
    window_start = state.get("window_start")
    if not window_start or (now - window_start) > timedelta(minutes=LOGIN_FAILURE_WINDOW_MINUTES):
        count = 1
        window_start = now
    else:
        count = int(state.get("count", 0)) + 1

    locked_until = None
    if count >= MAX_LOGIN_FAILURES:
        locked_until = now + timedelta(minutes=LOGIN_LOCK_MINUTES)
        count = 0
        window_start = None
    save_login_fail_state(db, username, count, locked_until, window_start)
    if locked_until:
        raise HTTPException(
            status_code=423,
            detail=f"密码错误次数过多，账号已锁定，请于 {locked_until.strftime('%Y-%m-%d %H:%M:%S')} 后重试"
        )
    raise HTTPException(status_code=401, detail="用户名或密码错误")

def load_ldap_config_from_db(db: Session):
    """加载 LDAP 运行配置。优先级：进程环境变量 > 数据库配置表 > 未配置（空值）。
    每次调用都从空值开始**重新构造完整配置**（而不是增量覆盖），
    这样数据库配置被删除/清空且无环境变量时，上一次请求加载的旧服务器、
    旧凭据、旧搜索过滤器会随本次加载被清除，LDAP 登录不可用（401），
    不会继续用旧值认证。绝不回退到任何内置默认值。"""
    global LDAP_SERVER, LDAP_BASE_DN, LDAP_ADMIN_DN, LDAP_ADMIN_PASSWORD, LDAP_USER_SEARCH_FILTER
    mapping = [
        ("LDAP_SERVER", "ldap_server"),
        ("LDAP_BASE_DN", "ldap_base_dn"),
        ("LDAP_ADMIN_DN", "ldap_admin_dn"),
        ("LDAP_ADMIN_PASSWORD", "ldap_admin_password"),
        ("LDAP_USER_SEARCH_FILTER", "ldap_user_search_filter"),
    ]
    mod_globals = globals()
    for env_key, db_key in mapping:
        mod_globals[env_key] = ""  # 先全部清空：撤销/删除 DB 配置必须立即生效
        env_val = os.getenv(env_key, "").strip()
        if env_val:
            mod_globals[env_key] = env_val
            continue
        item = db.query(Config).filter(Config.key == db_key).first()
        if item and item.value is not None and str(item.value).strip():
            mod_globals[env_key] = str(item.value).strip()
    if LDAP_USER_SEARCH_FILTER and "{}" not in LDAP_USER_SEARCH_FILTER:
        logging.warning("LDAP_USER_SEARCH_FILTER does not contain '{}' placeholder; search may return non-target users.")

# LDAP认证
def ldap_authenticate(username: str, password: str):
    """通过LDAP验证用户身份"""
    if not LDAP_SERVER or not LDAP_BASE_DN or not LDAP_USER_SEARCH_FILTER:
        logging.warning("LDAP 未完整配置（缺少服务器/Base DN/搜索过滤器），LDAP 登录不可用")
        return False, None
    if not LDAP_ADMIN_DN or not LDAP_ADMIN_PASSWORD:
        logging.error("LDAP admin credentials are missing")
        return False, None
    try:
        logging.info(f"开始LDAP认证，用户: {username}")
        # 建立LDAP连接
        server = ldap3.Server(LDAP_SERVER, get_info=ldap3.ALL)
        conn = ldap3.Connection(server, user=LDAP_ADMIN_DN, password=LDAP_ADMIN_PASSWORD, auto_bind=True)
        logging.info("成功连接到LDAP服务器")

        # 搜索用户
        escaped_username = escape_filter_chars(username)
        search_filter = LDAP_USER_SEARCH_FILTER.format(escaped_username)
        logging.info(f"搜索过滤器: {search_filter}")
        conn.search(LDAP_BASE_DN, search_filter, attributes=['cn', 'mail', 'givenName', 'sn', 'sAMAccountName', 'uid'])
        logging.info(f"搜索结果数量: {len(conn.entries)}")

        if conn.entries:
            normalized_username = username.strip().lower()
            matched_entry = None
            for entry in conn.entries:
                candidate_values = []
                if hasattr(entry, "sAMAccountName") and entry.sAMAccountName.value:
                    candidate_values.append(str(entry.sAMAccountName.value))
                if hasattr(entry, "uid") and entry.uid.value:
                    candidate_values.append(str(entry.uid.value))
                if hasattr(entry, "cn") and entry.cn.value:
                    candidate_values.append(str(entry.cn.value))
                if hasattr(entry, "mail") and entry.mail.value:
                    mail_value = str(entry.mail.value)
                    candidate_values.append(mail_value)
                    if "@" in mail_value:
                        candidate_values.append(mail_value.split("@", 1)[0])

                if any(v.strip().lower() == normalized_username for v in candidate_values):
                    matched_entry = entry
                    break

            if not matched_entry:
                logging.error(f"LDAP搜索返回{len(conn.entries)}条，但未找到与用户名精确匹配的条目: {username}")
                return False, None

            user_dn = matched_entry.entry_dn
            logging.info(f"找到精确匹配用户DN: {user_dn}")
            # 尝试使用用户凭证绑定
            user_conn = ldap3.Connection(server, user=user_dn, password=password)
            if user_conn.bind():
                logging.info(f"用户 {username} 认证成功")
                # 获取用户信息
                user_info = {
                    'username': username,
                    'full_name': matched_entry.cn.value if hasattr(matched_entry, 'cn') else username,
                    'email': matched_entry.mail.value if hasattr(matched_entry, 'mail') else '',
                    'first_name': matched_entry.givenName.value if hasattr(matched_entry, 'givenName') else '',
                    'last_name': matched_entry.sn.value if hasattr(matched_entry, 'sn') else ''
                }
                return True, user_info
            else:
                logging.error(f"用户 {username} 凭证绑定失败，DN: {user_dn}")
        else:
            logging.error(f"未找到用户: {username}，搜索过滤器: {search_filter}")

        return False, None
    except Exception as e:
        logging.error(f"LDAP认证过程异常: {str(e)}")
        return False, None

# 创建JWT Token
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# 获取当前用户
async def get_current_user(request: Request, db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )

    tokens_to_try: List[str] = []
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        header_token = authorization.split(" ", 1)[1].strip()
        if header_token and header_token.lower() not in {"null", "undefined"}:
            tokens_to_try.append(header_token)

    cookie_token = request.cookies.get(AUTH_COOKIE_NAME)
    if cookie_token and cookie_token not in tokens_to_try:
        tokens_to_try.append(cookie_token)

    username: Optional[str] = None
    for token in tokens_to_try:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
            if username:
                break
        except JWTError:
            continue

    if username is None:
        raise credentials_exception
    
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户已被禁用")
    return user

async def read_upload_with_size_limit(upload_file: UploadFile, max_bytes: int) -> bytes:
    """读取上传文件并限制最大体积，防止大文件占满内存。"""
    total = 0
    chunks: List[bytes] = []
    while True:
        chunk = await upload_file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"上传文件过大，最大允许 {MAX_IMPORT_FILE_SIZE_MB}MB"
            )
        chunks.append(chunk)
    return b"".join(chunks)

def format_outbound_order_response(order):
    return {
        "id": order.id,
        "order_no": order.order_no,
        "warehouse_id": order.warehouse_id,
        "warehouse_name": order.warehouse.name,
        "customer": order.customer,
        "operator_id": order.operator_id,
        "operator_name": order.operator.full_name,
        "total_amount": order.total_amount,
        "remark": order.remark,
        "status": order.status,
        "create_time": order.create_time,
        "submit_time": order.submit_time,
        "complete_time": order.complete_time,
        "item_count": len(order.items)
    }

def format_outbound_order_item_response(item):
    return {
        "id": item.id,
        "goods_id": item.goods_id,
        "goods_barcode": item.goods.barcode,
        "goods_name": item.goods.name,
        "location_id": item.location_id,
        "location_code": item.location.location_code,
        "quantity": item.quantity,
        "unit_price": item.unit_price,
        "total_price": item.total_price,
        "remark": item.remark
    }

def recalculate_outbound_order_total(order_id, db):
    """重新计算出库单总金额"""
    order = db.query(OutboundOrderHeader).filter(OutboundOrderHeader.id == order_id).first()
    if order:
        total_amount = 0
        for item in order.items:
            total_amount += item.total_price
        order.total_amount = total_amount
        db.commit()
        db.refresh(order)

def recalculate_inbound_order_total(order_id, db):
    """重新计算入库单总金额"""
    order = db.query(InboundOrderHeader).filter(InboundOrderHeader.id == order_id).first()
    if order:
        total_amount = 0
        for item in order.items:
            total_amount += item.total_price
        order.total_amount = total_amount
        db.commit()
        db.refresh(order)

# 生成单据编号
def lock_stock_row(db: Session, warehouse_id: int, goods_id: int, location_id: int):
    """行级锁读取库存行（防止并发超卖/负库存）。
    PostgreSQL 使用 FOR UPDATE 行锁（跨会话互斥，同一会话可重入）；
    SQLite 等不支持时退化为普通查询。
    """
    q = db.query(Stock).filter(
        Stock.warehouse_id == warehouse_id,
        Stock.goods_id == goods_id,
        Stock.location_id == location_id
    )
    try:
        q = q.with_for_update()
    except Exception:
        pass
    return q.first()

def lock_order_header(db: Session, model, order_id):
    """行级锁读取单据头（PostgreSQL FOR UPDATE），串行化同一单据的并发提交，
    防止草稿被并发重复提交导致库存被扣减/增加两次。SQLite 下退化为普通查询。"""
    q = db.query(model).filter(model.id == order_id)
    try:
        q = q.with_for_update()
    except Exception:
        pass
    return q.first()

def advisory_lock_stock_key(db: Session, warehouse_id: int, goods_id: int, location_id: int):
    """PostgreSQL 事务咨询锁：串行化"同一 (仓库,货物,库位) 库存行"的并发创建。
    行不存在时 FOR UPDATE 锁不到任何东西，两个并发首笔入库都会看到'无行'而各插一条，
    触发复合唯一约束（500）。先持咨询锁再重查：后到者等前一个事务提交后能看到新行，
    从而改为更新而不是插入。SQLite 下退化为 no-op。"""
    try:
        if engine.dialect.name == "postgresql":
            from sqlalchemy import text as _sa_text
            k = int(zlib.crc32(f"stockrow-{warehouse_id}-{goods_id}-{location_id}".encode("utf-8")))
            db.execute(_sa_text("SELECT pg_advisory_xact_lock(:k)"), {"k": k})
    except Exception:
        pass

def generate_order_no(prefix: str, db: Session) -> str:
    """生成单据编号（PostgreSQL 下用事务咨询锁串行化编号生成，防止并发撞号）"""
    today = datetime.now().strftime("%Y%m%d")
    try:
        if engine.dialect.name == "postgresql":
            from sqlalchemy import text as _sa_text
            db.execute(_sa_text("SELECT pg_advisory_xact_lock(:k)"),
                       {"k": int(zlib.crc32(f"orderno-{prefix}-{today}".encode("utf-8")))} )
    except Exception:
        pass
    # 取当天已存在单据的最大数字尾号 +1（而不是"计数+1"：
    # 计数+1 在删除较早单据后会复用仍存在的编号；最大尾号+1 不会）
    model = InboundOrderHeader if prefix == "IN" else OutboundOrderHeader
    prefix_len = len(prefix) + len(today)
    existing = db.query(model.order_no).filter(
        model.order_no.like(f"{prefix}{today}%")
    ).all()
    max_seq = 0
    for (no,) in existing:
        if isinstance(no, str) and len(no) > prefix_len and no[prefix_len:].isdigit():
            max_seq = max(max_seq, int(no[prefix_len:]))
    return f"{prefix}{today}{str(max_seq + 1).zfill(3)}"

# ------------------- Pydantic模型 -------------------
class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str
    warehouse_ids: List[int] = Field(default_factory=list)  # 改为列表
    role: UserRole = UserRole.OPERATOR
    
    class Config:
        json_encoders = {
            UserRole: lambda v: v.value  # 确保枚举被正确序列化
        }

class UserResponse(BaseModel):
    id: int
    username: str
    full_name: str
    current_warehouse_id: Optional[int] = None
    current_warehouse_name: Optional[str] = None
    role: UserRole
    is_ldap_user: bool
    is_active: bool = True

    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None

    class Config:
        json_encoders = {
            UserRole: lambda v: v.value
        }

class WarehouseCreate(BaseModel):
    code: str
    name: str
    address: Optional[str] = ""

class WarehouseResponse(BaseModel):
    id: int
    code: str
    name: str
    address: str
    is_active: Optional[bool] = True

    class Config:
        from_attributes = True

class WarehouseUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    is_active: Optional[bool] = None

class LocationCreate(BaseModel):
    warehouse_id: int
    location_code: str
    name: str

class LocationResponse(BaseModel):
    id: int
    warehouse_id: int
    location_code: str
    name: str
    is_active: bool
    create_time: datetime

    class Config:
        from_attributes = True

class LocationUpdate(BaseModel):
    warehouse_id: Optional[int] = None
    location_code: Optional[str] = None
    name: Optional[str] = None
    is_active: Optional[bool] = None

class GoodsCreate(BaseModel):
    barcode: str
    name: str
    spec: Optional[str] = ""
    unit: Optional[str] = "个"
    price: Optional[float] = Field(default=0.0, ge=0)

class GoodsResponse(BaseModel):
    id: int
    barcode: str
    name: str
    spec: str
    unit: str
    price: float
    create_time: datetime

    class Config:
        from_attributes = True

class GoodsUpdate(BaseModel):
    barcode: Optional[str] = None
    name: Optional[str] = None
    spec: Optional[str] = None
    unit: Optional[str] = None
    price: Optional[float] = None

class InventoryCreate(BaseModel):
    goods_barcode: str  # 扫码传入条码
    location_code: str  # 扫码传入库位编码
    type: InventoryType
    quantity: float = Field(gt=0)
    remark: Optional[str] = ""

class CheckCreate(BaseModel):
    goods_barcode: str
    location_code: str
    check_quantity: float = Field(ge=0)

class StockResponse(BaseModel):
    id: int
    warehouse_id: int
    warehouse_name: str
    goods_id: int
    goods_name: str
    goods_barcode: str
    goods_price: float
    goods_spec: Optional[str] = ""
    goods_unit: Optional[str] = ""
    location_id: int
    location_code: str
    location_name: str
    quantity: float
    update_time: datetime

# 入库单相关模型
class InboundOrderItemCreate(BaseModel):
    goods_barcode: str  # 货物条码
    location_code: str  # 库位编码
    quantity: float = Field(gt=0)
    unit_price: Optional[float] = Field(default=None, ge=0)
    remark: Optional[str] = ""

class InboundOrderItemResponse(BaseModel):
    id: int
    goods_id: int
    goods_barcode: str
    goods_name: str
    location_id: int
    location_code: str
    quantity: float
    unit_price: float
    total_price: float
    remark: str
    
    class Config:
        from_attributes = True

class InboundOrderHeaderCreate(BaseModel):
    supplier: Optional[str] = ""
    remark: Optional[str] = ""

class InboundOrderHeaderResponse(BaseModel):
    id: int
    order_no: str
    warehouse_id: int
    warehouse_name: str
    supplier: str
    operator_id: int
    operator_name: str
    total_amount: float
    remark: str
    status: str
    create_time: datetime
    submit_time: Optional[datetime] = None
    complete_time: Optional[datetime] = None
    item_count: int = 0
    
    class Config:
        from_attributes = True

class InboundOrderDetailResponse(InboundOrderHeaderResponse):
    items: List[InboundOrderItemResponse] = []

# 出库单相关模型
class OutboundOrderItemCreate(BaseModel):
    goods_barcode: str
    location_code: str
    quantity: float = Field(gt=0)
    unit_price: Optional[float] = Field(default=None, ge=0)
    remark: Optional[str] = ""

class OutboundOrderItemResponse(BaseModel):
    id: int
    goods_id: int
    goods_barcode: str
    goods_name: str
    location_id: int
    location_code: str
    quantity: float
    unit_price: float
    total_price: float
    remark: str
    
    class Config:
        from_attributes = True

class OutboundOrderHeaderCreate(BaseModel):
    customer: Optional[str] = ""
    remark: Optional[str] = ""

class OutboundOrderHeaderResponse(BaseModel):
    id: int
    order_no: str
    warehouse_id: int
    warehouse_name: str
    customer: str
    operator_id: int
    operator_name: str
    total_amount: float
    remark: str
    status: str
    create_time: datetime
    submit_time: Optional[datetime] = None
    complete_time: Optional[datetime] = None
    item_count: int = 0
    
    class Config:
        from_attributes = True

class OutboundOrderDetailResponse(OutboundOrderHeaderResponse):
    items: List[OutboundOrderItemResponse] = []

# ------------------- 认证接口 -------------------
@router.post("/token", summary="用户登录获取Token")
async def login_for_access_token(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    # 登录前优先从配置表刷新 LDAP 配置，避免仅依赖进程环境变量
    load_ldap_config_from_db(db)
    ensure_not_locked(db, username)

    # 首先尝试本地数据库认证
    user = db.query(User).filter(User.username == username).first()
    if user:
        # 检查用户是否被禁用
        if not user.is_active:
            raise HTTPException(status_code=401, detail="用户已被禁用")

        if user.is_ldap_user:
            # LDAP用户，直接使用LDAP认证
            ldap_success, ldap_user_info = ldap_authenticate(username, password)
            if not ldap_success:
                handle_login_failure(db, username)
        else:
            # 本地用户，使用本地数据库认证
            if not verify_password(password, user.hashed_password):
                handle_login_failure(db, username)
    else:
        # 本地数据库中未找到用户，尝试LDAP认证
        ldap_success, ldap_user_info = ldap_authenticate(username, password)
        if ldap_success:
            # LDAP认证成功，自动创建本地用户
            hashed_password = get_password_hash(password)
            user = User(
                username=username,
                hashed_password=hashed_password,
                full_name=ldap_user_info.get('full_name', username),
                role=UserRole.OPERATOR,  # 默认角色为操作员
                is_ldap_user=True  # 标识为LDAP用户
            )
            db.add(user)
            db.flush()

            # 新用户默认零仓库权限：由管理员在"用户管理"中显式分配。
            # （不再自动授予所有启用仓库，避免 LDAP 新用户默认获得全仓库权限）
            db.commit()
            db.refresh(user)
        else:
            handle_login_failure(db, username)

    clear_login_fail_state(db, username)

    # 获取用户所有可管理仓库
    warehouses = []
    for uw in db.query(UserWarehouse).filter(UserWarehouse.user_id == user.id).all():
        warehouse = db.query(Warehouse).filter(Warehouse.id == uw.warehouse_id).first()
        if warehouse:
            warehouses.append({
                "id": warehouse.id,
                "code": warehouse.code,
                "name": warehouse.name,
                "is_default": uw.is_default
            })

    # 获取当前仓库名称
    current_warehouse_name = None
    if user.current_warehouse_id:
        warehouse = db.query(Warehouse).filter(Warehouse.id == user.current_warehouse_id).first()
        current_warehouse_name = warehouse.name if warehouse else None

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "user_id": user.id, "role": user.role},
        expires_delta=access_token_expires
    )

    # 计算token过期时间（UTC时间）
    token_expiry = datetime.utcnow() + access_token_expires

    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=access_token,
        httponly=True,
        secure=(AUTH_COOKIE_SECURE or request.url.scheme == "https"),
        samesite=AUTH_COOKIE_SAMESITE,
        max_age=int(access_token_expires.total_seconds()),
        expires=int(access_token_expires.total_seconds()),
        path="/"
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expiry": token_expiry.isoformat(timespec='seconds') + 'Z',  # 添加过期时间字段，确保包含时区信息
        "user": {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
            "current_warehouse_id": user.current_warehouse_id,
            "current_warehouse_name": current_warehouse_name,
            "warehouses": warehouses
        }
    }

@router.post("/logout", summary="退出登录")
async def logout(response: Response):
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        path="/",
        samesite=AUTH_COOKIE_SAMESITE
    )
    return {"message": "已退出登录"}

# 新增用户（仅管理员可操作）
@router.post("/users/", response_model=UserResponse, summary="新增用户")
async def create_user(
    user: UserCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 权限校验：仅admin可创建用户
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")
    
    # 检查用户名是否存在
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="用户名已存在")
    
    try:
        # 创建用户
        hashed_password = get_password_hash(user.password)
        new_user = User(
            username=user.username,
            hashed_password=hashed_password,
            full_name=user.full_name,
            role=user.role
        )
        db.add(new_user)
        db.flush()  # 先获取用户ID
        
        # 如果是管理员，分配所有现有仓库
        if user.role == UserRole.ADMIN:
            all_warehouses = db.query(Warehouse).filter(Warehouse.is_active == True).all()
            for i, warehouse in enumerate(all_warehouses):
                is_default = (i == 0)  # 第一个仓库设为默认
                user_warehouse = UserWarehouse(
                    user_id=new_user.id,
                    warehouse_id=warehouse.id,
                    is_default=is_default
                )
                db.add(user_warehouse)
            # 设置当前仓库（第一个仓库）
            if all_warehouses:
                new_user.current_warehouse_id = all_warehouses[0].id
        else:
            # 普通操作员，按传入的仓库分配
            for warehouse_id in user.warehouse_ids:
                warehouse = db.query(Warehouse).filter(Warehouse.id == warehouse_id).first()
                if not warehouse:
                    raise HTTPException(status_code=400, detail=f"仓库ID {warehouse_id} 不存在")
                is_default = (user.warehouse_ids.index(warehouse_id) == 0)
                user_warehouse = UserWarehouse(
                    user_id=new_user.id,
                    warehouse_id=warehouse_id,
                    is_default=is_default
                )
                db.add(user_warehouse)
            # 设置当前仓库（第一个）
            if user.warehouse_ids:
                new_user.current_warehouse_id = user.warehouse_ids[0]
        
        db.commit()
        db.refresh(new_user)
        return new_user
    except Exception as e:
        db.rollback()
        logging.exception("创建用户失败: %s", str(e))
        raise HTTPException(status_code=500, detail="创建用户失败")

# 用户切换仓库接口
@router.post("/users/{user_id}/switch-warehouse", summary="切换当前仓库")
async def switch_user_warehouse(
    user_id: int,
    warehouse_id: Optional[int] = Query(None),
    request: Optional[dict] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 获取仓库ID，可以通过查询参数或请求体获取
    if not warehouse_id:
        if request and "warehouse_id" in request:
            warehouse_id = request.get("warehouse_id")
        else:
            raise HTTPException(status_code=400, detail="缺少仓库ID参数")

    # 确保warehouse_id是整数
    try:
        warehouse_id = int(warehouse_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="仓库ID必须是整数")
    # 权限检查：只能切换自己的仓库或管理员操作
    if current_user.id != user_id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")
    
    # 检查用户是否有权限访问该仓库
    user_warehouse = db.query(UserWarehouse).filter(
        UserWarehouse.user_id == user_id,
        UserWarehouse.warehouse_id == warehouse_id
    ).first()
    if not user_warehouse:
        raise HTTPException(status_code=403, detail="用户无权访问该仓库")
    
    # 更新用户当前仓库
    user = db.query(User).filter(User.id == user_id).first()
    user.current_warehouse_id = warehouse_id
    db.commit()
    
    warehouse = db.query(Warehouse).filter(Warehouse.id == warehouse_id).first()
    return {
        "message": "仓库切换成功",
        "current_warehouse_id": warehouse_id,
        "current_warehouse_name": warehouse.name if warehouse else None
    }

# 获取用户可管理的仓库列表
@router.get("/users/{user_id}/warehouses", summary="获取用户可管理的仓库")
async def get_user_warehouses(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.id != user_id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限查看")
    
    user = db.query(User).filter(User.id == user_id).first()
    
    # 如果是管理员，返回所有仓库
    if user.role == UserRole.ADMIN:
        all_warehouses = db.query(Warehouse).filter(Warehouse.is_active == True).all()
        warehouses = []
        for warehouse in all_warehouses:
            user_warehouse = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == user_id,
                UserWarehouse.warehouse_id == warehouse.id
            ).first()
            warehouses.append({
                "id": warehouse.id,
                "code": warehouse.code,
                "name": warehouse.name,
                "is_default": user_warehouse.is_default if user_warehouse else False,
                "is_current": user.current_warehouse_id == warehouse.id
            })
    else:
        # 普通用户，返回分配的仓库
        warehouses = []
        for uw in db.query(UserWarehouse).filter(UserWarehouse.user_id == user_id).all():
            warehouse = db.query(Warehouse).filter(Warehouse.id == uw.warehouse_id).first()
            if warehouse:
                warehouses.append({
                    "id": warehouse.id,
                    "code": warehouse.code,
                    "name": warehouse.name,
                    "is_default": uw.is_default,
                    "is_current": user.current_warehouse_id == warehouse.id
                })
    
    return warehouses

# 获取LDAP配置
@router.get("/ldap/config", summary="获取当前LDAP配置")
async def get_ldap_config(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")

    # 从数据库获取配置
    config = {}
    keys = ["ldap_server", "ldap_base_dn", "ldap_admin_dn", "ldap_admin_password", "ldap_user_search_filter"]

    for key in keys:
        db_config = db.query(Config).filter(Config.key == key).first()
        # 未配置时返回 null；不回退、也不写回任何内置默认值
        config[key] = db_config.value if db_config else None

    if config.get("ldap_admin_password"):
        config["ldap_admin_password"] = "******"
    return config

# 更新LDAP配置
@router.put("/ldap/config", summary="更新LDAP配置")
async def update_ldap_config(
    config_data: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")

    # 允许更新的配置项
    allowed_keys = ["ldap_server", "ldap_base_dn", "ldap_admin_dn", "ldap_admin_password", "ldap_user_search_filter"]

    for key, value in config_data.items():
        if key in allowed_keys:
            if key == "ldap_admin_password" and value == "******":
                continue
            # 查找或创建配置项
            db_config = db.query(Config).filter(Config.key == key).first()
            if db_config:
                db_config.value = value
                db_config.update_time = datetime.now()
            else:
                db_config = Config(
                    key=key,
                    value=value,
                    description=f"LDAP {key}配置"
                )
                db.add(db_config)

    db.commit()

    return {"message": "LDAP配置更新成功"}

# LDAP用户导入
@router.post("/ldap/import-users", summary="导入LDAP用户")
async def import_ldap_users(
    ldap_config: dict = None,  # 允许传入自定义配置，可选
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")

    # 声明为全局变量，以便在函数内部访问和修改
    global LDAP_SERVER, LDAP_BASE_DN, LDAP_ADMIN_DN, LDAP_ADMIN_PASSWORD, LDAP_USER_SEARCH_FILTER

    # 保存原始配置，确保在任何情况下都能恢复
    original_server = LDAP_SERVER
    original_base_dn = LDAP_BASE_DN
    original_admin_dn = LDAP_ADMIN_DN
    original_admin_password = LDAP_ADMIN_PASSWORD
    original_user_filter = LDAP_USER_SEARCH_FILTER

    try:
        # 获取配置（优先级：传入参数 > 数据库配置 > 进程环境变量）
        if ldap_config:
            LDAP_SERVER = (ldap_config.get("ldap_server") or LDAP_SERVER).strip()
            LDAP_BASE_DN = (ldap_config.get("base_dn") or LDAP_BASE_DN).strip()
            LDAP_ADMIN_DN = (ldap_config.get("admin_dn") or LDAP_ADMIN_DN).strip()
            LDAP_ADMIN_PASSWORD = (ldap_config.get("admin_password") or LDAP_ADMIN_PASSWORD).strip()
            LDAP_USER_SEARCH_FILTER = (ldap_config.get("user_filter") or LDAP_USER_SEARCH_FILTER).strip()
        else:
            # 使用数据库中保存的配置
            config_keys = {
                "ldap_server": "ldap_server",
                "base_dn": "ldap_base_dn",
                "admin_dn": "ldap_admin_dn",
                "admin_password": "ldap_admin_password",
                "user_filter": "ldap_user_search_filter"
            }

            for key, db_key in config_keys.items():
                db_config = db.query(Config).filter(Config.key == db_key).first()
                if db_config:
                    if key == "ldap_server":
                        LDAP_SERVER = db_config.value
                    elif key == "base_dn":
                        LDAP_BASE_DN = db_config.value
                    elif key == "admin_dn":
                        LDAP_ADMIN_DN = db_config.value
                    elif key == "admin_password":
                        LDAP_ADMIN_PASSWORD = db_config.value
                    elif key == "user_filter":
                        LDAP_USER_SEARCH_FILTER = db_config.value

        # 配置不完整时直接报错，避免空配置导致不可定位的底层异常
        if not LDAP_SERVER or not LDAP_BASE_DN or not LDAP_ADMIN_DN or not LDAP_ADMIN_PASSWORD or not LDAP_USER_SEARCH_FILTER:
            raise HTTPException(status_code=400, detail="LDAP 配置不完整，请先在 /api/ldap/config 配置或设置 LDAP_* 环境变量")

        # 连接LDAP服务器
        server = ldap3.Server(LDAP_SERVER, get_info=ldap3.ALL)
        conn = ldap3.Connection(server, user=LDAP_ADMIN_DN, password=LDAP_ADMIN_PASSWORD, auto_bind=True)

        # 搜索符合条件的用户
        search_filter = LDAP_USER_SEARCH_FILTER  # 使用传入的用户搜索过滤器
        conn.search(LDAP_BASE_DN, search_filter, attributes=['cn', 'mail', 'givenName', 'sn', 'sAMAccountName', 'uid'])

        imported_count = 0
        skipped_count = 0

        for entry in conn.entries:
            # 尝试获取用户名（优先级：sAMAccountName > uid > cn > 邮箱前缀）
            username = None
            if hasattr(entry, 'sAMAccountName'):
                username = entry.sAMAccountName.value
            elif hasattr(entry, 'uid'):
                username = entry.uid.value
            elif hasattr(entry, 'cn'):
                username = entry.cn.value
            elif hasattr(entry, 'mail'):
                username = entry.mail.value.split('@')[0]

            if not username:
                skipped_count += 1
                continue

            # 检查用户是否已存在
            existing_user = db.query(User).filter(User.username == username).first()
            if existing_user:
                skipped_count += 1
                continue

            # 获取用户姓名（优先级：cn > givenName + sn > 用户名）
            full_name = username
            if hasattr(entry, 'cn'):
                full_name = entry.cn.value
            elif hasattr(entry, 'givenName') and hasattr(entry, 'sn'):
                full_name = f"{entry.givenName.value} {entry.sn.value}"
            elif hasattr(entry, 'givenName'):
                full_name = entry.givenName.value
            elif hasattr(entry, 'sn'):
                full_name = entry.sn.value

            # 生成随机本地临时密码（防止弱口令"123456"成为登录后门；
            # LDAP 用户仍可用域账号通过 LDAP 认证登录）
            temp_password = secrets.token_urlsafe(12)
            hashed_password = get_password_hash(temp_password)

            # 创建本地用户
            new_user = User(
                username=username,
                hashed_password=hashed_password,
                full_name=full_name,
                role=UserRole.OPERATOR,  # 默认角色为操作员
                is_ldap_user=True  # 标识为LDAP用户
            )
            db.add(new_user)
            db.flush()

            # 导入的新用户默认零仓库权限：由管理员显式分配，
            # 不再自动授予所有启用仓库
            imported_count += 1

        db.commit()

        # 恢复全局配置
        LDAP_SERVER = original_server
        LDAP_BASE_DN = original_base_dn
        LDAP_ADMIN_DN = original_admin_dn
        LDAP_ADMIN_PASSWORD = original_admin_password
        LDAP_USER_SEARCH_FILTER = original_user_filter

        return {
            "imported": imported_count,
            "skipped": skipped_count,
            "message": f"成功导入 {imported_count} 个用户，跳过 {skipped_count} 个用户"
        }

    except HTTPException:
        # 恢复全局配置（即使出现错误）
        LDAP_SERVER = original_server
        LDAP_BASE_DN = original_base_dn
        LDAP_ADMIN_DN = original_admin_dn
        LDAP_ADMIN_PASSWORD = original_admin_password
        LDAP_USER_SEARCH_FILTER = original_user_filter
        raise
    except Exception:
        db.rollback()
        # 恢复全局配置（即使出现错误）
        LDAP_SERVER = original_server
        LDAP_BASE_DN = original_base_dn
        LDAP_ADMIN_DN = original_admin_dn
        LDAP_ADMIN_PASSWORD = original_admin_password
        LDAP_USER_SEARCH_FILTER = original_user_filter
        raise HTTPException(status_code=500, detail="导入LDAP用户失败")

# 获取所有用户
@router.get("/users/", summary="获取所有用户")
async def get_all_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 权限校验：仅管理员可查看所有用户
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限查看用户列表")
    
    users = db.query(User).all()
    
    # 返回简化用户信息
    result = []
    for user in users:
        result.append({
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
            "is_active": user.is_active,
            "is_ldap_user": user.is_ldap_user
        })
    return result

# 修改用户（仅管理员可操作）
@router.put("/users/{user_id}", summary="修改用户信息")
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if user_update.full_name is not None:
        user.full_name = user_update.full_name
    if user_update.role is not None:
        user.role = user_update.role
    if user_update.is_active is not None:
        user.is_active = user_update.is_active
    if user_update.password:
        user.hashed_password = get_password_hash(user_update.password)

    db.commit()
    return {"message": "用户更新成功"}

# 为用户分配/取消分配仓库
@router.post("/users/{user_id}/assign-warehouse", summary="为用户分配仓库")
async def assign_warehouse_to_user(
    user_id: int,
    warehouse_id: int,
    is_default: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    
    # 检查是否已分配
    existing = db.query(UserWarehouse).filter(
        UserWarehouse.user_id == user_id,
        UserWarehouse.warehouse_id == warehouse_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="已分配该仓库给用户")
    
    # 如果是设为默认，先取消其他默认
    if is_default:
        db.query(UserWarehouse).filter(
            UserWarehouse.user_id == user_id,
            UserWarehouse.is_default == True
        ).update({"is_default": False})
    
    # 分配新仓库
    user_warehouse = UserWarehouse(
        user_id=user_id,
        warehouse_id=warehouse_id,
        is_default=is_default
    )
    db.add(user_warehouse)
    
    # 如果用户没有当前仓库，设置为当前仓库
    user = db.query(User).filter(User.id == user_id).first()
    if not user.current_warehouse_id:
        user.current_warehouse_id = warehouse_id
    
    db.commit()
    return {"message": "仓库分配成功"}

@router.delete("/users/{user_id}", summary="删除用户")
async def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="仅管理员可操作")

    # 不能删除自己
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="不能删除自己的账户")

    # 查找用户
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 删除用户的仓库关联
    db.query(UserWarehouse).filter(UserWarehouse.user_id == user_id).delete()

    # 删除用户
    db.delete(user)
    db.commit()

    return {"message": "用户删除成功"}

@router.delete("/users/{user_id}/unassign-warehouse", summary="取消用户仓库分配")
async def unassign_warehouse_from_user(
    user_id: int,
    warehouse_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    
    user_warehouse = db.query(UserWarehouse).filter(
        UserWarehouse.user_id == user_id,
        UserWarehouse.warehouse_id == warehouse_id
    ).first()
    if not user_warehouse:
        raise HTTPException(status_code=404, detail="未找到分配记录")
    
    db.delete(user_warehouse)
    
    # 如果这是用户的当前仓库，需要重新设置
    user = db.query(User).filter(User.id == user_id).first()
    if user.current_warehouse_id == warehouse_id:
        # 尝试找默认仓库，没有就找第一个
        default = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == user_id,
            UserWarehouse.is_default == True
        ).first()
        if default:
            user.current_warehouse_id = default.warehouse_id
        else:
            first = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == user_id
            ).first()
            user.current_warehouse_id = first.warehouse_id if first else None
    
    db.commit()
    return {"message": "取消分配成功"}

# ------------------- 仓库管理接口 -------------------
@router.post("/warehouses/", response_model=WarehouseResponse, summary="新增仓库")
async def create_warehouse(
    warehouse: WarehouseCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")
    
    db_warehouse = db.query(Warehouse).filter(Warehouse.code == warehouse.code).first()
    if db_warehouse:
        raise HTTPException(status_code=400, detail="仓库编码已存在")
    
    new_warehouse = Warehouse(**warehouse.dict())
    db.add(new_warehouse)
    db.flush()  # 获取新仓库的ID
    
    # 自动将新仓库分配给所有管理员
    admin_users = db.query(User).filter(User.role == UserRole.ADMIN).all()
    for admin in admin_users:
        # 检查是否已经分配（理论上不会，因为是新建的仓库）
        existing = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == admin.id,
            UserWarehouse.warehouse_id == new_warehouse.id
        ).first()
        if not existing:
            user_warehouse = UserWarehouse(
                user_id=admin.id,
                warehouse_id=new_warehouse.id,
                is_default=False  # 不设为默认，保持原有默认仓库
            )
            db.add(user_warehouse)
    
    db.commit()
    db.refresh(new_warehouse)
    return new_warehouse

@router.get("/warehouses/", response_model=List[WarehouseResponse], summary="获取所有仓库")
async def get_warehouses(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        if current_user.role == UserRole.ADMIN:
            # 管理员可以看到所有仓库，包括禁用的
            warehouses = db.query(Warehouse).all()
            print(f"管理员 {current_user.username} 查询到 {len(warehouses)} 个仓库")
            return warehouses
        else:
            # 非管理员只返回自己有权限的启用的仓库
            user_warehouse_ids = [
                uw.warehouse_id for uw in
                db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
            ]
            print(f"用户 {current_user.username} 有权限的仓库ID: {user_warehouse_ids}")

            if user_warehouse_ids:
                warehouses = db.query(Warehouse).filter(
                    Warehouse.id.in_(user_warehouse_ids),
                    Warehouse.is_active == True
                ).all()
                print(f"用户 {current_user.username} 查询到 {len(warehouses)} 个启用的仓库")
                return warehouses
            else:
                print(f"用户 {current_user.username} 没有分配任何仓库")
                return []
    except Exception as e:
        logging.exception("获取仓库列表出错: %s", str(e))
        raise HTTPException(status_code=500, detail="获取仓库列表失败")

@router.get("/warehouses/{id}", response_model=WarehouseResponse, summary="获取仓库详情")
async def get_warehouse(id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # 获取仓库信息
    db_warehouse = db.query(Warehouse).filter(Warehouse.id == id).first()

    if not db_warehouse:
        raise HTTPException(status_code=404, detail="仓库未找到")

    # 检查用户权限
    if current_user.role != UserRole.ADMIN:
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == id
        ).first()

        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无权限访问此仓库")

    return db_warehouse

@router.put("/warehouses/{id}", response_model=WarehouseResponse, summary="修改仓库信息")
async def update_warehouse(
    id: int,
    warehouse: WarehouseUpdate,  # 使用 Pydantic 模型
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")
    
    db_warehouse = db.query(Warehouse).filter(Warehouse.id == id).first()
    if not db_warehouse:
        raise HTTPException(status_code=404, detail="仓库未找到")
    
    # 只更新提供的字段
    for key, value in warehouse.dict(exclude_unset=True).items():
        setattr(db_warehouse, key, value)
    
    db.commit()
    db.refresh(db_warehouse)
    return db_warehouse

# ------------------- 库位管理接口 -------------------
@router.post("/locations/", response_model=LocationResponse, summary="新增库位")
async def create_location(
    location: LocationCreate, 
    current_user: User = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    # 库位主数据维护仅限管理员（与货物管理一致）
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="库位维护仅限管理员操作")

    db_location = db.query(Location).filter(Location.location_code == location.location_code).first()
    if db_location:
        raise HTTPException(status_code=400, detail="库位编码已存在")
    
    new_location = Location(**location.dict())
    db.add(new_location)
    db.commit()
    db.refresh(new_location)
    return new_location

@router.get("/locations/", response_model=List[LocationResponse], summary="获取库位列表")
async def get_locations(
    warehouse_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        # 如果是管理员，返回所有库位，包括禁用的
        if current_user.role == UserRole.ADMIN:
            if warehouse_id:
                locations = db.query(Location).filter(Location.warehouse_id == warehouse_id).all()
            else:
                locations = db.query(Location).all()
        else:
            # 非管理员，获取用户可管理的所有仓库ID，只返回启用的库位
            user_warehouse_ids = [
                uw.warehouse_id for uw in
                db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
            ]
            if user_warehouse_ids:
                query = db.query(Location).filter(
                    Location.is_active == True,
                    Location.warehouse_id.in_(user_warehouse_ids)
                )
                if warehouse_id and warehouse_id in user_warehouse_ids:
                    query = query.filter(Location.warehouse_id == warehouse_id)
                locations = query.all()
            else:
                # 用户没有分配任何仓库，返回空列表
                locations = []

        # 确保返回正确的字段
        result = []
        for loc in locations:
            result.append({
                "id": loc.id,
                "warehouse_id": loc.warehouse_id,
                "location_code": loc.location_code,
                "name": loc.name,
                "is_active": loc.is_active,
                "create_time": loc.create_time
            })
        return result
    except Exception as e:
        # 记录错误日志
        logging.exception("获取库位列表出错: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail="获取库位列表失败"
        )

@router.put("/locations/{id}", response_model=LocationResponse, summary="修改库位信息")
async def update_location(
    id: int,
    location: LocationUpdate,  
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 库位主数据维护仅限管理员（与新增/删除保持一致）
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="库位维护仅限管理员操作")

    db_location = db.query(Location).filter(Location.id == id).first()
    if not db_location:
        raise HTTPException(status_code=404, detail="库位未找到")

    data = location.dict(exclude_unset=True)
    # 禁止通过修改接口变更库位所属仓库（跨仓库移动需删除后重建）
    new_warehouse_id = data.get("warehouse_id")
    if new_warehouse_id is not None and new_warehouse_id != db_location.warehouse_id:
        raise HTTPException(status_code=400, detail="不允许修改库位所属仓库")
    data.pop("warehouse_id", None)

    for key, value in data.items():
        setattr(db_location, key, value)
    
    db.commit()
    db.refresh(db_location)
    return db_location

@router.delete("/locations/{id}", summary="删除库位")
async def delete_location(
    id: int, 
    current_user: User = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")
    
    db_location = db.query(Location).filter(Location.id == id).first()
    if not db_location:
        raise HTTPException(status_code=404, detail="库位未找到")
    
    db.delete(db_location)
    db.commit()
    return {"message": "删除成功"}

# ------------------- 货物管理接口 -------------------
@router.post("/goods/", response_model=GoodsResponse, summary="新增货物")
async def create_goods(
    goods: GoodsCreate, 
    current_user: User = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    # 货物主数据维护仅限管理员
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")
    db_goods = db.query(Goods).filter(Goods.barcode == goods.barcode).first()
    if db_goods:
        raise HTTPException(status_code=400, detail="货物条码已存在")
    
    new_goods = Goods(**goods.dict())
    db.add(new_goods)
    db.commit()
    db.refresh(new_goods)
    return new_goods

@router.get("/goods/", response_model=List[GoodsResponse], summary="获取所有货物（支持搜索）")
async def get_goods(
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取货物列表，支持通过货物名称、条码或规格搜索"""
    query = db.query(Goods)

    if keyword:
        query = query.filter(
            Goods.name.contains(keyword) |
            Goods.barcode.contains(keyword) |
            Goods.spec.contains(keyword)
        )

    return query.all()

@router.get("/goods/export", summary="导出货物数据")
async def export_goods(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """导出货物数据为Excel文件"""
    try:
        # 获取所有货物数据
        goods_list = db.query(Goods).all()

        # 转换为DataFrame
        data = []
        for goods in goods_list:
            data.append({
                "条码": goods.barcode,
                "货物名称": goods.name,
                "规格型号": goods.spec,
                "单位": goods.unit,
                "单价": goods.price,
                "创建时间": goods.create_time.strftime("%Y-%m-%d %H:%M:%S")
            })

        df = pd.DataFrame(data)

        # 写入Excel文件
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='货物数据')

        output.seek(0)

        # 返回响应
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=goods_export.xlsx"}
        )
    except Exception as e:
        logging.exception("导出失败: %s", str(e))
        raise HTTPException(status_code=500, detail="导出失败")

@router.get("/goods/{id}", response_model=GoodsResponse, summary="获取单个货物详情")
async def get_goods_detail(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_goods = db.query(Goods).filter(Goods.id == id).first()
    if not db_goods:
        raise HTTPException(status_code=404, detail="货物未找到")
    return db_goods

@router.put("/goods/{id}", response_model=GoodsResponse, summary="修改货物信息")
async def update_goods(
    id: int,
    goods: GoodsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 货物主数据维护仅限管理员
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")
    db_goods = db.query(Goods).filter(Goods.id == id).first()
    if not db_goods:
        raise HTTPException(status_code=404, detail="货物未找到")
    
    for key, value in goods.dict(exclude_unset=True).items():
        setattr(db_goods, key, value)
    
    db.commit()
    db.refresh(db_goods)
    return db_goods

@router.delete("/goods/{id}", summary="删除货物")
async def delete_goods(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")

    db_goods = db.query(Goods).filter(Goods.id == id).first()
    if not db_goods:
        raise HTTPException(status_code=404, detail="货物未找到")

    db.delete(db_goods)
    db.commit()
    return {"message": "删除成功"}

@router.get("/goods/export", summary="导出货物数据")
async def export_goods(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """导出货物数据为Excel文件"""
    try:
        # 获取所有货物数据
        goods_list = db.query(Goods).all()

        # 转换为DataFrame
        data = []
        for goods in goods_list:
            data.append({
                "条码": goods.barcode,
                "货物名称": goods.name,
                "规格型号": goods.spec,
                "单位": goods.unit,
                "单价": goods.price,
                "创建时间": goods.create_time.strftime("%Y-%m-%d %H:%M:%S")
            })

        df = pd.DataFrame(data)

        # 写入Excel文件
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='货物数据')

        output.seek(0)

        # 返回响应
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=goods_export.xlsx"}
        )
    except Exception as e:
        logging.exception("导出失败: %s", str(e))
        raise HTTPException(status_code=500, detail="导出失败")

@router.post("/goods/import", summary="导入货物数据")
async def import_goods(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """从Excel文件导入货物数据"""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")

    try:
        # 读取Excel文件
        if not file.filename.lower().endswith((".xls", ".xlsx")):
            raise HTTPException(status_code=400, detail="仅支持导入 .xls 或 .xlsx 文件")
        contents = await read_upload_with_size_limit(file, MAX_IMPORT_FILE_SIZE_BYTES)
        df = pd.read_excel(io.BytesIO(contents))

        # 检查必要的列
        required_columns = ["条码", "货物名称", "规格型号", "单位", "单价"]
        for col in required_columns:
            if col not in df.columns:
                raise HTTPException(status_code=400, detail=f"缺少必要列：{col}")

        # 导入数据
        success_count = 0
        error_count = 0
        errors = []

        for index, row in df.iterrows():
            try:
                # 检查条码是否已存在
                existing_goods = db.query(Goods).filter(Goods.barcode == str(row["条码"])).first()
                if existing_goods:
                    error_count += 1
                    errors.append(f"第{index+1}行：条码{row['条码']}已存在")
                    continue

                # 创建新货物
                new_goods = Goods(
                    barcode=str(row["条码"]),
                    name=str(row["货物名称"]),
                    spec=str(row["规格型号"]) if pd.notna(row["规格型号"]) else "",
                    unit=str(row["单位"]) if pd.notna(row["单位"]) else "个",
                    price=float(row["单价"]) if pd.notna(row["单价"]) else 0.0
                )

                db.add(new_goods)
                success_count += 1
            except Exception as e:
                error_count += 1
                errors.append(f"第{index+1}行：数据处理失败")

        db.commit()

        return {
            "success_count": success_count,
            "error_count": error_count,
            "errors": errors
        }
    except Exception as e:
        db.rollback()
        logging.exception("导入失败: %s", str(e))
        raise HTTPException(status_code=500, detail="导入失败")

# ------------------- 扫码出入库接口 -------------------
@router.post("/inventory/scan", summary="扫码出入库")
async def scan_inventory(
    inventory: InventoryCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 1. 查询货物
    goods = db.query(Goods).filter(Goods.barcode == inventory.goods_barcode).first()
    if not goods:
        raise HTTPException(status_code=404, detail="货物不存在")

    # 2. 查询库位并校验所属仓库
    location = db.query(Location).filter(Location.location_code == inventory.location_code).first()
    if not location:
        raise HTTPException(status_code=404, detail="库位不存在")

    # 检查用户是否有权限操作这个仓库
    user_warehouse = db.query(UserWarehouse).filter(
        UserWarehouse.user_id == current_user.id,
        UserWarehouse.warehouse_id == location.warehouse_id
    ).first()
    if not user_warehouse:
        raise HTTPException(status_code=403, detail="无权限操作其他仓库库位")

    # 3. 处理出入库逻辑（行级锁读取库存行，防止并发超卖/丢失更新）
    warehouse_id = location.warehouse_id
    stock = lock_stock_row(db, warehouse_id, goods.id, location.id)

    if inventory.type == InventoryType.IN:
        # 入库
        if not stock:
            # 库存行尚不存在：先持咨询锁串行化并发建行（防止并发首笔扫码
            # 各插一条触发复合唯一约束），再重查
            advisory_lock_stock_key(db, warehouse_id, goods.id, location.id)
            stock = lock_stock_row(db, warehouse_id, goods.id, location.id)
        if stock:
            stock.quantity += inventory.quantity
            stock.update_time = datetime.now()
        else:
            stock = Stock(
                warehouse_id=warehouse_id,
                goods_id=goods.id,
                location_id=location.id,
                quantity=inventory.quantity
            )
            db.add(stock)

        # 创建入库单
        order_no = generate_order_no("IN", db)
        new_order = InboundOrderHeader(
            order_no=order_no,
            warehouse_id=warehouse_id,
            supplier="扫码入库",
            operator_id=current_user.id,
            remark=inventory.remark,
            status="COMPLETED"  # 直接设置为已完成
        )
        db.add(new_order)
        db.flush()  # 获取自增ID；不在这里提交，保持整单原子性

        # 添加明细项
        unit_price = goods.price
        total_price = unit_price * inventory.quantity
        new_item = InboundOrderItem(
            header_id=new_order.id,
            goods_id=goods.id,
            location_id=location.id,
            quantity=inventory.quantity,
            unit_price=unit_price,
            total_price=total_price,
            remark=inventory.remark
        )
        new_order.total_amount = total_price
        db.add(new_item)
    else:
        # 出库
        if not stock or stock.quantity < inventory.quantity:
            raise HTTPException(status_code=400, detail="库存不足")
        stock.quantity -= inventory.quantity

        # 创建出库单
        order_no = generate_order_no("OUT", db)
        new_order = OutboundOrderHeader(
            order_no=order_no,
            warehouse_id=warehouse_id,
            customer="扫码出库",
            operator_id=current_user.id,
            remark=inventory.remark,
            status="COMPLETED"  # 直接设置为已完成
        )
        db.add(new_order)
        db.flush()  # 获取自增ID；不在这里提交，保持整单原子性

        # 添加明细项
        unit_price = goods.price
        total_price = unit_price * inventory.quantity
        new_item = OutboundOrderItem(
            header_id=new_order.id,
            goods_id=goods.id,
            location_id=location.id,
            quantity=inventory.quantity,
            unit_price=unit_price,
            total_price=total_price,
            remark=inventory.remark
        )
        new_order.total_amount = total_price
        db.add(new_item)

    # 4. 记录出入库日志
    record = InventoryRecord(
        warehouse_id=warehouse_id,
        goods_id=goods.id,
        location_id=location.id,
        type=inventory.type,
        quantity=inventory.quantity,
        operator_id=current_user.id,
        remark=inventory.remark
    )
    db.add(record)
    db.commit()

    return {
        "message": f"{inventory.type}成功",
        "goods_name": goods.name,
        "location_name": location.name,
        "current_stock": stock.quantity
    }

# ------------------- 库存查询接口（带仓库+库位） -------------------
@router.get("/stock/", response_model=List[StockResponse], summary="库存查询（带仓库库位）")
async def get_stock(
    warehouse_id: Optional[int] = Query(None, description="按仓库ID过滤"),
    location_id: Optional[int] = Query(None, description="按库位ID过滤"),
    goods_barcode: Optional[str] = Query(None, description="按货物条码过滤"),
    keyword: Optional[str] = Query(None, description="按货物名称/条码/库位模糊查询"),
    limit: Optional[int] = Query(None, ge=1, le=5000, description="限制返回条数"),
    offset: int = Query(0, ge=0, description="返回偏移量"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    query = (
        db.query(
            func.min(Stock.id).label("id"),
            Stock.warehouse_id.label("warehouse_id"),
            Warehouse.name.label("warehouse_name"),
            Stock.goods_id.label("goods_id"),
            Goods.name.label("goods_name"),
            Goods.barcode.label("goods_barcode"),
            func.coalesce(Goods.price, 0).label("goods_price"),
            func.coalesce(Goods.spec, "").label("goods_spec"),
            func.coalesce(Goods.unit, "").label("goods_unit"),
            Stock.location_id.label("location_id"),
            Location.location_code.label("location_code"),
            Location.name.label("location_name"),
            func.coalesce(func.sum(Stock.quantity), 0).label("quantity"),
            func.max(Stock.update_time).label("update_time"),
        )
        .join(Warehouse, Stock.warehouse_id == Warehouse.id)
        .join(Goods, Stock.goods_id == Goods.id)
        .join(Location, Stock.location_id == Location.id)
    )

    if warehouse_id is not None:
        query = query.filter(Stock.warehouse_id == warehouse_id)

    if location_id is not None:
        query = query.filter(Stock.location_id == location_id)

    if goods_barcode:
        query = query.filter(Goods.barcode == goods_barcode.strip())

    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.filter(
            or_(
                Goods.name.ilike(like),
                Goods.barcode.ilike(like),
                Location.location_code.ilike(like),
                Location.name.ilike(like),
            )
        )

    if current_user.role != UserRole.ADMIN:
        # 获取用户有权限的仓库
        user_warehouse_ids = [
            uw.warehouse_id for uw in
            db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
        ]
        if user_warehouse_ids:
            query = query.filter(Stock.warehouse_id.in_(user_warehouse_ids))
        else:
            return []

    query = query.group_by(
        Stock.warehouse_id,
        Warehouse.name,
        Stock.goods_id,
        Goods.name,
        Goods.barcode,
        Goods.price,
        Goods.spec,
        Goods.unit,
        Stock.location_id,
        Location.location_code,
        Location.name,
    ).order_by(
        Stock.warehouse_id.asc(),
        Goods.barcode.asc(),
        Stock.location_id.asc(),
    )

    if limit is not None:
        query = query.offset(offset).limit(limit)

    rows = query.all()
    return [
        {
            "id": row.id,
            "warehouse_id": row.warehouse_id,
            "warehouse_name": row.warehouse_name,
            "goods_id": row.goods_id,
            "goods_name": row.goods_name,
            "goods_barcode": row.goods_barcode,
            "goods_price": float(row.goods_price or 0),
            "goods_spec": row.goods_spec or "",
            "goods_unit": row.goods_unit or "",
            "location_id": row.location_id,
            "location_code": row.location_code,
            "location_name": row.location_name,
            "quantity": float(row.quantity or 0),
            "update_time": row.update_time,
        }
        for row in rows
    ]

# ------------------- 扫码盘点接口 -------------------
@router.post("/check/scan", summary="扫码盘点")
async def scan_check(
    check: CheckCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 查询货物和库位
    goods = db.query(Goods).filter(Goods.barcode == check.goods_barcode).first()
    location = db.query(Location).filter(Location.location_code == check.location_code).first()
    
    if not goods or not location:
        raise HTTPException(status_code=404, detail="货物或库位不存在")
    
    # 权限校验
    user_warehouse = db.query(UserWarehouse).filter(
        UserWarehouse.user_id == current_user.id,
        UserWarehouse.warehouse_id == location.warehouse_id
    ).first()
    if not user_warehouse:
        raise HTTPException(status_code=403, detail="无权限操作其他仓库库位")
    
    # 查询实际库存
    warehouse_id = location.warehouse_id
    stock = db.query(Stock).filter(
        Stock.warehouse_id == warehouse_id,
        Stock.goods_id == goods.id,
        Stock.location_id == location.id
    ).first()
    
    actual_quantity = stock.quantity if stock else 0.0
    
    # 记录盘点
    record = CheckRecord(
        warehouse_id=warehouse_id,
        goods_id=goods.id,
        location_id=location.id,
        check_quantity=check.check_quantity,
        actual_quantity=actual_quantity,
        operator_id=current_user.id
    )
    db.add(record)
    db.commit()
    
    return {
        "goods_name": goods.name,
        "location_name": location.name,
        "check_quantity": check.check_quantity,
        "actual_quantity": actual_quantity,
        "diff": check.check_quantity - actual_quantity,
        "message": "库存一致" if actual_quantity == check.check_quantity else "库存不符"
    }

# 获取出入库记录
@router.get("/inventory/logs", summary="获取出入库记录")
async def get_inventory_logs(
    warehouse_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: Optional[int] = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    query = db.query(InventoryRecord).join(
        Goods, InventoryRecord.goods_id == Goods.id
    ).join(
        Location, InventoryRecord.location_id == Location.id
    ).join(
        Warehouse, InventoryRecord.warehouse_id == Warehouse.id
    ).join(
        User, InventoryRecord.operator_id == User.id
    )

    if current_user.role != UserRole.ADMIN:
        # 操作员仅可查看当前仓库日志
        if not current_user.current_warehouse_id:
            return []
        query = query.filter(InventoryRecord.warehouse_id == current_user.current_warehouse_id)

    # 管理员可按仓库筛选全部日志；操作员传入warehouse_id也仅允许当前仓库
    if warehouse_id:
        query = query.filter(InventoryRecord.warehouse_id == warehouse_id)

    if start_date:
        start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
        query = query.filter(InventoryRecord.create_time >= start_datetime)

    if end_date:
        end_datetime = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        query = query.filter(InventoryRecord.create_time < end_datetime)

    # 排序和限制返回条数
    query = query.order_by(InventoryRecord.create_time.desc())
    if limit:
        query = query.limit(limit)

    records = query.all()

    result = []
    for record in records:
        result.append({
            "id": record.id,
            "warehouse_id": record.warehouse_id,
            "warehouse_name": record.warehouse.name,
            "goods_id": record.goods_id,
            "goods_name": record.goods.name,
            "goods_barcode": record.goods.barcode,
            "location_code": record.location.location_code,
            "location_name": record.location.name,
            "type": record.type,
            "quantity": record.quantity,
            "operator_name": record.operator.full_name,
            "remark": record.remark,
            "create_time": record.create_time,
            "status": "SUCCESS"
        })
    return result

# 获取盘点记录
@router.get("/check/records", summary="获取盘点记录")
async def get_check_records(
    warehouse_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    query = db.query(CheckRecord).join(
        Goods, CheckRecord.goods_id == Goods.id
    ).join(
        Location, CheckRecord.location_id == Location.id
    ).join(
        Warehouse, CheckRecord.warehouse_id == Warehouse.id
    ).join(
        User, CheckRecord.operator_id == User.id
    )

    if current_user.role != UserRole.ADMIN:
        # 操作员仅可查看当前仓库盘点记录
        if not current_user.current_warehouse_id:
            return []
        query = query.filter(CheckRecord.warehouse_id == current_user.current_warehouse_id)

    if warehouse_id:
        query = query.filter(CheckRecord.warehouse_id == warehouse_id)

    if start_date:
        start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
        query = query.filter(CheckRecord.check_time >= start_datetime)

    if end_date:
        end_datetime = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        query = query.filter(CheckRecord.check_time < end_datetime)

    records = query.order_by(CheckRecord.check_time.desc()).all()

    result = []
    for record in records:
        diff = record.check_quantity - record.actual_quantity
        result.append({
            "id": record.id,
            "warehouse_id": record.warehouse_id,
            "warehouse_name": record.warehouse.name,
            "goods_name": record.goods.name,
            "goods_barcode": record.goods.barcode,
            "location_code": record.location.location_code,
            "location_name": record.location.name,
            "actual_quantity": record.actual_quantity,
            "check_quantity": record.check_quantity,
            "diff": diff,
            "check_time": record.check_time,
            "operator_name": record.operator.full_name
        })
    return result


# 获取盘点统计
@router.get("/check/stats", summary="获取盘点统计")
async def get_check_stats(
    warehouse_id: Optional[int] = None,
    date: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if date:
        start_datetime = datetime.strptime(date, "%Y-%m-%d")
    else:
        start_datetime = datetime.combine(datetime.now().date(), datetime.min.time())
    end_datetime = start_datetime + timedelta(days=1)

    query = db.query(CheckRecord).filter(
        CheckRecord.check_time >= start_datetime,
        CheckRecord.check_time < end_datetime
    )

    if current_user.role != UserRole.ADMIN:
        user_warehouse_ids = [
            uw.warehouse_id for uw in
            db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
        ]
        if user_warehouse_ids:
            query = query.filter(CheckRecord.warehouse_id.in_(user_warehouse_ids))
        else:
            query = query.filter(CheckRecord.warehouse_id == -1)

    if warehouse_id:
        query = query.filter(CheckRecord.warehouse_id == warehouse_id)

    records = query.all()
    matched = sum(1 for record in records if abs(record.check_quantity - record.actual_quantity) < 0.0001)
    diff_checks = len(records) - matched
    checked_goods = len({record.goods_id for record in records})

    return {
        "todayChecks": len(records),
        "matchedChecks": matched,
        "diffChecks": diff_checks,
        "checkedGoods": checked_goods
    }


# 获取盘点差异报表
@router.get("/check/diffs", summary="获取盘点差异报表")
async def get_check_diffs(
    warehouse_id: Optional[int] = None,
    date: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if date:
        start_datetime = datetime.strptime(date, "%Y-%m-%d")
        end_datetime = start_datetime + timedelta(days=1)
    else:
        start_datetime = None
        end_datetime = None

    query = db.query(CheckRecord).join(
        Goods, CheckRecord.goods_id == Goods.id
    ).join(
        Location, CheckRecord.location_id == Location.id
    ).join(
        Warehouse, CheckRecord.warehouse_id == Warehouse.id
    ).join(
        User, CheckRecord.operator_id == User.id
    )

    if current_user.role != UserRole.ADMIN:
        user_warehouse_ids = [
            uw.warehouse_id for uw in
            db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
        ]
        if user_warehouse_ids:
            query = query.filter(CheckRecord.warehouse_id.in_(user_warehouse_ids))
        else:
            query = query.filter(CheckRecord.warehouse_id == -1)

    if warehouse_id:
        query = query.filter(CheckRecord.warehouse_id == warehouse_id)

    if start_datetime and end_datetime:
        query = query.filter(
            CheckRecord.check_time >= start_datetime,
            CheckRecord.check_time < end_datetime
        )

    records = query.order_by(CheckRecord.check_time.desc()).all()
    result = []
    for record in records:
        diff = record.check_quantity - record.actual_quantity
        if abs(diff) < 0.0001:
            continue
        result.append({
            "id": record.id,
            "warehouse_id": record.warehouse_id,
            "warehouse_name": record.warehouse.name,
            "goods_name": record.goods.name,
            "goods_barcode": record.goods.barcode,
            "location_code": record.location.location_code,
            "location_name": record.location.name,
            "actual_quantity": record.actual_quantity,
            "check_quantity": record.check_quantity,
            "diff": diff,
            "check_time": record.check_time,
            "operator_name": record.operator.full_name
        })
    return result

# ------------------- 盘点单管理接口 -------------------
# 创建盘点单请求模型
class CheckOrderCreate(BaseModel):
    warehouse_id: Optional[int] = None
    remark: Optional[str] = None

# 盘点单响应模型
class CheckOrderHeaderResponse(BaseModel):
    id: int
    order_no: str
    warehouse_id: int
    warehouse_name: str
    operator_id: int
    operator_name: str
    remark: Optional[str]
    status: str
    create_time: datetime
    start_time: Optional[datetime]
    complete_time: Optional[datetime]
    item_count: int

    class Config:
        from_attributes = True

# 盘点单明细响应模型
class CheckOrderItemResponse(BaseModel):
    id: int
    goods_id: int
    goods_name: str
    goods_barcode: str
    location_id: int
    location_code: str
    location_name: str
    check_quantity: float
    actual_quantity: float
    diff_quantity: float
    create_time: datetime

    class Config:
        from_attributes = True

# 完整盘点单响应模型（包含明细）
class CheckOrderFullResponse(BaseModel):
    header: CheckOrderHeaderResponse
    items: List[CheckOrderItemResponse]

# 创建盘点单
@router.post("/check-orders/", response_model=CheckOrderHeaderResponse, summary="创建盘点单")
async def create_check_order(
    order: CheckOrderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 确定仓库ID
    if order.warehouse_id:
        warehouse_id = order.warehouse_id
    elif current_user.current_warehouse_id:
        warehouse_id = current_user.current_warehouse_id
    else:
        raise HTTPException(status_code=400, detail="请选择仓库或设置默认仓库")

    # 权限校验
    if current_user.role != UserRole.ADMIN:
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无该仓库权限")

    # 生成盘点单号（格式：CK + 年月日 + 5位流水号；PostgreSQL 用咨询锁串行化，防止并发撞号）
    today = datetime.now().strftime("%Y%m%d")
    try:
        if engine.dialect.name == "postgresql":
            from sqlalchemy import text as _sa_text
            db.execute(_sa_text("SELECT pg_advisory_xact_lock(:k)"),
                       {"k": int(zlib.crc32(f"orderno-CK-{today}".encode("utf-8")))} )
    except Exception:
        pass
    # 查询当日最大流水号
    last_order = db.query(CheckOrderHeader).filter(
        CheckOrderHeader.order_no.like(f"CK{today}%")
    ).order_by(CheckOrderHeader.order_no.desc()).first()

    if last_order:
        last_seq = int(last_order.order_no[-5:])
        new_seq = last_seq + 1
    else:
        new_seq = 1

    order_no = f"CK{today}{new_seq:05d}"

    # 创建盘点单
    db_order = CheckOrderHeader(
        order_no=order_no,
        warehouse_id=warehouse_id,
        operator_id=current_user.id,
        remark=order.remark,
        status="DRAFT"
    )
    db.add(db_order)
    db.commit()
    db.refresh(db_order)

    # 返回响应
    return {
        "id": db_order.id,
        "order_no": db_order.order_no,
        "warehouse_id": db_order.warehouse_id,
        "warehouse_name": db.query(Warehouse).get(warehouse_id).name,
        "operator_id": db_order.operator_id,
        "operator_name": current_user.full_name,
        "remark": db_order.remark,
        "status": db_order.status,
        "create_time": db_order.create_time,
        "start_time": db_order.start_time,
        "complete_time": db_order.complete_time,
        "item_count": 0
    }

# 获取盘点单列表
@router.get("/check-orders/", summary="获取盘点单列表")
async def get_check_orders(
    warehouse_id: Optional[int] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    query = db.query(CheckOrderHeader).join(Warehouse, CheckOrderHeader.warehouse_id == Warehouse.id)

    # 权限校验
    if current_user.role != UserRole.ADMIN:
        user_warehouse_ids = [
            uw.warehouse_id for uw in
            db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
        ]
        if user_warehouse_ids:
            query = query.filter(CheckOrderHeader.warehouse_id.in_(user_warehouse_ids))
        else:
            return []

    # 过滤条件
    if warehouse_id:
        query = query.filter(CheckOrderHeader.warehouse_id == warehouse_id)

    if status:
        query = query.filter(CheckOrderHeader.status == status)

    if start_date:
        start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
        query = query.filter(CheckOrderHeader.create_time >= start_datetime)

    if end_date:
        end_datetime = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        query = query.filter(CheckOrderHeader.create_time < end_datetime)

    # 分页
    total_count = query.count()
    offset = (page - 1) * page_size
    orders = query.order_by(CheckOrderHeader.create_time.desc()).offset(offset).limit(page_size).all()

    # 构建响应数据
    result = []
    for order in orders:
        item_count = db.query(CheckOrderItem).filter(CheckOrderItem.header_id == order.id).count()
        result.append({
            "id": order.id,
            "order_no": order.order_no,
            "warehouse_id": order.warehouse_id,
            "warehouse_name": order.warehouse.name,
            "operator_id": order.operator_id,
            "operator_name": order.operator.full_name,
            "remark": order.remark,
            "status": order.status,
            "create_time": order.create_time,
            "start_time": order.start_time,
            "complete_time": order.complete_time,
            "item_count": item_count
        })

    return {
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "data": result
    }

# 获取盘点单详情（包含明细）
@router.get("/check-orders/{order_id}", response_model=CheckOrderFullResponse, summary="获取盘点单详情")
async def get_check_order_detail(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 查询盘点单
    order = db.query(CheckOrderHeader).filter(CheckOrderHeader.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="盘点单不存在")

    # 权限校验
    if current_user.role != UserRole.ADMIN:
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无该仓库权限")

    # 查询明细
    items = db.query(CheckOrderItem).filter(CheckOrderItem.header_id == order_id).all()

    # 构建响应
    header_response = CheckOrderHeaderResponse(
        id=order.id,
        order_no=order.order_no,
        warehouse_id=order.warehouse_id,
        warehouse_name=order.warehouse.name,
        operator_id=order.operator_id,
        operator_name=order.operator.full_name,
        remark=order.remark,
        status=order.status,
        create_time=order.create_time,
        start_time=order.start_time,
        complete_time=order.complete_time,
        item_count=len(items)
    )

    items_response = []
    for item in items:
        items_response.append(CheckOrderItemResponse(
            id=item.id,
            goods_id=item.goods_id,
            goods_name=item.goods.name,
            goods_barcode=item.goods.barcode,
            location_id=item.location_id,
            location_code=item.location.location_code,
            location_name=item.location.name,
            check_quantity=item.check_quantity,
            actual_quantity=item.actual_quantity,
            diff_quantity=item.diff_quantity,
            create_time=item.create_time
        ))

    return {"header": header_response, "items": items_response}

# 添加盘点明细（扫码盘点）
class CheckOrderItemCreate(BaseModel):
    header_id: int
    goods_barcode: str
    location_code: str
    check_quantity: float = Field(ge=0)

@router.post("/check-orders/items/", summary="添加盘点明细（扫码盘点）")
async def add_check_order_item(
    item: CheckOrderItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 查询盘点单
    order = db.query(CheckOrderHeader).filter(CheckOrderHeader.id == item.header_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="盘点单不存在")

    # 检查盘点单状态
    if order.status == "COMPLETED":
        raise HTTPException(status_code=400, detail="盘点单已完成，无法添加明细")

    # 权限校验
    if current_user.role != UserRole.ADMIN:
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无该仓库权限")

    # 查询货物和库位
    goods = db.query(Goods).filter(Goods.barcode == item.goods_barcode).first()
    location = db.query(Location).filter(Location.location_code == item.location_code).first()

    if not goods or not location:
        raise HTTPException(status_code=404, detail="货物或库位不存在")

    # 货物为全局主数据，仅校验库位所属仓库
    if location.warehouse_id != order.warehouse_id:
        raise HTTPException(status_code=400, detail="库位不属于该盘点单仓库")

    # 获取系统库存
    stock = db.query(Stock).filter(
        Stock.warehouse_id == order.warehouse_id,
        Stock.goods_id == goods.id,
        Stock.location_id == location.id
    ).first()

    actual_quantity = stock.quantity if stock else 0.0

    # 计算差异
    diff_quantity = item.check_quantity - actual_quantity

    # 创建或更新明细
    existing_item = db.query(CheckOrderItem).filter(
        CheckOrderItem.header_id == item.header_id,
        CheckOrderItem.goods_id == goods.id,
        CheckOrderItem.location_id == location.id
    ).first()

    if existing_item:
        # 更新现有明细
        existing_item.check_quantity = item.check_quantity
        existing_item.actual_quantity = actual_quantity
        existing_item.diff_quantity = diff_quantity
        existing_item.create_time = datetime.now()
    else:
        # 创建新明细
        existing_item = CheckOrderItem(
            header_id=item.header_id,
            goods_id=goods.id,
            location_id=location.id,
            check_quantity=item.check_quantity,
            actual_quantity=actual_quantity,
            diff_quantity=diff_quantity
        )
        db.add(existing_item)

    # 如果盘点单状态是草稿，设置为盘点中
    if order.status == "DRAFT":
        order.status = "IN_PROGRESS"
        order.start_time = datetime.now()

    db.commit()
    db.refresh(existing_item)
    db.refresh(order)

    # 同时创建盘点记录（保留历史）
    db_record = CheckRecord(
        warehouse_id=order.warehouse_id,
        goods_id=goods.id,
        location_id=location.id,
        check_quantity=item.check_quantity,
        actual_quantity=actual_quantity,
        operator_id=current_user.id
    )
    db.add(db_record)
    db.commit()

    return {
        "id": existing_item.id,
        "goods_name": goods.name,
        "goods_barcode": goods.barcode,
        "location_code": location.location_code,
        "location_name": location.name,
        "check_quantity": existing_item.check_quantity,
        "actual_quantity": existing_item.actual_quantity,
        "diff_quantity": existing_item.diff_quantity,
        "order_status": order.status
    }

# 完成盘点
@router.post("/check-orders/{order_id}/complete", summary="完成盘点")
async def complete_check_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 查询盘点单（单据头行级锁：串行化同一盘点单的并发完成，防止两个并发完成
    # 都看到"未完成"而各自回写/新建库存）
    order = lock_order_header(db, CheckOrderHeader, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="盘点单不存在")

    # 检查盘点单状态（持锁后判定：并发完成中先提交者置 COMPLETED，后到者在此被拒）
    if order.status == "COMPLETED":
        raise HTTPException(status_code=400, detail="盘点单已完成")

    if order.status == "DRAFT":
        raise HTTPException(status_code=400, detail="盘点单尚未开始，无法完成")

    # 权限校验
    if current_user.role != UserRole.ADMIN:
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无该仓库权限")

    # 将盘点差异回写到库存（行级锁，防止并发下丢失更新）
    # 冲突检测：录入时记录的系统库存基线（item.actual_quantity）与当前库存不一致，
    # 说明盘点期间发生了出入库 —— 直接覆盖会抹掉期间合法变动，必须要求重盘。
    items = db.query(CheckOrderItem).filter(CheckOrderItem.header_id == order_id).all()
    for item in items:
        stock = lock_stock_row(db, order.warehouse_id, item.goods_id, item.location_id)
        if stock is None:
            # 与入库/扫码入库同一把咨询锁：串行化"同一 (仓库,货物,库位)"的并发建行，
            # 避免盘点完成与首次扫码入库并发时都看到"无库存行"而各建一条。
            advisory_lock_stock_key(db, order.warehouse_id, item.goods_id, item.location_id)
            stock = lock_stock_row(db, order.warehouse_id, item.goods_id, item.location_id)
        baseline = item.actual_quantity if item.actual_quantity is not None else 0.0
        goods_name = item.goods.name if item.goods else f"货物#{item.goods_id}"
        location_name = item.location.location_code if item.location else f"库位#{item.location_id}"

        if stock is None:
            if abs(baseline) > 0.0001:
                raise HTTPException(
                    status_code=409,
                    detail=f"盘点期间 {goods_name}（{location_name}）的库存记录已变化（基线 {baseline} → 无记录），为避免覆盖期间出入库，请重新盘点该明细"
                )
            # 持锁重查后仍无行：录入时系统无该库位记录，期间也无新增 → 按盘点数量新建库存行
            if (item.check_quantity or 0) > 0:
                db.add(Stock(
                    warehouse_id=order.warehouse_id,
                    goods_id=item.goods_id,
                    location_id=item.location_id,
                    quantity=item.check_quantity,
                    update_time=datetime.now()
                ))
            continue

        if abs(stock.quantity - baseline) > 0.0001:
            raise HTTPException(
                status_code=409,
                detail=f"盘点期间 {goods_name}（{location_name}）的库存已变化（基线 {baseline} → 当前 {stock.quantity}），为避免覆盖期间出入库，请重新盘点该明细"
            )

        if abs(item.diff_quantity or 0) < 0.0001:
            continue  # 无差异且无冲突，跳过
        stock.quantity = item.check_quantity if item.check_quantity is not None else 0
        stock.update_time = datetime.now()

    # 设置为完成状态
    order.status = "COMPLETED"
    order.complete_time = datetime.now()
    db.commit()
    db.refresh(order)

    # 统计信息
    total_items = len(items)
    matched_items = sum(1 for item in items if abs(item.diff_quantity) < 0.0001)
    diff_items = total_items - matched_items

    return {
        "order_no": order.order_no,
        "status": order.status,
        "complete_time": order.complete_time,
        "total_items": total_items,
        "matched_items": matched_items,
        "diff_items": diff_items,
        "message": "盘点完成"
    }

# 导出盘点报告
@router.get("/check-orders/{order_id}/export", summary="导出盘点报告")
async def export_check_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 查询盘点单
    order = db.query(CheckOrderHeader).filter(CheckOrderHeader.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="盘点单不存在")

    # 权限校验
    if current_user.role != UserRole.ADMIN:
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无该仓库权限")

    # 查询明细
    items = db.query(CheckOrderItem).filter(CheckOrderItem.header_id == order_id).all()

    # 准备导出数据
    data = []
    for item in items:
        data.append({
            "序号": len(data) + 1,
            "货物名称": item.goods.name,
            "货物条码": item.goods.barcode,
            "规格": item.goods.spec or "",
            "单位": item.goods.unit,
            "库位编码": item.location.location_code,
            "库位名称": item.location.name,
            "系统库存": item.actual_quantity,
            "盘点数量": item.check_quantity,
            "差异": item.diff_quantity,
            "盘点时间": item.create_time.strftime("%Y-%m-%d %H:%M:%S")
        })

    # 创建 Excel 文件
    df = pd.DataFrame(data)
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')

    df.to_excel(writer, index=False, sheet_name='盘点明细')

    # 设置 Excel 格式
    worksheet = writer.sheets['盘点明细']
    # 自动调整列宽
    for i, width in enumerate([10, 30, 20, 20, 10, 15, 30, 15, 15, 15, 20]):
        worksheet.set_column(i, i, width)

    # 统计信息
    total_items = len(data)
    matched_items = sum(1 for item in items if abs(item.diff_quantity) < 0.0001)
    diff_items = total_items - matched_items

    worksheet.write(total_items + 2, 0, '合计')
    worksheet.write(total_items + 2, 6, f'总条数: {total_items}')
    worksheet.write(total_items + 3, 6, f'一致: {matched_items}')
    worksheet.write(total_items + 4, 6, f'差异: {diff_items}')

    writer.close()

    output.seek(0)

    # 返回文件响应
    return StreamingResponse(
        io.BytesIO(output.getvalue()),
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="盘点单_{order.order_no}.xlsx"'}
    )


# ------------------- 入库单管理接口 -------------------
@router.post("/inbound-orders/", response_model=InboundOrderHeaderResponse, summary="创建入库单")
async def create_inbound_order(
    order: InboundOrderHeaderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """创建入库单（表头）"""
    try:
        # 检查用户是否有当前仓库
        if not current_user.current_warehouse_id:
            raise HTTPException(status_code=400, detail="请先选择当前仓库")
        
        # 检查用户是否有权限操作当前仓库
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == current_user.current_warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无权限操作当前仓库")
        
        # 生成单号
        order_no = generate_order_no("IN", db)
        
        # 创建入库单头
        new_order = InboundOrderHeader(
            order_no=order_no,
            warehouse_id=current_user.current_warehouse_id,
            supplier=order.supplier,
            operator_id=current_user.id,
            remark=order.remark,
            status="DRAFT"
        )
        
        db.add(new_order)
        db.commit()
        db.refresh(new_order)
        
        # 获取仓库名称
        warehouse = db.query(Warehouse).filter(Warehouse.id == new_order.warehouse_id).first()
        
        return {
            "id": new_order.id,
            "order_no": new_order.order_no,
            "warehouse_id": new_order.warehouse_id,
            "warehouse_name": warehouse.name if warehouse else "",
            "supplier": new_order.supplier,
            "operator_id": new_order.operator_id,
            "operator_name": current_user.full_name,
            "total_amount": new_order.total_amount,
            "remark": new_order.remark,
            "status": new_order.status,
            "create_time": new_order.create_time
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("创建入库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="创建入库单失败")

@router.post("/inbound-orders/{order_id}/items", response_model=InboundOrderItemResponse, summary="添加入库单明细")
async def add_inbound_order_item(
    order_id: int,
    item: InboundOrderItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """向入库单添加明细项"""
    try:
        # 检查订单是否存在且属于当前用户仓库
        order = db.query(InboundOrderHeader).filter(
            InboundOrderHeader.id == order_id
        ).first()
        
        if not order:
            raise HTTPException(status_code=404, detail="入库单不存在")
        
        # 检查权限
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无权限操作此入库单")
        
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="只能向草稿状态的单据添加明细")
        
        # 查询货物和库位
        goods = db.query(Goods).filter(Goods.barcode == item.goods_barcode).first()
        if not goods:
            raise HTTPException(status_code=404, detail="货物不存在")
        
        location = db.query(Location).filter(Location.location_code == item.location_code).first()
        if not location:
            raise HTTPException(status_code=404, detail="库位不存在")
        
        if location.warehouse_id != order.warehouse_id:
            raise HTTPException(status_code=400, detail="库位不属于入库单仓库")
        
        # 获取单价（如果未提供，使用货物默认单价）
        unit_price = item.unit_price if item.unit_price is not None else goods.price
        total_price = unit_price * item.quantity
        
        # 创建明细项
        new_item = InboundOrderItem(
            header_id=order_id,
            goods_id=goods.id,
            location_id=location.id,
            quantity=item.quantity,
            unit_price=unit_price,
            total_price=total_price,
            remark=item.remark
        )
        
        # 更新单据总金额
        order.total_amount = (order.total_amount or 0) + total_price
        
        db.add(new_item)
        db.commit()
        db.refresh(new_item)
        
        return {
            "id": new_item.id,
            "goods_id": new_item.goods_id,
            "goods_barcode": goods.barcode,
            "goods_name": goods.name,
            "location_id": new_item.location_id,
            "location_code": location.location_code,
            "quantity": new_item.quantity,
            "unit_price": new_item.unit_price,
            "total_price": new_item.total_price,
            "remark": new_item.remark
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("添加明细失败: %s", str(e))
        raise HTTPException(status_code=500, detail="添加明细失败")

@router.post("/inbound-orders/{order_id}/submit", summary="提交入库单")
async def submit_inbound_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """提交入库单，更新库存"""
    try:
        # 行级锁读取单据头：串行化同一草稿的并发提交，防止重复入库
        order = lock_order_header(db, InboundOrderHeader, order_id)
        
        if not order:
            raise HTTPException(status_code=404, detail="入库单不存在")
        
        # 检查权限
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无权限操作此入库单")
        
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="只能提交草稿状态的单据")
        
        # 检查是否有明细
        if not order.items:
            raise HTTPException(status_code=400, detail="入库单没有明细项")
        
        # 开始事务（行级锁读取库存行，防止并发下丢失更新）
        # 先按 (货物,库位) 汇总入库量：同一组合的多条明细只处理一次库存，
        # 避免"无库存行时逐条查询并各插一条"在同一会话内触发复合唯一约束（500）
        inbound_qty: Dict[tuple, float] = {}
        for item in order.items:
            key = (item.goods_id, item.location_id)
            inbound_qty[key] = inbound_qty.get(key, 0.0) + (item.quantity or 0)

        for (goods_id, location_id), qty in inbound_qty.items():
            stock = lock_stock_row(db, order.warehouse_id, goods_id, location_id)
            if not stock:
                # 库存行尚不存在：先持咨询锁串行化并发建行，再重查
                #（后到者等前一个事务提交后能看到新行，从而改为更新）
                advisory_lock_stock_key(db, order.warehouse_id, goods_id, location_id)
                stock = lock_stock_row(db, order.warehouse_id, goods_id, location_id)
            if stock:
                stock.quantity += qty
                stock.update_time = datetime.now()
            else:
                stock = Stock(
                    warehouse_id=order.warehouse_id,
                    goods_id=goods_id,
                    location_id=location_id,
                    quantity=qty
                )
                db.add(stock)

        # 记录出入库流水（按原始明细逐条，保留审计轨迹）
        for item in order.items:
            record = InventoryRecord(
                warehouse_id=order.warehouse_id,
                goods_id=item.goods_id,
                location_id=item.location_id,
                type=InventoryType.IN,
                quantity=item.quantity,
                operator_id=current_user.id,
                remark=f"入库单：{order.order_no}"
            )
            db.add(record)
        
        # 更新订单状态
        order.status = "COMPLETED"
        order.submit_time = datetime.now()
        order.complete_time = datetime.now()
        
        db.commit()
        
        return {"message": "入库单提交成功", "order_no": order.order_no}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("提交入库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="提交入库单失败")

@router.get("/inbound-orders/", response_model=List[InboundOrderHeaderResponse], summary="获取入库单列表")
async def get_inbound_orders(
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取入库单列表"""
    try:
        query = db.query(InboundOrderHeader).join(
            Warehouse, InboundOrderHeader.warehouse_id == Warehouse.id
        ).join(
            User, InboundOrderHeader.operator_id == User.id
        )
        
        # 权限过滤
        if current_user.role != UserRole.ADMIN:
            # 获取用户有权限的仓库
            user_warehouse_ids = [
                uw.warehouse_id for uw in 
                db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
            ]
            if user_warehouse_ids:
                query = query.filter(InboundOrderHeader.warehouse_id.in_(user_warehouse_ids))
            else:
                query = query.filter(InboundOrderHeader.warehouse_id == -1)  # 返回空结果
        
        # 状态过滤
        if status:
            query = query.filter(InboundOrderHeader.status == status)
        
        # 日期过滤
        if start_date:
            start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
            query = query.filter(InboundOrderHeader.create_time >= start_datetime)
        
        if end_date:
            end_datetime = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(InboundOrderHeader.create_time < end_datetime)
        
        orders = query.order_by(InboundOrderHeader.create_time.desc()).all()
        
        result = []
        for order in orders:
            result.append({
                "id": order.id,
                "order_no": order.order_no,
                "warehouse_id": order.warehouse_id,
                "warehouse_name": order.warehouse.name,
                "supplier": order.supplier,
                "operator_id": order.operator_id,
                "operator_name": order.operator.full_name,
                "total_amount": order.total_amount,
                "remark": order.remark,
                "status": order.status,
                "create_time": order.create_time,
                "submit_time": order.submit_time,
                "complete_time": order.complete_time,
                "item_count": len(order.items)
            })
        
        return result
        
    except Exception as e:
        logging.exception("获取入库单列表失败: %s", str(e))
        raise HTTPException(status_code=500, detail="获取入库单列表失败")

@router.get("/inbound-orders/{order_id}", response_model=InboundOrderDetailResponse, summary="获取入库单详情")
async def get_inbound_order_detail(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取入库单详情"""
    try:
        query = db.query(InboundOrderHeader).filter(InboundOrderHeader.id == order_id)

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            user_warehouse_ids = [
                uw.warehouse_id for uw in
                db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
            ]
            if user_warehouse_ids:
                query = query.filter(InboundOrderHeader.warehouse_id.in_(user_warehouse_ids))
            else:
                raise HTTPException(status_code=403, detail="无权限查看此入库单")

        order = query.first()

        if not order:
            raise HTTPException(status_code=404, detail="入库单不存在")

        # 获取明细
        items = []
        for item in order.items:
            items.append({
                "id": item.id,
                "goods_id": item.goods_id,
                "goods_barcode": item.goods.barcode,
                "goods_name": item.goods.name,
                "location_id": item.location_id,
                "location_code": item.location.location_code,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "total_price": item.total_price,
                "remark": item.remark
            })

        return {
            "id": order.id,
            "order_no": order.order_no,
            "warehouse_id": order.warehouse_id,
            "warehouse_name": order.warehouse.name,
            "supplier": order.supplier,
            "operator_id": order.operator_id,
            "operator_name": order.operator.full_name,
            "total_amount": order.total_amount,
            "remark": order.remark,
            "status": order.status,
            "create_time": order.create_time,
            "submit_time": order.submit_time,
            "complete_time": order.complete_time,
            "item_count": len(order.items),
            "items": items
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.exception("获取入库单详情失败: %s", str(e))
        raise HTTPException(status_code=500, detail="获取入库单详情失败")


@router.put("/inbound-orders/{order_id}", response_model=InboundOrderHeaderResponse, summary="更新入库单")
async def update_inbound_order(
    order_id: int,
    order: InboundOrderHeaderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新入库单（仅允许更新草稿状态的订单）"""
    try:
        # 查询订单
        db_order = db.query(InboundOrderHeader).filter(InboundOrderHeader.id == order_id).first()

        if not db_order:
            raise HTTPException(status_code=404, detail="入库单不存在")

        # 检查订单状态
        if db_order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许更新草稿状态的入库单")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            # 检查订单是否属于当前用户可操作的仓库
            user_warehouse = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == current_user.id,
                UserWarehouse.warehouse_id == db_order.warehouse_id
            ).first()
            if not user_warehouse:
                raise HTTPException(status_code=403, detail="无权限操作此入库单")

        # 更新订单信息
        db_order.supplier = order.supplier
        db_order.remark = order.remark
        db.commit()
        db.refresh(db_order)

        # 返回更新后的订单（与列表接口同构的响应结构）
        return {
            "id": db_order.id,
            "order_no": db_order.order_no,
            "warehouse_id": db_order.warehouse_id,
            "warehouse_name": db_order.warehouse.name if db_order.warehouse else "",
            "supplier": db_order.supplier,
            "operator_id": db_order.operator_id,
            "operator_name": db_order.operator.full_name if db_order.operator else "",
            "total_amount": db_order.total_amount or 0,
            "remark": db_order.remark,
            "status": db_order.status,
            "create_time": db_order.create_time,
            "submit_time": db_order.submit_time,
            "complete_time": db_order.complete_time,
            "item_count": len(db_order.items)
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("更新入库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="更新入库单失败")

@router.put("/inbound-orders/{order_id}/items/{item_id}", response_model=InboundOrderItemResponse, summary="更新入库单明细")
async def update_inbound_order_item(
    order_id: int,
    item_id: int,
    item: InboundOrderItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新入库单明细（仅允许更新草稿状态的订单的明细）"""
    try:
        # 查询订单
        order = db.query(InboundOrderHeader).filter(InboundOrderHeader.id == order_id).first()

        if not order:
            raise HTTPException(status_code=404, detail="入库单不存在")

        # 检查订单状态
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许更新草稿状态的入库单的明细")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            user_warehouse = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == current_user.id,
                UserWarehouse.warehouse_id == order.warehouse_id
            ).first()
            if not user_warehouse:
                raise HTTPException(status_code=403, detail="无权限操作此入库单")

        # 查询明细项
        db_item = db.query(InboundOrderItem).filter(
            InboundOrderItem.id == item_id,
            InboundOrderItem.header_id == order_id
        ).first()

        if not db_item:
            raise HTTPException(status_code=404, detail="入库单明细不存在")

        # 查询货物信息
        goods = db.query(Goods).filter(Goods.barcode == item.goods_barcode).first()
        if not goods:
            raise HTTPException(status_code=404, detail="货物不存在")

        # 查询库位信息
        location = db.query(Location).filter(Location.location_code == item.location_code).first()
        if not location:
            raise HTTPException(status_code=404, detail="库位不存在")

        # 库位必须属于单据仓库（与新增明细的校验一致）：
        # 否则提交时会按"单据仓库 + 他仓库位"的组合写库存和流水，造成跨仓脏数据
        if location.warehouse_id != order.warehouse_id:
            raise HTTPException(status_code=400, detail="库位不属于入库单仓库")

        # 更新明细信息
        db_item.goods_id = goods.id
        db_item.goods_name = goods.name
        db_item.goods_barcode = goods.barcode
        db_item.goods_spec = goods.spec
        db_item.location_id = location.id
        db_item.location_code = location.location_code
        db_item.quantity = item.quantity
        # 单价可选：未提供时沿用原明细单价，再退化为货物默认单价（避免 None 参与乘法）
        if item.unit_price is not None:
            db_item.unit_price = item.unit_price
        elif db_item.unit_price is None:
            db_item.unit_price = goods.price or 0
        db_item.total_price = db_item.quantity * db_item.unit_price
        db_item.remark = item.remark
        db.commit()
        db.refresh(db_item)

        # 重新计算订单总金额
        recalculate_inbound_order_total(order_id, db)

        return {
            "id": db_item.id,
            "goods_id": db_item.goods_id,
            "goods_barcode": db_item.goods_barcode,
            "goods_name": db_item.goods_name,
            "location_id": db_item.location_id,
            "location_code": db_item.location_code,
            "quantity": db_item.quantity,
            "unit_price": db_item.unit_price,
            "total_price": db_item.total_price,
            "remark": db_item.remark
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("更新入库单明细失败: %s", str(e))
        raise HTTPException(status_code=500, detail="更新入库单明细失败")

@router.delete("/inbound-orders/{order_id}/items/{item_id}", summary="删除入库单明细")
async def delete_inbound_order_item(
    order_id: int,
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除入库单明细（仅允许删除草稿状态的订单的明细）"""
    try:
        # 查询订单
        order = db.query(InboundOrderHeader).filter(InboundOrderHeader.id == order_id).first()

        if not order:
            raise HTTPException(status_code=404, detail="入库单不存在")

        # 检查订单状态
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许删除草稿状态的入库单的明细")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            user_warehouse = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == current_user.id,
                UserWarehouse.warehouse_id == order.warehouse_id
            ).first()
            if not user_warehouse:
                raise HTTPException(status_code=403, detail="无权限操作此入库单")

        # 查询明细项
        db_item = db.query(InboundOrderItem).filter(
            InboundOrderItem.id == item_id,
            InboundOrderItem.header_id == order_id
        ).first()

        if not db_item:
            raise HTTPException(status_code=404, detail="入库单明细不存在")

        # 删除明细项
        db.delete(db_item)
        db.commit()

        # 重新计算订单总金额
        recalculate_inbound_order_total(order_id, db)

        return {"message": "入库单明细删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("删除入库单明细失败: %s", str(e))
        raise HTTPException(status_code=500, detail="删除入库单明细失败")

@router.post("/inbound-orders/{order_id}/return", summary="退库")
async def return_inbound_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """根据入库单创建退库出库单"""
    try:
        # 查询入库单
        inbound_order = db.query(InboundOrderHeader).filter(InboundOrderHeader.id == order_id).first()

        if not inbound_order:
            raise HTTPException(status_code=404, detail="入库单不存在")

        # 检查订单状态
        if inbound_order.status != "COMPLETED":
            raise HTTPException(status_code=400, detail="仅允许对已完成的入库单进行退库操作")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            user_warehouse = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == current_user.id,
                UserWarehouse.warehouse_id == inbound_order.warehouse_id
            ).first()
            if not user_warehouse:
                raise HTTPException(status_code=403, detail="无权限操作此入库单")

        # 检查是否已经有退库单（使用英文备注避免编码问题）
        existing_return_order = db.query(OutboundOrderHeader).filter(
            OutboundOrderHeader.remark.contains(f"Return: Original inbound order {inbound_order.order_no}")
        ).first()

        if existing_return_order:
            raise HTTPException(status_code=400, detail=f"该入库单已创建退库单 {existing_return_order.order_no}")

        # 生成退库出库单
        order_no = generate_order_no("OUT", db)
        outbound_order = OutboundOrderHeader(
            order_no=order_no,
            warehouse_id=inbound_order.warehouse_id,
            customer=inbound_order.supplier,  # 退库时客户填原供应商
            operator_id=current_user.id,
            total_amount=inbound_order.total_amount,
            remark=f"Return: Original inbound order {inbound_order.order_no}",
            status="DRAFT"
        )
        db.add(outbound_order)
        db.commit()
        db.refresh(outbound_order)

        # 转换入库单明细为出库单明细
        for inbound_item in inbound_order.items:
            outbound_item = OutboundOrderItem(
                header_id=outbound_order.id,
                goods_id=inbound_item.goods_id,
                location_id=inbound_item.location_id,
                quantity=inbound_item.quantity,
                unit_price=inbound_item.unit_price,
                total_price=inbound_item.total_price,
                remark=f"退库：原入库单明细"
            )
            db.add(outbound_item)

        db.commit()
        db.refresh(outbound_order)

        # 重新计算出库单总金额
        recalculate_outbound_order_total(outbound_order.id, db)

        return format_outbound_order_response(outbound_order)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("创建退库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="创建退库单失败")

@router.delete("/inbound-orders/{order_id}", summary="删除入库单")
async def delete_inbound_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除入库单（仅允许删除草稿状态的订单）"""
    try:
        # 查询订单
        order = db.query(InboundOrderHeader).filter(InboundOrderHeader.id == order_id).first()

        if not order:
            raise HTTPException(status_code=404, detail="入库单不存在")

        # 检查订单状态
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许删除草稿状态的入库单")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            # 检查订单是否属于当前用户
            if order.operator_id != current_user.id:
                raise HTTPException(status_code=403, detail="无权限删除此入库单")

        # 删除订单（包括所有明细项）
        db.delete(order)
        db.commit()

        return {"message": "入库单删除成功"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("删除入库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="删除入库单失败")

# ------------------- 出库单管理接口 -------------------
@router.post("/outbound-orders/", response_model=OutboundOrderHeaderResponse, summary="创建出库单")
async def create_outbound_order(
    order: OutboundOrderHeaderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """创建出库单（表头）"""
    try:
        # 检查用户是否有当前仓库
        if not current_user.current_warehouse_id:
            raise HTTPException(status_code=400, detail="请先选择当前仓库")
        
        # 检查用户是否有权限操作当前仓库
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == current_user.current_warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无权限操作当前仓库")
        
        # 生成单号
        order_no = generate_order_no("OUT", db)
        
        # 创建出库单头
        new_order = OutboundOrderHeader(
            order_no=order_no,
            warehouse_id=current_user.current_warehouse_id,
            customer=order.customer,
            operator_id=current_user.id,
            remark=order.remark,
            status="DRAFT"
        )
        
        db.add(new_order)
        db.commit()
        db.refresh(new_order)
        
        # 获取仓库名称
        warehouse = db.query(Warehouse).filter(Warehouse.id == new_order.warehouse_id).first()
        
        return {
            "id": new_order.id,
            "order_no": new_order.order_no,
            "warehouse_id": new_order.warehouse_id,
            "warehouse_name": warehouse.name if warehouse else "",
            "customer": new_order.customer,
            "operator_id": new_order.operator_id,
            "operator_name": current_user.full_name,
            "total_amount": new_order.total_amount,
            "remark": new_order.remark,
            "status": new_order.status,
            "create_time": new_order.create_time
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("创建出库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="创建出库单失败")

@router.post("/outbound-orders/{order_id}/items", response_model=OutboundOrderItemResponse, summary="添加出库单明细")
async def add_outbound_order_item(
    order_id: int,
    item: OutboundOrderItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """向出库单添加明细项"""
    try:
        order = db.query(OutboundOrderHeader).filter(
            OutboundOrderHeader.id == order_id
        ).first()
        
        if not order:
            raise HTTPException(status_code=404, detail="出库单不存在")
        
        # 检查权限
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无权限操作此出库单")
        
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="只能向草稿状态的单据添加明细")
        
        # 查询货物和库位
        goods = db.query(Goods).filter(Goods.barcode == item.goods_barcode).first()
        if not goods:
            raise HTTPException(status_code=404, detail="货物不存在")
        
        location = db.query(Location).filter(Location.location_code == item.location_code).first()
        if not location:
            raise HTTPException(status_code=404, detail="库位不存在")
        
        if location.warehouse_id != order.warehouse_id:
            raise HTTPException(status_code=400, detail="库位不属于出库单仓库")
        
        # 检查库存是否足够
        stock = db.query(Stock).filter(
            Stock.warehouse_id == order.warehouse_id,
            Stock.goods_id == goods.id,
            Stock.location_id == location.id
        ).first()
        
        if not stock or stock.quantity < item.quantity:
            raise HTTPException(status_code=400, detail="库存不足")
        
        # 获取单价
        unit_price = item.unit_price if item.unit_price is not None else goods.price
        total_price = unit_price * item.quantity
        
        # 创建明细项
        new_item = OutboundOrderItem(
            header_id=order_id,
            goods_id=goods.id,
            location_id=location.id,
            quantity=item.quantity,
            unit_price=unit_price,
            total_price=total_price,
            remark=item.remark
        )
        
        # 更新单据总金额
        order.total_amount = (order.total_amount or 0) + total_price
        
        db.add(new_item)
        db.commit()
        db.refresh(new_item)
        
        return {
            "id": new_item.id,
            "goods_id": new_item.goods_id,
            "goods_barcode": goods.barcode,
            "goods_name": goods.name,
            "location_id": new_item.location_id,
            "location_code": location.location_code,
            "quantity": new_item.quantity,
            "unit_price": new_item.unit_price,
            "total_price": new_item.total_price,
            "remark": new_item.remark
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("添加明细失败: %s", str(e))
        raise HTTPException(status_code=500, detail="添加明细失败")

@router.post("/outbound-orders/{order_id}/submit", summary="提交出库单")
async def submit_outbound_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """提交出库单，更新库存"""
    try:
        # 行级锁读取单据头：串行化同一草稿的并发提交，
        # 第二个并发提交会看到已提交状态而被拒绝
        order = lock_order_header(db, OutboundOrderHeader, order_id)
        
        if not order:
            raise HTTPException(status_code=404, detail="出库单不存在")
        
        # 检查权限
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无权限操作此出库单")
        
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="只能提交草稿状态的单据")
        
        if not order.items:
            raise HTTPException(status_code=400, detail="出库单没有明细项")
        
        # 按 (货物, 库位) 汇总需求数量：同一货物/库位的多条明细必须合并后
        # 再与库存比较，否则可拆成多条明细绕过库存检查
        required: Dict[tuple, float] = {}
        names: Dict[tuple, str] = {}
        for item in order.items:
            key = (item.goods_id, item.location_id)
            required[key] = required.get(key, 0.0) + (item.quantity or 0)
            if item.goods:
                names.setdefault(key, item.goods.name)

        # 同一事务内：锁定库存行 → 按汇总数量校验 → 扣减
        for (goods_id, location_id), qty in required.items():
            stock = lock_stock_row(db, order.warehouse_id, goods_id, location_id)
            current = stock.quantity if stock else 0
            if current < qty:
                name = names.get((goods_id, location_id), f"#{goods_id}")
                raise HTTPException(status_code=400, detail=f"货物{name}库存不足（需要 {qty}，现有 {current}）")
            stock.quantity -= qty
            stock.update_time = datetime.now()

        # 记录出库流水（按明细逐条，保留审计轨迹）
        for item in order.items:
            record = InventoryRecord(
                warehouse_id=order.warehouse_id,
                goods_id=item.goods_id,
                location_id=item.location_id,
                type=InventoryType.OUT,
                quantity=item.quantity,
                operator_id=current_user.id,
                remark=f"出库单：{order.order_no}"
            )
            db.add(record)
        
        # 更新订单状态
        order.status = "COMPLETED"
        order.submit_time = datetime.now()
        order.complete_time = datetime.now()
        
        db.commit()
        
        return {"message": "出库单提交成功", "order_no": order.order_no}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("提交出库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="提交出库单失败")

@router.get("/outbound-orders/", response_model=List[OutboundOrderHeaderResponse], summary="获取出库单列表")
async def get_outbound_orders(
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取出库单列表"""
    try:
        query = db.query(OutboundOrderHeader).join(
            Warehouse, OutboundOrderHeader.warehouse_id == Warehouse.id
        ).join(
            User, OutboundOrderHeader.operator_id == User.id
        )
        
        if current_user.role != UserRole.ADMIN:
            # 获取用户有权限的仓库
            user_warehouse_ids = [
                uw.warehouse_id for uw in 
                db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
            ]
            if user_warehouse_ids:
                query = query.filter(OutboundOrderHeader.warehouse_id.in_(user_warehouse_ids))
            else:
                query = query.filter(OutboundOrderHeader.warehouse_id == -1)  # 返回空结果
        
        if status:
            query = query.filter(OutboundOrderHeader.status == status)
        
        if start_date:
            start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
            query = query.filter(OutboundOrderHeader.create_time >= start_datetime)
        
        if end_date:
            end_datetime = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(OutboundOrderHeader.create_time < end_datetime)
        
        orders = query.order_by(OutboundOrderHeader.create_time.desc()).all()
        
        result = []
        for order in orders:
            result.append({
                "id": order.id,
                "order_no": order.order_no,
                "warehouse_id": order.warehouse_id,
                "warehouse_name": order.warehouse.name,
                "customer": order.customer,
                "operator_id": order.operator_id,
                "operator_name": order.operator.full_name,
                "total_amount": order.total_amount,
                "remark": order.remark,
                "status": order.status,
                "create_time": order.create_time,
                "submit_time": order.submit_time,
                "complete_time": order.complete_time,
                "item_count": len(order.items)
            })
        
        return result
        
    except Exception as e:
        logging.exception("获取出库单列表失败: %s", str(e))
        raise HTTPException(status_code=500, detail="获取出库单列表失败")

@router.get("/outbound-orders/{order_id}", response_model=OutboundOrderDetailResponse, summary="获取出库单详情")
async def get_outbound_order_detail(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取出库单详情"""
    try:
        query = db.query(OutboundOrderHeader).filter(OutboundOrderHeader.id == order_id)
        
        if current_user.role != UserRole.ADMIN:
            # 权限检查
            user_warehouse_ids = [
                uw.warehouse_id for uw in 
                db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
            ]
            if user_warehouse_ids:
                query = query.filter(OutboundOrderHeader.warehouse_id.in_(user_warehouse_ids))
            else:
                raise HTTPException(status_code=403, detail="无权限查看此出库单")
        
        order = query.first()
        
        if not order:
            raise HTTPException(status_code=404, detail="出库单不存在")
        
        items = []
        for item in order.items:
            items.append({
                "id": item.id,
                "goods_id": item.goods_id,
                "goods_barcode": item.goods.barcode,
                "goods_name": item.goods.name,
                "location_id": item.location_id,
                "location_code": item.location.location_code,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "total_price": item.total_price,
                "remark": item.remark
            })
        
        return {
            "id": order.id,
            "order_no": order.order_no,
            "warehouse_id": order.warehouse_id,
            "warehouse_name": order.warehouse.name,
            "customer": order.customer,
            "operator_id": order.operator_id,
            "operator_name": order.operator.full_name,
            "total_amount": order.total_amount,
            "remark": order.remark,
            "status": order.status,
            "create_time": order.create_time,
            "submit_time": order.submit_time,
            "complete_time": order.complete_time,
            "item_count": len(order.items),
            "items": items
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("获取出库单详情失败: %s", str(e))
        raise HTTPException(status_code=500, detail="获取出库单详情失败")


@router.put("/outbound-orders/{order_id}", response_model=OutboundOrderHeaderResponse, summary="更新出库单")
async def update_outbound_order(
    order_id: int,
    order: OutboundOrderHeaderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新出库单（仅允许更新草稿状态的订单）"""
    try:
        # 查询订单
        db_order = db.query(OutboundOrderHeader).filter(OutboundOrderHeader.id == order_id).first()

        if not db_order:
            raise HTTPException(status_code=404, detail="出库单不存在")

        # 检查订单状态
        if db_order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许更新草稿状态的出库单")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            # 检查订单是否属于当前用户可操作的仓库
            user_warehouse = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == current_user.id,
                UserWarehouse.warehouse_id == db_order.warehouse_id
            ).first()
            if not user_warehouse:
                raise HTTPException(status_code=403, detail="无权限操作此出库单")

        # 更新订单信息
        db_order.customer = order.customer
        db_order.remark = order.remark
        db.commit()
        db.refresh(db_order)

        # 返回更新后的订单
        return format_outbound_order_response(db_order)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("更新出库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="更新出库单失败")

@router.put("/outbound-orders/{order_id}/items/{item_id}", response_model=OutboundOrderItemResponse, summary="更新出库单明细")
async def update_outbound_order_item(
    order_id: int,
    item_id: int,
    item: OutboundOrderItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新出库单明细（仅允许更新草稿状态的订单的明细）"""
    try:
        # 查询订单
        order = db.query(OutboundOrderHeader).filter(OutboundOrderHeader.id == order_id).first()

        if not order:
            raise HTTPException(status_code=404, detail="出库单不存在")

        # 检查订单状态
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许更新草稿状态的出库单的明细")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            user_warehouse = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == current_user.id,
                UserWarehouse.warehouse_id == order.warehouse_id
            ).first()
            if not user_warehouse:
                raise HTTPException(status_code=403, detail="无权限操作此出库单")

        # 查询明细项
        db_item = db.query(OutboundOrderItem).filter(
            OutboundOrderItem.id == item_id,
            OutboundOrderItem.header_id == order_id
        ).first()

        if not db_item:
            raise HTTPException(status_code=404, detail="出库单明细不存在")

        # 查询货物信息
        goods = db.query(Goods).filter(Goods.barcode == item.goods_barcode).first()
        if not goods:
            raise HTTPException(status_code=404, detail="货物不存在")

        # 查询库位信息
        location = db.query(Location).filter(Location.location_code == item.location_code).first()
        if not location:
            raise HTTPException(status_code=404, detail="库位不存在")

        # 库位必须属于单据仓库（与新增明细/入库编辑的校验一致），防止跨仓脏数据
        if location.warehouse_id != order.warehouse_id:
            raise HTTPException(status_code=400, detail="库位不属于出库单仓库")

        # 更新明细信息
        db_item.goods_id = goods.id
        db_item.goods_name = goods.name
        db_item.goods_barcode = goods.barcode
        db_item.goods_spec = goods.spec
        db_item.location_id = location.id
        db_item.location_code = location.location_code
        db_item.quantity = item.quantity
        # 单价可选（与入库明细编辑一致）：未提供时沿用原明细单价，再退化为货物默认单价
        if item.unit_price is not None:
            db_item.unit_price = item.unit_price
        elif db_item.unit_price is None:
            db_item.unit_price = goods.price or 0
        db_item.total_price = db_item.quantity * db_item.unit_price
        db_item.remark = item.remark
        db.commit()
        db.refresh(db_item)

        # 重新计算订单总金额
        recalculate_outbound_order_total(order_id, db)

        return format_outbound_order_item_response(db_item)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("更新出库单明细失败: %s", str(e))
        raise HTTPException(status_code=500, detail="更新出库单明细失败")

@router.delete("/outbound-orders/{order_id}/items/{item_id}", summary="删除出库单明细")
async def delete_outbound_order_item(
    order_id: int,
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除出库单明细（仅允许删除草稿状态的订单的明细）"""
    try:
        # 查询订单
        order = db.query(OutboundOrderHeader).filter(OutboundOrderHeader.id == order_id).first()

        if not order:
            raise HTTPException(status_code=404, detail="出库单不存在")

        # 检查订单状态
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许删除草稿状态的出库单的明细")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            user_warehouse = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == current_user.id,
                UserWarehouse.warehouse_id == order.warehouse_id
            ).first()
            if not user_warehouse:
                raise HTTPException(status_code=403, detail="无权限操作此出库单")

        # 查询明细项
        db_item = db.query(OutboundOrderItem).filter(
            OutboundOrderItem.id == item_id,
            OutboundOrderItem.header_id == order_id
        ).first()

        if not db_item:
            raise HTTPException(status_code=404, detail="出库单明细不存在")

        # 删除明细项
        db.delete(db_item)
        db.commit()

        # 重新计算订单总金额
        recalculate_outbound_order_total(order_id, db)

        return {"message": "出库单明细删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("删除出库单明细失败: %s", str(e))
        raise HTTPException(status_code=500, detail="删除出库单明细失败")

@router.delete("/outbound-orders/{order_id}", summary="删除出库单")
async def delete_outbound_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除出库单（仅允许删除草稿状态的订单）"""
    try:
        # 查询订单
        order = db.query(OutboundOrderHeader).filter(OutboundOrderHeader.id == order_id).first()

        if not order:
            raise HTTPException(status_code=404, detail="出库单不存在")

        # 检查订单状态
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许删除草稿状态的出库单")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            # 检查订单是否属于当前用户
            if order.operator_id != current_user.id:
                raise HTTPException(status_code=403, detail="无权限删除此出库单")

        # 删除订单（包括所有明细项）
        db.delete(order)
        db.commit()

        return {"message": "出库单删除成功"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("删除出库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="删除出库单失败")

# 初始管理员自举（见 README）：
# 设置 ADMIN_USERNAME/ADMIN_PASSWORD 且数据库尚无任何用户时，
# 创建初始管理员并授权所有现有仓库；账号一旦存在（含已创建的管理员）即不再触发，
# 因此创建成功后即可移除该环境变量。
def _bootstrap_admin_if_requested(db: Session):
    admin_username = os.getenv("ADMIN_USERNAME", "").strip()
    admin_password = os.getenv("ADMIN_PASSWORD", "").strip()
    if not admin_username or not admin_password:
        return
    if db.query(User).count() > 0:
        return
    admin = User(
        username=admin_username,
        hashed_password=get_password_hash(admin_password),
        full_name=admin_username,
        role=UserRole.ADMIN,
        is_active=True,
        is_ldap_user=False
    )
    db.add(admin)
    db.flush()
    warehouses = db.query(Warehouse).all()
    for warehouse in warehouses:
        db.add(UserWarehouse(user_id=admin.id, warehouse_id=warehouse.id, is_default=False))
    if warehouses:
        admin.current_warehouse_id = warehouses[0].id
    db.commit()
    logging.warning("已通过 ADMIN_USERNAME 创建初始管理员 %s（已授权 %d 个现有仓库）；首次登录后请移除该环境变量",
                    admin_username, len(warehouses))

with SessionLocal() as _bootstrap_db:
    try:
        _bootstrap_admin_if_requested(_bootstrap_db)
    except Exception:
        _bootstrap_db.rollback()
        logging.exception("初始管理员自举失败")

# 包含路由到app
app.include_router(router, prefix="/api")

# 前端静态托管（见 README）：仓库自带 frontend/ 目录时直接由后端托管；
# 生产环境通常由 nginx 优先服务静态文件，此挂载用于独立部署/本地开发场景
_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
if os.path.isfile(os.path.join(_FRONTEND_DIR, "index.html")):
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)






