"""库存路由：扫码出入库、库存查询、出入库记录（三个子路由保持原始注册顺序）。"""

import json
import logging

from fastapi import HTTPException, Depends, APIRouter, Query, Header
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional

from core.models import UserRole, InventoryType, Warehouse, UserWarehouse, User, Location, Goods, Stock, InventoryRecord, InboundOrderHeader, InboundOrderItem, OutboundOrderHeader, OutboundOrderItem, IdempotencyRecord
from core.schemas import InventoryBatchCreate, InventoryCreate, StockResponse
from core.security import get_current_user
from core.deps import get_db
from core.order_utils import lock_stock_row, lock_stock_rows_for_keys, advisory_lock_stock_key, advisory_lock_idempotency_key, generate_order_no

router = APIRouter()
router_query = APIRouter()
router_logs = APIRouter()

def _batch_idempotency_key(header_key: Optional[str], body_key: Optional[str]) -> str:
    key = (header_key or body_key or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail="批量扫码确认必须提供 Idempotency-Key 或 request_id")
    return key

def _batch_operation_response(order_no: str, inventory_type: InventoryType, warehouse_id: int, rows: list) -> dict:
    return {
        "message": f"{inventory_type.value}成功",
        "order_no": order_no,
        "warehouse_id": warehouse_id,
        "items": rows,
    }

@router.post("/inventory/batch", summary="连续扫码批量确认出入库")
async def batch_inventory(
    payload: InventoryBatchCreate,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """连续扫码的最终确认接口。

    所有条码先在同一事务内解析、锁定和校验，之后才创建单据、更新库存、写流水；
    任意一条失败都会回滚整单。幂等记录与业务单据共用事务，重试只返回首次结果。
    """
    key = _batch_idempotency_key(idempotency_key, payload.request_id)
    operation = "inventory-batch"
    try:
        advisory_lock_idempotency_key(db, operation, key)
        previous = db.query(IdempotencyRecord).filter(
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.idempotency_key == key,
        ).first()
        if previous:
            return json.loads(previous.response_json)

        # 先按条码/库位解析全部明细并汇总，拒绝跨仓批量请求。
        resolved = {}
        warehouse_ids = set()
        for item in payload.items:
            barcode = item.goods_barcode.strip()
            location_code = item.location_code.strip()
            goods = db.query(Goods).filter(Goods.barcode == barcode).first()
            if goods is None:
                raise HTTPException(status_code=404, detail=f"未找到该货物条码：{barcode}")
            location = db.query(Location).filter(Location.location_code == location_code).first()
            if location is None:
                raise HTTPException(status_code=404, detail=f"未找到该库位：{location_code}")
            wh = db.query(Warehouse).filter(Warehouse.id == location.warehouse_id).first()
            if wh is None or not wh.is_active:
                raise HTTPException(status_code=400, detail="库位所属仓库不存在或已停用")
            if current_user.role != UserRole.ADMIN and not db.query(UserWarehouse).filter(
                UserWarehouse.user_id == current_user.id,
                UserWarehouse.warehouse_id == location.warehouse_id,
            ).first():
                raise HTTPException(status_code=403, detail="无权限操作该库位所属仓库")
            warehouse_ids.add(location.warehouse_id)
            row_key = (goods.id, location.id)
            if row_key not in resolved:
                resolved[row_key] = {
                    "goods": goods,
                    "location": location,
                    "quantity": 0.0,
                }
            resolved[row_key]["quantity"] += item.quantity

        if len(warehouse_ids) != 1:
            raise HTTPException(status_code=400, detail="一次连续扫码只能操作同一仓库")
        warehouse_id = next(iter(warehouse_ids))
        keys = sorted(resolved)

        # 入库允许首笔扫码创建库存行：先锁咨询锁，再重新读取，避免并发插入重复行。
        if payload.type == InventoryType.IN:
            for goods_id, location_id in keys:
                if not db.query(Stock).filter(
                    Stock.warehouse_id == warehouse_id,
                    Stock.goods_id == goods_id,
                    Stock.location_id == location_id,
                ).first():
                    advisory_lock_stock_key(db, warehouse_id, goods_id, location_id)
        locked = lock_stock_rows_for_keys(db, warehouse_id, keys)
        stock_by_key = {(row.goods_id, row.location_id): row for row in locked}
        # 咨询锁之后再查一次，保证刚创建的行被纳入本事务。
        if payload.type == InventoryType.IN:
            locked = lock_stock_rows_for_keys(db, warehouse_id, keys)
            stock_by_key = {(row.goods_id, row.location_id): row for row in locked}

        if payload.type == InventoryType.OUT:
            for row_key in keys:
                item = resolved[row_key]
                stock = stock_by_key.get(row_key)
                available = float(stock.quantity if stock else 0)
                if available < item["quantity"]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"库存不足：{item['goods'].name} 需要 {item['quantity']:g}，现有 {available:g}",
                    )

        order_no = generate_order_no("IN" if payload.type == InventoryType.IN else "OUT", db)
        now = datetime.now()
        rows = []
        total_amount = 0.0
        for row_key in keys:
            item = resolved[row_key]
            goods = item["goods"]
            location = item["location"]
            quantity = item["quantity"]
            stock = stock_by_key.get(row_key)
            if payload.type == InventoryType.IN:
                if stock is None:
                    stock = Stock(
                        warehouse_id=warehouse_id,
                        goods_id=goods.id,
                        location_id=location.id,
                        quantity=0,
                    )
                    db.add(stock)
                    db.flush()
                    stock_by_key[row_key] = stock
                stock.quantity = (stock.quantity or 0) + quantity
            else:
                stock.quantity = (stock.quantity or 0) - quantity
            stock.update_time = now
            price = float(goods.price or 0)
            total_amount += quantity * price
            rows.append({
                "goods_id": goods.id,
                "goods_barcode": goods.barcode,
                "goods_name": goods.name,
                "unit_price": float(goods.price or 0),
                "location_id": location.id,
                "location_code": location.location_code,
                "quantity": quantity,
                "current_stock": float(stock.quantity or 0),
            })

        if payload.type == InventoryType.IN:
            order = InboundOrderHeader(
                order_no=order_no, warehouse_id=warehouse_id, supplier="连续扫码入库",
                operator_id=current_user.id, total_amount=total_amount,
                remark=payload.remark, status="COMPLETED", create_time=now,
                submit_time=now, complete_time=now,
            )
            db.add(order)
            db.flush()
            for row in rows:
                db.add(InboundOrderItem(
                    header_id=order.id, goods_id=row["goods_id"], location_id=row["location_id"],
                    quantity=row["quantity"], unit_price=row["unit_price"],
                    total_price=row["quantity"] * row["unit_price"],
                    remark=payload.remark,
                ))
                db.add(InventoryRecord(
                    warehouse_id=warehouse_id, goods_id=row["goods_id"], location_id=row["location_id"],
                    type=InventoryType.IN, quantity=row["quantity"], operator_id=current_user.id,
                    remark=f"连续扫码入库：{order_no} {payload.remark or ''}"[:500],
                ))
        else:
            order = OutboundOrderHeader(
                order_no=order_no, warehouse_id=warehouse_id, customer="连续扫码出库",
                operator_id=current_user.id, total_amount=total_amount,
                remark=payload.remark, status="COMPLETED", create_time=now,
                submit_time=now, complete_time=now,
            )
            db.add(order)
            db.flush()
            for row in rows:
                price = row["unit_price"]
                db.add(OutboundOrderItem(
                    header_id=order.id, goods_id=row["goods_id"], location_id=row["location_id"],
                    quantity=row["quantity"], unit_price=price,
                    total_price=row["quantity"] * price, remark=payload.remark,
                ))
                db.add(InventoryRecord(
                    warehouse_id=warehouse_id, goods_id=row["goods_id"], location_id=row["location_id"],
                    type=InventoryType.OUT, quantity=row["quantity"], operator_id=current_user.id,
                    remark=f"连续扫码出库：{order_no} {payload.remark or ''}"[:500],
                ))

        response = _batch_operation_response(order_no, payload.type, warehouse_id, rows)
        db.add(IdempotencyRecord(
            operation=operation, idempotency_key=key,
            response_json=json.dumps(response, ensure_ascii=False), create_time=now,
        ))
        db.commit()
        return response
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logging.exception("批量扫码出入库失败")
        raise HTTPException(status_code=500, detail="批量扫码出入库失败，整单未提交")

# ===== 原 main.py 块: 扫码出入库 =====
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

# ===== 原 main.py 块: 库存查询 =====
# ------------------- 库存查询接口（带仓库+库位） -------------------
@router_query.get("/stock/", response_model=List[StockResponse], summary="库存查询（带仓库库位）")
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

# ===== 原 main.py 块: 出入库记录 =====
# 获取出入库记录
@router_logs.get("/inventory/logs", summary="获取出入库记录")
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
