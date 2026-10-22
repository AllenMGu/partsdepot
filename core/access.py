"""仓库业务访问边界；前端的当前仓库和菜单仅用于展示。"""

from fastapi import HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from core.models import User, UserRole, UserWarehouse, Warehouse, Goods


def lock_goods_row(db: Session, warehouse_id: Optional[int], barcode: str):
    """按 (仓库, 条码) 锁定货物行（SELECT ... FOR UPDATE），返回行或 None。

    评审 P2（并发）：删除货物与提交申请/修改申请明细两条路径都引用同一货物档案行。
    仅"先查后写"在两个事务交错时会漏判（检查通过→对方提交→删除落地→悬空待审批引用）。
    两条路径对同一 (仓库, 条码) 行加行级锁后互斥串行：
      - 删除先提交 → 申请侧锁定查询取不到行 → 422"货物不存在"；
      - 申请先提交 → 删除侧待审批引用检查可见该明细 → 409。
    SQLite 方言不支持 FOR UPDATE：与 _lock_pending_request 相同的防御性降级
    （单进程/单连接测试下无并发语义，直接读即可）。
    """
    q = db.query(Goods).filter(Goods.warehouse_id == warehouse_id, Goods.barcode == barcode)
    try:
        q = q.with_for_update()
    except Exception:
        pass
    return q.first()


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


def require_inventory_write(db: Session, user: User, warehouse_id: Optional[int]) -> int:
    """库存单据写操作（入库/出库/盘点单的创建、明细、提交、完成、退库等）统一仓库访问规则。

    评审第 5 轮 P2：入库/出库写接口此前直接查询 UserWarehouse，未遵循管理员跨仓豁免
    （switch-warehouse、get_user_warehouses、require_warehouse 均豁免），管理员切换到
    未分配的启用仓库后建单仍 403。统一为：
      - 角色须具备库存操作权限（admin/supervisor/operator）；
      - 目标仓库必须存在且启用；
      - 非管理员须拥有该仓库授权（UserWarehouse）；
      - 管理员可操作任意启用仓库。
    """
    require_operator(user)
    return require_warehouse(db, user, warehouse_id, manage=False)


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
