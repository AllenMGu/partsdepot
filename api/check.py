"""盘点路由：扫码盘点、盘点记录/统计/差异、盘点单管理（三个子路由保持原始注册顺序）。"""

from fastapi import HTTPException, Depends, status, APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional
import zlib
import pandas as pd
import io

from core.database import engine
from core.models import UserRole, Warehouse, UserWarehouse, User, Location, Goods, Stock, CheckOrderHeader, CheckOrderItem, CheckRecord
from core.schemas import CheckCreate, CheckOrderCreate, CheckOrderHeaderResponse, CheckOrderItemResponse, CheckOrderFullResponse, CheckOrderItemCreate
from core.security import get_current_user
from core.deps import get_db
from core.order_utils import lock_stock_row, lock_stock_rows_for_keys, lock_order_header, advisory_lock_stock_key

router = APIRouter()
router_report = APIRouter()
router_orders = APIRouter()

# ===== 原 main.py 块: 扫码盘点 =====
# ------------------- 扫码盘点接口 -------------------
@router.post("/check/scan", summary="扫码盘点")
async def scan_check(
    check: CheckCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 查询货物和库位
    goods = db.query(Goods).filter(Goods.barcode == check.goods_barcode).first()
    location = db.query(Location).filter(Location.location_code == check.location_code).first()
    
    if not goods or not location:
        raise HTTPException(status_code=404, detail="货物或库位不存在")
    
    # 权限校验
    user_warehouse = db.query(UserWarehouse).filter(
        UserWarehouse.user_id == current_user.id,
        UserWarehouse.warehouse_id == location.warehouse_id
    ).first()
    if not user_warehouse:
        raise HTTPException(status_code=403, detail="无权限操作其他仓库库位")
    
    # 查询实际库存
    warehouse_id = location.warehouse_id
    stock = db.query(Stock).filter(
        Stock.warehouse_id == warehouse_id,
        Stock.goods_id == goods.id,
        Stock.location_id == location.id
    ).first()
    
    actual_quantity = stock.quantity if stock else 0.0
    
    # 记录盘点
    record = CheckRecord(
        warehouse_id=warehouse_id,
        goods_id=goods.id,
        location_id=location.id,
        check_quantity=check.check_quantity,
        actual_quantity=actual_quantity,
        operator_id=current_user.id
    )
    db.add(record)
    db.commit()
    
    return {
        "goods_name": goods.name,
        "location_name": location.name,
        "check_quantity": check.check_quantity,
        "actual_quantity": actual_quantity,
        "diff": check.check_quantity - actual_quantity,
        "message": "库存一致" if actual_quantity == check.check_quantity else "库存不符"
    }

# ===== 原 main.py 块: 盘点记录 =====
# 获取盘点记录
@router_report.get("/check/records", summary="获取盘点记录")
async def get_check_records(
    warehouse_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    query = db.query(CheckRecord).join(
        Goods, CheckRecord.goods_id == Goods.id
    ).join(
        Location, CheckRecord.location_id == Location.id
    ).join(
        Warehouse, CheckRecord.warehouse_id == Warehouse.id
    ).join(
        User, CheckRecord.operator_id == User.id
    )

    if current_user.role != UserRole.ADMIN:
        # 操作员仅可查看当前仓库盘点记录
        if not current_user.current_warehouse_id:
            return []
        query = query.filter(CheckRecord.warehouse_id == current_user.current_warehouse_id)

    if warehouse_id:
        query = query.filter(CheckRecord.warehouse_id == warehouse_id)

    if start_date:
        start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
        query = query.filter(CheckRecord.check_time >= start_datetime)

    if end_date:
        end_datetime = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        query = query.filter(CheckRecord.check_time < end_datetime)

    records = query.order_by(CheckRecord.check_time.desc()).all()

    result = []
    for record in records:
        diff = record.check_quantity - record.actual_quantity
        result.append({
            "id": record.id,
            "warehouse_id": record.warehouse_id,
            "warehouse_name": record.warehouse.name,
            "goods_name": record.goods.name,
            "goods_barcode": record.goods.barcode,
            "location_code": record.location.location_code,
            "location_name": record.location.name,
            "actual_quantity": record.actual_quantity,
            "check_quantity": record.check_quantity,
            "diff": diff,
            "check_time": record.check_time,
            "operator_name": record.operator.full_name
        })
    return result


# 获取盘点统计
@router_report.get("/check/stats", summary="获取盘点统计")
async def get_check_stats(
    warehouse_id: Optional[int] = None,
    date: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if date:
        start_datetime = datetime.strptime(date, "%Y-%m-%d")
    else:
        start_datetime = datetime.combine(datetime.now().date(), datetime.min.time())
    end_datetime = start_datetime + timedelta(days=1)

    query = db.query(CheckRecord).filter(
        CheckRecord.check_time >= start_datetime,
        CheckRecord.check_time < end_datetime
    )

    if current_user.role != UserRole.ADMIN:
        user_warehouse_ids = [
            uw.warehouse_id for uw in
            db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
        ]
        if user_warehouse_ids:
            query = query.filter(CheckRecord.warehouse_id.in_(user_warehouse_ids))
        else:
            query = query.filter(CheckRecord.warehouse_id == -1)

    if warehouse_id:
        query = query.filter(CheckRecord.warehouse_id == warehouse_id)

    records = query.all()
    matched = sum(1 for record in records if abs(record.check_quantity - record.actual_quantity) < 0.0001)
    diff_checks = len(records) - matched
    checked_goods = len({record.goods_id for record in records})

    return {
        "todayChecks": len(records),
        "matchedChecks": matched,
        "diffChecks": diff_checks,
        "checkedGoods": checked_goods
    }


# 获取盘点差异报表
@router_report.get("/check/diffs", summary="获取盘点差异报表")
async def get_check_diffs(
    warehouse_id: Optional[int] = None,
    date: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if date:
        start_datetime = datetime.strptime(date, "%Y-%m-%d")
        end_datetime = start_datetime + timedelta(days=1)
    else:
        start_datetime = None
        end_datetime = None

    query = db.query(CheckRecord).join(
        Goods, CheckRecord.goods_id == Goods.id
    ).join(
        Location, CheckRecord.location_id == Location.id
    ).join(
        Warehouse, CheckRecord.warehouse_id == Warehouse.id
    ).join(
        User, CheckRecord.operator_id == User.id
    )

    if current_user.role != UserRole.ADMIN:
        user_warehouse_ids = [
            uw.warehouse_id for uw in
            db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
        ]
        if user_warehouse_ids:
            query = query.filter(CheckRecord.warehouse_id.in_(user_warehouse_ids))
        else:
            query = query.filter(CheckRecord.warehouse_id == -1)

    if warehouse_id:
        query = query.filter(CheckRecord.warehouse_id == warehouse_id)

    if start_datetime and end_datetime:
        query = query.filter(
            CheckRecord.check_time >= start_datetime,
            CheckRecord.check_time < end_datetime
        )

    records = query.order_by(CheckRecord.check_time.desc()).all()
    result = []
    for record in records:
        diff = record.check_quantity - record.actual_quantity
        if abs(diff) < 0.0001:
            continue
        result.append({
            "id": record.id,
            "warehouse_id": record.warehouse_id,
            "warehouse_name": record.warehouse.name,
            "goods_name": record.goods.name,
            "goods_barcode": record.goods.barcode,
            "location_code": record.location.location_code,
            "location_name": record.location.name,
            "actual_quantity": record.actual_quantity,
            "check_quantity": record.check_quantity,
            "diff": diff,
            "check_time": record.check_time,
            "operator_name": record.operator.full_name
        })
    return result

# ===== 原 main.py 块: 盘点单节头 =====
# ------------------- 盘点单管理接口 -------------------

# ===== 原 main.py 块: 创建盘点单路由 =====
# 创建盘点单
@router_orders.post("/check-orders/", response_model=CheckOrderHeaderResponse, summary="创建盘点单")
async def create_check_order(
    order: CheckOrderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 确定仓库ID
    if order.warehouse_id:
        warehouse_id = order.warehouse_id
    elif current_user.current_warehouse_id:
        warehouse_id = current_user.current_warehouse_id
    else:
        raise HTTPException(status_code=400, detail="请选择仓库或设置默认仓库")

    # 权限校验
    if current_user.role != UserRole.ADMIN:
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无该仓库权限")

    # 生成盘点单号（格式：CK + 年月日 + 5位流水号；PostgreSQL 用咨询锁串行化，防止并发撞号）
    today = datetime.now().strftime("%Y%m%d")
    try:
        if engine.dialect.name == "postgresql":
            from sqlalchemy import text as _sa_text
            db.execute(_sa_text("SELECT pg_advisory_xact_lock(:k)"),
                       {"k": int(zlib.crc32(f"orderno-CK-{today}".encode("utf-8")))} )
    except Exception:
        pass
    # 查询当日最大流水号
    last_order = db.query(CheckOrderHeader).filter(
        CheckOrderHeader.order_no.like(f"CK{today}%")
    ).order_by(CheckOrderHeader.order_no.desc()).first()

    if last_order:
        last_seq = int(last_order.order_no[-5:])
        new_seq = last_seq + 1
    else:
        new_seq = 1

    order_no = f"CK{today}{new_seq:05d}"

    # 创建盘点单
    db_order = CheckOrderHeader(
        order_no=order_no,
        warehouse_id=warehouse_id,
        operator_id=current_user.id,
        remark=order.remark,
        status="DRAFT"
    )
    db.add(db_order)
    db.commit()
    db.refresh(db_order)

    # 返回响应
    return {
        "id": db_order.id,
        "order_no": db_order.order_no,
        "warehouse_id": db_order.warehouse_id,
        "warehouse_name": db.query(Warehouse).get(warehouse_id).name,
        "operator_id": db_order.operator_id,
        "operator_name": current_user.full_name,
        "remark": db_order.remark,
        "status": db_order.status,
        "create_time": db_order.create_time,
        "start_time": db_order.start_time,
        "complete_time": db_order.complete_time,
        "item_count": 0
    }

# ===== 原 main.py 块: 盘点单列表路由 =====
# 获取盘点单列表
@router_orders.get("/check-orders/", summary="获取盘点单列表")
async def get_check_orders(
    warehouse_id: Optional[int] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    query = db.query(CheckOrderHeader).join(Warehouse, CheckOrderHeader.warehouse_id == Warehouse.id)

    # 权限校验
    if current_user.role != UserRole.ADMIN:
        user_warehouse_ids = [
            uw.warehouse_id for uw in
            db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
        ]
        if user_warehouse_ids:
            query = query.filter(CheckOrderHeader.warehouse_id.in_(user_warehouse_ids))
        else:
            return []

    # 过滤条件
    if warehouse_id:
        query = query.filter(CheckOrderHeader.warehouse_id == warehouse_id)

    if status:
        query = query.filter(CheckOrderHeader.status == status)

    if start_date:
        start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
        query = query.filter(CheckOrderHeader.create_time >= start_datetime)

    if end_date:
        end_datetime = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        query = query.filter(CheckOrderHeader.create_time < end_datetime)

    # 分页
    total_count = query.count()
    offset = (page - 1) * page_size
    orders = query.order_by(CheckOrderHeader.create_time.desc()).offset(offset).limit(page_size).all()

    # 构建响应数据
    result = []
    for order in orders:
        item_count = db.query(CheckOrderItem).filter(CheckOrderItem.header_id == order.id).count()
        result.append({
            "id": order.id,
            "order_no": order.order_no,
            "warehouse_id": order.warehouse_id,
            "warehouse_name": order.warehouse.name,
            "operator_id": order.operator_id,
            "operator_name": order.operator.full_name,
            "remark": order.remark,
            "status": order.status,
            "create_time": order.create_time,
            "start_time": order.start_time,
            "complete_time": order.complete_time,
            "item_count": item_count
        })

    return {
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "data": result
    }

# ===== 原 main.py 块: 盘点单详情路由 =====
# 获取盘点单详情（包含明细）
@router_orders.get("/check-orders/{order_id}", response_model=CheckOrderFullResponse, summary="获取盘点单详情")
async def get_check_order_detail(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 查询盘点单
    order = db.query(CheckOrderHeader).filter(CheckOrderHeader.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="盘点单不存在")

    # 权限校验
    if current_user.role != UserRole.ADMIN:
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无该仓库权限")

    # 查询明细
    items = db.query(CheckOrderItem).filter(CheckOrderItem.header_id == order_id).all()

    # 构建响应
    header_response = CheckOrderHeaderResponse(
        id=order.id,
        order_no=order.order_no,
        warehouse_id=order.warehouse_id,
        warehouse_name=order.warehouse.name,
        operator_id=order.operator_id,
        operator_name=order.operator.full_name,
        remark=order.remark,
        status=order.status,
        create_time=order.create_time,
        start_time=order.start_time,
        complete_time=order.complete_time,
        item_count=len(items)
    )

    items_response = []
    for item in items:
        items_response.append(CheckOrderItemResponse(
            id=item.id,
            goods_id=item.goods_id,
            goods_name=item.goods.name,
            goods_barcode=item.goods.barcode,
            location_id=item.location_id,
            location_code=item.location.location_code,
            location_name=item.location.name,
            check_quantity=item.check_quantity,
            actual_quantity=item.actual_quantity,
            diff_quantity=item.diff_quantity,
            create_time=item.create_time
        ))

    return {"header": header_response, "items": items_response}

# ===== 原 main.py 块: 添加盘点明细路由 =====
@router_orders.post("/check-orders/items/", summary="添加盘点明细（扫码盘点）")
async def add_check_order_item(
    item: CheckOrderItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 查询盘点单（行级锁：与"并发完成"串行化，锁内复核状态，
    # 防止"已完成单据出现未过账新明细"的竞争窗口）
    order = lock_order_header(db, CheckOrderHeader, item.header_id)
    if not order:
        raise HTTPException(status_code=404, detail="盘点单不存在")

    # 检查盘点单状态
    if order.status == "COMPLETED":
        raise HTTPException(status_code=400, detail="盘点单已完成，无法添加明细")

    # 权限校验
    if current_user.role != UserRole.ADMIN:
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无该仓库权限")

    # 查询货物和库位
    goods = db.query(Goods).filter(Goods.barcode == item.goods_barcode).first()
    location = db.query(Location).filter(Location.location_code == item.location_code).first()

    if not goods or not location:
        raise HTTPException(status_code=404, detail="货物或库位不存在")

    # 货物为全局主数据，仅校验库位所属仓库
    if location.warehouse_id != order.warehouse_id:
        raise HTTPException(status_code=400, detail="库位不属于该盘点单仓库")

    # 获取系统库存
    stock = db.query(Stock).filter(
        Stock.warehouse_id == order.warehouse_id,
        Stock.goods_id == goods.id,
        Stock.location_id == location.id
    ).first()

    actual_quantity = stock.quantity if stock else 0.0

    # 计算差异
    diff_quantity = item.check_quantity - actual_quantity

    # 创建或更新明细
    existing_item = db.query(CheckOrderItem).filter(
        CheckOrderItem.header_id == item.header_id,
        CheckOrderItem.goods_id == goods.id,
        CheckOrderItem.location_id == location.id
    ).first()

    if existing_item:
        # 更新现有明细
        existing_item.check_quantity = item.check_quantity
        existing_item.actual_quantity = actual_quantity
        existing_item.diff_quantity = diff_quantity
        existing_item.create_time = datetime.now()
    else:
        # 创建新明细
        existing_item = CheckOrderItem(
            header_id=item.header_id,
            goods_id=goods.id,
            location_id=location.id,
            check_quantity=item.check_quantity,
            actual_quantity=actual_quantity,
            diff_quantity=diff_quantity
        )
        db.add(existing_item)

    # 如果盘点单状态是草稿，设置为盘点中
    if order.status == "DRAFT":
        order.status = "IN_PROGRESS"
        order.start_time = datetime.now()

    db.commit()
    db.refresh(existing_item)
    db.refresh(order)

    # 同时创建盘点记录（保留历史）
    db_record = CheckRecord(
        warehouse_id=order.warehouse_id,
        goods_id=goods.id,
        location_id=location.id,
        check_quantity=item.check_quantity,
        actual_quantity=actual_quantity,
        operator_id=current_user.id
    )
    db.add(db_record)
    db.commit()

    return {
        "id": existing_item.id,
        "goods_name": goods.name,
        "goods_barcode": goods.barcode,
        "location_code": location.location_code,
        "location_name": location.name,
        "check_quantity": existing_item.check_quantity,
        "actual_quantity": existing_item.actual_quantity,
        "diff_quantity": existing_item.diff_quantity,
        "order_status": order.status
    }

# ===== 原 main.py 块: 完成盘点路由 =====
# 完成盘点
@router_orders.post("/check-orders/{order_id}/complete", summary="完成盘点")
async def complete_check_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 查询盘点单（单据头行级锁：串行化同一盘点单的并发完成，防止两个并发完成
    # 都看到"未完成"而各自回写/新建库存）
    order = lock_order_header(db, CheckOrderHeader, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="盘点单不存在")

    # 检查盘点单状态（持锁后判定：并发完成中先提交者置 COMPLETED，后到者在此被拒）
    if order.status == "COMPLETED":
        raise HTTPException(status_code=400, detail="盘点单已完成")

    if order.status == "DRAFT":
        raise HTTPException(status_code=400, detail="盘点单尚未开始，无法完成")

    # 权限校验
    if current_user.role != UserRole.ADMIN:
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无该仓库权限")

    # 将盘点差异回写到库存（行级锁，防止并发下丢失更新）
    # 冲突检测：录入时记录的系统库存基线（item.actual_quantity）与当前库存不一致，
    # 说明盘点期间发生了出入库 —— 直接覆盖会抹掉期间合法变动，必须要求重盘。
    items = (
        db.query(CheckOrderItem)
        .filter(CheckOrderItem.header_id == order_id)
        .order_by(CheckOrderItem.goods_id, CheckOrderItem.location_id, CheckOrderItem.id)
        .all()
    )
    locked = lock_stock_rows_for_keys(
        db,
        order.warehouse_id,
        ((item.goods_id, item.location_id) for item in items),
    )
    stock_by_key = {(row.goods_id, row.location_id): row for row in locked}
    for item in items:
        stock = stock_by_key.get((item.goods_id, item.location_id))
        if stock is None:
            # 与入库/扫码入库同一把咨询锁：串行化"同一 (仓库,货物,库位)"的并发建行，
            # 避免盘点完成与首次扫码入库并发时都看到"无库存行"而各建一条。
            advisory_lock_stock_key(db, order.warehouse_id, item.goods_id, item.location_id)
            stock = lock_stock_row(db, order.warehouse_id, item.goods_id, item.location_id)
        baseline = item.actual_quantity if item.actual_quantity is not None else 0.0
        goods_name = item.goods.name if item.goods else f"货物#{item.goods_id}"
        location_name = item.location.location_code if item.location else f"库位#{item.location_id}"

        if stock is None:
            if abs(baseline) > 0.0001:
                raise HTTPException(
                    status_code=409,
                    detail=f"盘点期间 {goods_name}（{location_name}）的库存记录已变化（基线 {baseline} → 无记录），为避免覆盖期间出入库，请重新盘点该明细"
                )
            # 持锁重查后仍无行：录入时系统无该库位记录，期间也无新增 → 按盘点数量新建库存行
            if (item.check_quantity or 0) > 0:
                db.add(Stock(
                    warehouse_id=order.warehouse_id,
                    goods_id=item.goods_id,
                    location_id=item.location_id,
                    quantity=item.check_quantity,
                    update_time=datetime.now()
                ))
            continue

        if abs(stock.quantity - baseline) > 0.0001:
            raise HTTPException(
                status_code=409,
                detail=f"盘点期间 {goods_name}（{location_name}）的库存已变化（基线 {baseline} → 当前 {stock.quantity}），为避免覆盖期间出入库，请重新盘点该明细"
            )

        if abs(item.diff_quantity or 0) < 0.0001:
            continue  # 无差异且无冲突，跳过
        stock.quantity = item.check_quantity if item.check_quantity is not None else 0
        stock.update_time = datetime.now()

    # 设置为完成状态
    order.status = "COMPLETED"
    order.complete_time = datetime.now()
    db.commit()
    db.refresh(order)

    # 统计信息
    total_items = len(items)
    matched_items = sum(1 for item in items if abs(item.diff_quantity) < 0.0001)
    diff_items = total_items - matched_items

    return {
        "order_no": order.order_no,
        "status": order.status,
        "complete_time": order.complete_time,
        "total_items": total_items,
        "matched_items": matched_items,
        "diff_items": diff_items,
        "message": "盘点完成"
    }

# ===== 原 main.py 块: 导出盘点报告路由 =====
# 导出盘点报告
@router_orders.get("/check-orders/{order_id}/export", summary="导出盘点报告")
async def export_check_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 查询盘点单
    order = db.query(CheckOrderHeader).filter(CheckOrderHeader.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="盘点单不存在")

    # 权限校验
    if current_user.role != UserRole.ADMIN:
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无该仓库权限")

    # 查询明细
    items = db.query(CheckOrderItem).filter(CheckOrderItem.header_id == order_id).all()

    # 准备导出数据
    data = []
    for item in items:
        data.append({
            "序号": len(data) + 1,
            "货物名称": item.goods.name,
            "货物条码": item.goods.barcode,
            "规格": item.goods.spec or "",
            "单位": item.goods.unit,
            "库位编码": item.location.location_code,
            "库位名称": item.location.name,
            "系统库存": item.actual_quantity,
            "盘点数量": item.check_quantity,
            "差异": item.diff_quantity,
            "盘点时间": item.create_time.strftime("%Y-%m-%d %H:%M:%S")
        })

    # 创建 Excel 文件
    df = pd.DataFrame(data)
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')

    df.to_excel(writer, index=False, sheet_name='盘点明细')

    # 设置 Excel 格式
    worksheet = writer.sheets['盘点明细']
    # 自动调整列宽
    for i, width in enumerate([10, 30, 20, 20, 10, 15, 30, 15, 15, 15, 20]):
        worksheet.set_column(i, i, width)

    # 统计信息
    total_items = len(data)
    matched_items = sum(1 for item in items if abs(item.diff_quantity) < 0.0001)
    diff_items = total_items - matched_items

    worksheet.write(total_items + 2, 0, '合计')
    worksheet.write(total_items + 2, 6, f'总条数: {total_items}')
    worksheet.write(total_items + 3, 6, f'一致: {matched_items}')
    worksheet.write(total_items + 4, 6, f'差异: {diff_items}')

    writer.close()

    output.seek(0)

    # 返回文件响应
    return StreamingResponse(
        io.BytesIO(output.getvalue()),
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="盘点单_{order.order_no}.xlsx"'}
    )
