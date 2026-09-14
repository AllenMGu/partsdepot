"""认证路由：登录（/token）与退出（/logout）。"""

from fastapi import HTTPException, Depends, Form, APIRouter, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from core.config import ACCESS_TOKEN_EXPIRE_MINUTES
from core.models import UserRole, Warehouse, UserWarehouse, User
from core.security import AUTH_COOKIE_NAME, AUTH_COOKIE_SAMESITE, AUTH_COOKIE_SECURE, verify_password, get_password_hash, create_access_token
from core.ldap import load_ldap_config_from_db, ldap_authenticate
from core.auth_state import clear_login_fail_state, ensure_not_locked, handle_login_failure
from core.deps import get_db

router = APIRouter()

# ===== 原 main.py 块: token/logout 路由 =====
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
