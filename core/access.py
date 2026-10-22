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


def require_selected_warehouse(db: Session, user: User, warehouse_id: Optional[int]) -> int:
    """写操作（单次扫码出入库、扫码盘点等）必须绑定用户当前选择的仓库。

    校验顺序与批量扫码（/inventory/batch）的安全边界一致，且保留"无权限优先"：
    1. 用户须拥有目标仓库权限（403；admin 豁免）；
    2. 目标仓库必须等于当前选择的仓库（400）——多仓授权用户不能在 A 仓页面
       携带 B 仓库位完成真实出入库/盘点；
    3. 仓库必须存在且启用（400）——仓库停用后单次扫码与扫码盘点统一拒绝写入。
    """
    if warehouse_id is None:
        raise HTTPException(status_code=400, detail="请先选择仓库")
    if user.role != UserRole.ADMIN and not db.query(UserWarehouse).filter(
        UserWarehouse.user_id == user.id, UserWarehouse.warehouse_id == warehouse_id
    ).first():
        raise HTTPException(status_code=403, detail="无权限操作其他仓库库位")
    selected = user.current_warehouse_id
    if not selected:
        raise HTTPException(status_code=400, detail="请先选择当前仓库")
    if warehouse_id != selected:
        raise HTTPException(status_code=400, detail="该库位不属于当前选择的仓库，请先切换到目标仓库")
    warehouse = db.query(Warehouse).filter(Warehouse.id == warehouse_id, Warehouse.is_active.is_(True)).first()
    if not warehouse:
        raise HTTPException(status_code=400, detail="当前仓库不存在或已停用")
    return warehouse_id
