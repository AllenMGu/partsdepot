"""用户管理路由（前段：新增/切换仓库/仓库列表；后段：列表/修改/分配/删除）。"""

from fastapi import HTTPException, Depends, APIRouter, Query
from sqlalchemy.orm import Session
from typing import Optional
import logging

from core.models import UserRole, Warehouse, UserWarehouse, User
from core.schemas import UserCreate, UserResponse, UserUpdate
from core.security import get_password_hash, get_current_user
from core.deps import get_db

router = APIRouter()
router_post = APIRouter()

# ===== 原 main.py 块: users 前段 =====
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

# ===== 原 main.py 块: users 后段 =====
# 获取所有用户
@router_post.get("/users/", summary="获取所有用户")
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
@router_post.put("/users/{user_id}", summary="修改用户信息")
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
@router_post.post("/users/{user_id}/assign-warehouse", summary="为用户分配仓库")
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

@router_post.delete("/users/{user_id}", summary="删除用户")
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

@router_post.delete("/users/{user_id}/unassign-warehouse", summary="取消用户仓库分配")
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
