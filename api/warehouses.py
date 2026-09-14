"""仓库管理路由。"""

from fastapi import HTTPException, Depends, APIRouter
from sqlalchemy.orm import Session
from typing import List
import logging

from core.models import UserRole, Warehouse, UserWarehouse, User
from core.schemas import WarehouseCreate, WarehouseResponse, WarehouseUpdate
from core.security import get_current_user
from core.deps import get_db

router = APIRouter()

# ===== 原 main.py 块: 仓库 =====
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
