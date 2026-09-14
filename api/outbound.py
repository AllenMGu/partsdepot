"""出库单管理路由。"""

from fastapi import HTTPException, Depends, status, APIRouter
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional, Dict
import logging

from core.models import UserRole, InventoryType, Warehouse, UserWarehouse, User, Location, Goods, Stock, InventoryRecord, OutboundOrderHeader, OutboundOrderItem
from core.schemas import OutboundOrderItemCreate, OutboundOrderItemResponse, OutboundOrderHeaderCreate, OutboundOrderHeaderResponse, OutboundOrderDetailResponse
from core.security import get_current_user
from core.deps import get_db
from core.order_utils import format_outbound_order_response, format_outbound_order_item_response, recalculate_outbound_order_total, lock_stock_row, lock_order_header, generate_order_no

router = APIRouter()

# ===== 原 main.py 块: 出库单 =====
# ------------------- 出库单管理接口 -------------------
@router.post("/outbound-orders/", response_model=OutboundOrderHeaderResponse, summary="创建出库单")
async def create_outbound_order(
    order: OutboundOrderHeaderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """创建出库单（表头）"""
    try:
        # 检查用户是否有当前仓库
        if not current_user.current_warehouse_id:
            raise HTTPException(status_code=400, detail="请先选择当前仓库")
        
        # 检查用户是否有权限操作当前仓库
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == current_user.current_warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无权限操作当前仓库")
        
        # 生成单号
        order_no = generate_order_no("OUT", db)
        
        # 创建出库单头
        new_order = OutboundOrderHeader(
            order_no=order_no,
            warehouse_id=current_user.current_warehouse_id,
            customer=order.customer,
            operator_id=current_user.id,
            remark=order.remark,
            status="DRAFT"
        )
        
        db.add(new_order)
        db.commit()
        db.refresh(new_order)
        
        # 获取仓库名称
        warehouse = db.query(Warehouse).filter(Warehouse.id == new_order.warehouse_id).first()
        
        return {
            "id": new_order.id,
            "order_no": new_order.order_no,
            "warehouse_id": new_order.warehouse_id,
            "warehouse_name": warehouse.name if warehouse else "",
            "customer": new_order.customer,
            "operator_id": new_order.operator_id,
            "operator_name": current_user.full_name,
            "total_amount": new_order.total_amount,
            "remark": new_order.remark,
            "status": new_order.status,
            "create_time": new_order.create_time
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("创建出库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="创建出库单失败")

@router.post("/outbound-orders/{order_id}/items", response_model=OutboundOrderItemResponse, summary="添加出库单明细")
async def add_outbound_order_item(
    order_id: int,
    item: OutboundOrderItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """向出库单添加明细项"""
    try:
        # 行级锁：与"并发提交"串行化，锁内复核状态
        order = lock_order_header(db, OutboundOrderHeader, order_id)

        if not order:
            raise HTTPException(status_code=404, detail="出库单不存在")
        
        # 检查权限
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无权限操作此出库单")
        
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="只能向草稿状态的单据添加明细")
        
        # 查询货物和库位
        goods = db.query(Goods).filter(Goods.barcode == item.goods_barcode).first()
        if not goods:
            raise HTTPException(status_code=404, detail="货物不存在")
        
        location = db.query(Location).filter(Location.location_code == item.location_code).first()
        if not location:
            raise HTTPException(status_code=404, detail="库位不存在")
        
        if location.warehouse_id != order.warehouse_id:
            raise HTTPException(status_code=400, detail="库位不属于出库单仓库")
        
        # 检查库存是否足够
        stock = db.query(Stock).filter(
            Stock.warehouse_id == order.warehouse_id,
            Stock.goods_id == goods.id,
            Stock.location_id == location.id
        ).first()
        
        if not stock or stock.quantity < item.quantity:
            raise HTTPException(status_code=400, detail="库存不足")
        
        # 获取单价
        unit_price = item.unit_price if item.unit_price is not None else goods.price
        total_price = unit_price * item.quantity
        
        # 创建明细项
        new_item = OutboundOrderItem(
            header_id=order_id,
            goods_id=goods.id,
            location_id=location.id,
            quantity=item.quantity,
            unit_price=unit_price,
            total_price=total_price,
            remark=item.remark
        )
        
        # 更新单据总金额
        order.total_amount = (order.total_amount or 0) + total_price
        
        db.add(new_item)
        db.commit()
        db.refresh(new_item)
        
        return {
            "id": new_item.id,
            "goods_id": new_item.goods_id,
            "goods_barcode": goods.barcode,
            "goods_name": goods.name,
            "location_id": new_item.location_id,
            "location_code": location.location_code,
            "quantity": new_item.quantity,
            "unit_price": new_item.unit_price,
            "total_price": new_item.total_price,
            "remark": new_item.remark
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("添加明细失败: %s", str(e))
        raise HTTPException(status_code=500, detail="添加明细失败")

@router.post("/outbound-orders/{order_id}/submit", summary="提交出库单")
async def submit_outbound_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """提交出库单，更新库存"""
    try:
        # 行级锁读取单据头：串行化同一草稿的并发提交，
        # 第二个并发提交会看到已提交状态而被拒绝
        order = lock_order_header(db, OutboundOrderHeader, order_id)
        
        if not order:
            raise HTTPException(status_code=404, detail="出库单不存在")
        
        # 检查权限
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无权限操作此出库单")
        
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="只能提交草稿状态的单据")
        
        if not order.items:
            raise HTTPException(status_code=400, detail="出库单没有明细项")
        
        # 按 (货物, 库位) 汇总需求数量：同一货物/库位的多条明细必须合并后
        # 再与库存比较，否则可拆成多条明细绕过库存检查
        required: Dict[tuple, float] = {}
        names: Dict[tuple, str] = {}
        for item in order.items:
            key = (item.goods_id, item.location_id)
            required[key] = required.get(key, 0.0) + (item.quantity or 0)
            if item.goods:
                names.setdefault(key, item.goods.name)

        # 同一事务内：锁定库存行 → 按汇总数量校验 → 扣减
        for (goods_id, location_id), qty in required.items():
            stock = lock_stock_row(db, order.warehouse_id, goods_id, location_id)
            current = stock.quantity if stock else 0
            if current < qty:
                name = names.get((goods_id, location_id), f"#{goods_id}")
                raise HTTPException(status_code=400, detail=f"货物{name}库存不足（需要 {qty}，现有 {current}）")
            stock.quantity -= qty
            stock.update_time = datetime.now()

        # 记录出库流水（按明细逐条，保留审计轨迹）
        for item in order.items:
            record = InventoryRecord(
                warehouse_id=order.warehouse_id,
                goods_id=item.goods_id,
                location_id=item.location_id,
                type=InventoryType.OUT,
                quantity=item.quantity,
                operator_id=current_user.id,
                remark=f"出库单：{order.order_no}"
            )
            db.add(record)
        
        # 更新订单状态
        order.status = "COMPLETED"
        order.submit_time = datetime.now()
        order.complete_time = datetime.now()
        
        db.commit()
        
        return {"message": "出库单提交成功", "order_no": order.order_no}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("提交出库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="提交出库单失败")

@router.get("/outbound-orders/", response_model=List[OutboundOrderHeaderResponse], summary="获取出库单列表")
async def get_outbound_orders(
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取出库单列表"""
    try:
        query = db.query(OutboundOrderHeader).join(
            Warehouse, OutboundOrderHeader.warehouse_id == Warehouse.id
        ).join(
            User, OutboundOrderHeader.operator_id == User.id
        )
        
        if current_user.role != UserRole.ADMIN:
            # 获取用户有权限的仓库
            user_warehouse_ids = [
                uw.warehouse_id for uw in 
                db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
            ]
            if user_warehouse_ids:
                query = query.filter(OutboundOrderHeader.warehouse_id.in_(user_warehouse_ids))
            else:
                query = query.filter(OutboundOrderHeader.warehouse_id == -1)  # 返回空结果
        
        if status:
            query = query.filter(OutboundOrderHeader.status == status)
        
        if start_date:
            start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
            query = query.filter(OutboundOrderHeader.create_time >= start_datetime)
        
        if end_date:
            end_datetime = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(OutboundOrderHeader.create_time < end_datetime)
        
        orders = query.order_by(OutboundOrderHeader.create_time.desc()).all()
        
        result = []
        for order in orders:
            result.append({
                "id": order.id,
                "order_no": order.order_no,
                "warehouse_id": order.warehouse_id,
                "warehouse_name": order.warehouse.name,
                "customer": order.customer,
                "operator_id": order.operator_id,
                "operator_name": order.operator.full_name,
                "total_amount": order.total_amount,
                "remark": order.remark,
                "status": order.status,
                "create_time": order.create_time,
                "submit_time": order.submit_time,
                "complete_time": order.complete_time,
                "item_count": len(order.items)
            })
        
        return result
        
    except Exception as e:
        logging.exception("获取出库单列表失败: %s", str(e))
        raise HTTPException(status_code=500, detail="获取出库单列表失败")

@router.get("/outbound-orders/{order_id}", response_model=OutboundOrderDetailResponse, summary="获取出库单详情")
async def get_outbound_order_detail(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取出库单详情"""
    try:
        query = db.query(OutboundOrderHeader).filter(OutboundOrderHeader.id == order_id)
        
        if current_user.role != UserRole.ADMIN:
            # 权限检查
            user_warehouse_ids = [
                uw.warehouse_id for uw in 
                db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
            ]
            if user_warehouse_ids:
                query = query.filter(OutboundOrderHeader.warehouse_id.in_(user_warehouse_ids))
            else:
                raise HTTPException(status_code=403, detail="无权限查看此出库单")
        
        order = query.first()
        
        if not order:
            raise HTTPException(status_code=404, detail="出库单不存在")
        
        items = []
        for item in order.items:
            items.append({
                "id": item.id,
                "goods_id": item.goods_id,
                "goods_barcode": item.goods.barcode,
                "goods_name": item.goods.name,
                "location_id": item.location_id,
                "location_code": item.location.location_code,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "total_price": item.total_price,
                "remark": item.remark
            })
        
        return {
            "id": order.id,
            "order_no": order.order_no,
            "warehouse_id": order.warehouse_id,
            "warehouse_name": order.warehouse.name,
            "customer": order.customer,
            "operator_id": order.operator_id,
            "operator_name": order.operator.full_name,
            "total_amount": order.total_amount,
            "remark": order.remark,
            "status": order.status,
            "create_time": order.create_time,
            "submit_time": order.submit_time,
            "complete_time": order.complete_time,
            "item_count": len(order.items),
            "items": items
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("获取出库单详情失败: %s", str(e))
        raise HTTPException(status_code=500, detail="获取出库单详情失败")


@router.put("/outbound-orders/{order_id}", response_model=OutboundOrderHeaderResponse, summary="更新出库单")
async def update_outbound_order(
    order_id: int,
    order: OutboundOrderHeaderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新出库单（仅允许更新草稿状态的订单）"""
    try:
        # 查询订单（行级锁：与"并发提交"串行化，锁内复核状态）
        db_order = lock_order_header(db, OutboundOrderHeader, order_id)

        if not db_order:
            raise HTTPException(status_code=404, detail="出库单不存在")

        # 检查订单状态
        if db_order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许更新草稿状态的出库单")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            # 检查订单是否属于当前用户可操作的仓库
            user_warehouse = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == current_user.id,
                UserWarehouse.warehouse_id == db_order.warehouse_id
            ).first()
            if not user_warehouse:
                raise HTTPException(status_code=403, detail="无权限操作此出库单")

        # 更新订单信息
        db_order.customer = order.customer
        db_order.remark = order.remark
        db.commit()
        db.refresh(db_order)

        # 返回更新后的订单
        return format_outbound_order_response(db_order)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("更新出库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="更新出库单失败")

@router.put("/outbound-orders/{order_id}/items/{item_id}", response_model=OutboundOrderItemResponse, summary="更新出库单明细")
async def update_outbound_order_item(
    order_id: int,
    item_id: int,
    item: OutboundOrderItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新出库单明细（仅允许更新草稿状态的订单的明细）"""
    try:
        # 查询订单（行级锁：与"并发提交"串行化，锁内复核状态）
        order = lock_order_header(db, OutboundOrderHeader, order_id)

        if not order:
            raise HTTPException(status_code=404, detail="出库单不存在")

        # 检查订单状态
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许更新草稿状态的出库单的明细")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            user_warehouse = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == current_user.id,
                UserWarehouse.warehouse_id == order.warehouse_id
            ).first()
            if not user_warehouse:
                raise HTTPException(status_code=403, detail="无权限操作此出库单")

        # 查询明细项
        db_item = db.query(OutboundOrderItem).filter(
            OutboundOrderItem.id == item_id,
            OutboundOrderItem.header_id == order_id
        ).first()

        if not db_item:
            raise HTTPException(status_code=404, detail="出库单明细不存在")

        # 查询货物信息
        goods = db.query(Goods).filter(Goods.barcode == item.goods_barcode).first()
        if not goods:
            raise HTTPException(status_code=404, detail="货物不存在")

        # 查询库位信息
        location = db.query(Location).filter(Location.location_code == item.location_code).first()
        if not location:
            raise HTTPException(status_code=404, detail="库位不存在")

        # 库位必须属于单据仓库（与新增明细/入库编辑的校验一致），防止跨仓脏数据
        if location.warehouse_id != order.warehouse_id:
            raise HTTPException(status_code=400, detail="库位不属于出库单仓库")

        # 更新明细信息
        db_item.goods_id = goods.id
        db_item.goods_name = goods.name
        db_item.goods_barcode = goods.barcode
        db_item.goods_spec = goods.spec
        db_item.location_id = location.id
        db_item.location_code = location.location_code
        db_item.quantity = item.quantity
        # 单价可选（与入库明细编辑一致）：未提供时沿用原明细单价，再退化为货物默认单价
        if item.unit_price is not None:
            db_item.unit_price = item.unit_price
        elif db_item.unit_price is None:
            db_item.unit_price = goods.price or 0
        db_item.total_price = db_item.quantity * db_item.unit_price
        db_item.remark = item.remark
        db.commit()
        db.refresh(db_item)

        # 重新计算订单总金额
        recalculate_outbound_order_total(order_id, db)

        return format_outbound_order_item_response(db_item)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("更新出库单明细失败: %s", str(e))
        raise HTTPException(status_code=500, detail="更新出库单明细失败")

@router.delete("/outbound-orders/{order_id}/items/{item_id}", summary="删除出库单明细")
async def delete_outbound_order_item(
    order_id: int,
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除出库单明细（仅允许删除草稿状态的订单的明细）"""
    try:
        # 查询订单（行级锁：与"并发提交"串行化，锁内复核状态）
        order = lock_order_header(db, OutboundOrderHeader, order_id)

        if not order:
            raise HTTPException(status_code=404, detail="出库单不存在")

        # 检查订单状态
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许删除草稿状态的出库单的明细")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            user_warehouse = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == current_user.id,
                UserWarehouse.warehouse_id == order.warehouse_id
            ).first()
            if not user_warehouse:
                raise HTTPException(status_code=403, detail="无权限操作此出库单")

        # 查询明细项
        db_item = db.query(OutboundOrderItem).filter(
            OutboundOrderItem.id == item_id,
            OutboundOrderItem.header_id == order_id
        ).first()

        if not db_item:
            raise HTTPException(status_code=404, detail="出库单明细不存在")

        # 删除明细项
        db.delete(db_item)
        db.commit()

        # 重新计算订单总金额
        recalculate_outbound_order_total(order_id, db)

        return {"message": "出库单明细删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("删除出库单明细失败: %s", str(e))
        raise HTTPException(status_code=500, detail="删除出库单明细失败")

@router.delete("/outbound-orders/{order_id}", summary="删除出库单")
async def delete_outbound_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除出库单（仅允许删除草稿状态的订单）"""
    try:
        # 查询订单（行级锁：与"并发提交"串行化，锁内复核状态）
        order = lock_order_header(db, OutboundOrderHeader, order_id)

        if not order:
            raise HTTPException(status_code=404, detail="出库单不存在")

        # 检查订单状态
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许删除草稿状态的出库单")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            # 检查订单是否属于当前用户
            if order.operator_id != current_user.id:
                raise HTTPException(status_code=403, detail="无权限删除此出库单")

        # 删除订单（包括所有明细项）
        db.delete(order)
        db.commit()

        return {"message": "出库单删除成功"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("删除出库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="删除出库单失败")
