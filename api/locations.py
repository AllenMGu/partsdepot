"""库位管理路由。"""

from fastapi import HTTPException, Depends, APIRouter
from sqlalchemy.orm import Session
from typing import List, Optional
import logging

from core.models import UserRole, UserWarehouse, User, Location
from core.schemas import LocationCreate, LocationResponse, LocationUpdate
from core.security import get_current_user
from core.deps import get_db

router = APIRouter()

# ===== 原 main.py 块: 库位 =====
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
