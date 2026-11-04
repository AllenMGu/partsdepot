"""库位管理路由。"""

from fastapi import HTTPException, Depends, APIRouter
from sqlalchemy.orm import Session
from typing import List, Optional
import logging

from core.models import UserRole, UserWarehouse, User, Location
from core.schemas import LocationCreate, LocationResponse, LocationUpdate
from core.security import get_current_user
from core.deps import get_db
from core.access import require_warehouse, scoped_warehouse_id

router = APIRouter()

# ===== 原 main.py 块: 库位 =====
# ------------------- 库位管理接口 -------------------
@router.post("/locations/", response_model=LocationResponse, summary="新增库位")
async def create_location(
    location: LocationCreate, 
    current_user: User = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    require_warehouse(db, current_user, location.warehouse_id, manage=True)

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
        # 复核问题（2026-11）：库位列表统一按"当前仓库"返回（管理员同样按当前仓库，
        # 显式 warehouse_id 可查询任意已授权/启用仓库），避免切仓后混入其它仓库。
        wid = scoped_warehouse_id(db, current_user, warehouse_id)
        if current_user.role == UserRole.ADMIN:
            # 管理员可见当前仓库的全部库位（含已停用），便于维护
            locations = db.query(Location).filter(Location.warehouse_id == wid).all()
        else:
            # 非管理员只能看到当前仓库的启用库位
            locations = db.query(Location).filter(
                Location.warehouse_id == wid, Location.is_active == True
            ).all()

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
    except HTTPException:
        # 评审第 7 轮 P2：未选仓(400)/越权(403)/停用(404) 等业务错误须原样返回，
        # 不能被通用异常处理改写成 500
        raise
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
    db_location = db.query(Location).filter(Location.id == id).first()
    if not db_location:
        raise HTTPException(status_code=404, detail="库位未找到")
    require_warehouse(db, current_user, db_location.warehouse_id, manage=True)

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
    db_location = db.query(Location).filter(Location.id == id).first()
    if not db_location:
        raise HTTPException(status_code=404, detail="库位未找到")
    require_warehouse(db, current_user, db_location.warehouse_id, manage=True)
    
    db.delete(db_location)
    db.commit()
    return {"message": "删除成功"}
