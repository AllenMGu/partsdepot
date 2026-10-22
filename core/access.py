"""仓库业务访问边界；前端的当前仓库和菜单仅用于展示。"""

from fastapi import HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from core.models import User, UserRole, UserWarehouse, Warehouse


def require_warehouse(db: Session, user: User, warehouse_id: Optional[int], *, manage: bool = False) -> int:
    if not warehouse_id:
        raise HTTPException(status_code=400, detail="请先选择仓库")
    warehouse = db.query(Warehouse).filter(Warehouse.id == warehouse_id, Warehouse.is_active.is_(True)).first()
    if not warehouse:
        raise HTTPException(status_code=404, detail="仓库不存在或已停用")
    if manage and user.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
        raise HTTPException(status_code=403, detail="无仓库管理权限")
    if user.role != UserRole.ADMIN and not db.query(UserWarehouse).filter(
        UserWarehouse.user_id == user.id, UserWarehouse.warehouse_id == warehouse_id
    ).first():
        raise HTTPException(status_code=403, detail="无该仓库权限")
    return warehouse_id


def require_operator(user: User) -> None:
    if user.role not in (UserRole.ADMIN, UserRole.SUPERVISOR, UserRole.OPERATOR):
        raise HTTPException(status_code=403, detail="无库存操作权限")
