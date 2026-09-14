"""LDAP 运行时配置（可变模块级状态）+ 认证 + 管理路由。

注意：LDAP_SERVER 等模块级变量是运行时可变的 LDAP 配置的唯一事实来源。
load_ldap_config_from_db、ldap_authenticate 以及 /ldap/import-users 路由
都必须位于本模块并通过 global 读写这些变量，避免跨模块按值导入
（from ... import LDAP_SERVER）造成“配置更新后读到旧值”的问题。
其他模块只允许以函数/常量形式从本模块导入。"""

from fastapi import HTTPException, Depends, APIRouter
from sqlalchemy.orm import Session
from datetime import datetime
from ldap3.utils.conv import escape_filter_chars
import logging
import secrets
import ldap3
import os

from core.models import UserRole, Config, User
from core.security import get_password_hash, get_current_user
from core.deps import get_db

# ===== 原 main.py 块: LDAP 变量 =====
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

# ===== 原 main.py 块: 加载LDAP配置 =====
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

# ===== 原 main.py 块: ldap_authenticate =====
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

# ===== 原 main.py 块: ldap 三条路由 =====
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
