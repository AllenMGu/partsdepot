"""入库单管理路由。"""

from fastapi import HTTPException, Depends, status, APIRouter
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional, Dict
import logging

from core.models import UserRole, InventoryType, Warehouse, UserWarehouse, User, Location, Goods, Stock, InventoryRecord, InboundOrderHeader, InboundOrderItem, OutboundOrderHeader, OutboundOrderItem
from core.schemas import InboundOrderItemCreate, InboundOrderItemResponse, InboundOrderHeaderCreate, InboundOrderHeaderResponse, InboundOrderDetailResponse
from core.security import get_current_user
from core.deps import get_db
from core.order_utils import format_outbound_order_response, recalculate_outbound_order_total, recalculate_inbound_order_total, lock_stock_row, lock_order_header, advisory_lock_stock_key, generate_order_no

router = APIRouter()

# ===== 原 main.py 块: 入库单 =====
# ------------------- 入库单管理接口 -------------------
@router.post("/inbound-orders/", response_model=InboundOrderHeaderResponse, summary="创建入库单")
async def create_inbound_order(
    order: InboundOrderHeaderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """创建入库单（表头）"""
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
        order_no = generate_order_no("IN", db)
        
        # 创建入库单头
        new_order = InboundOrderHeader(
            order_no=order_no,
            warehouse_id=current_user.current_warehouse_id,
            supplier=order.supplier,
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
            "supplier": new_order.supplier,
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
        logging.exception("创建入库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="创建入库单失败")

@router.post("/inbound-orders/{order_id}/items", response_model=InboundOrderItemResponse, summary="添加入库单明细")
async def add_inbound_order_item(
    order_id: int,
    item: InboundOrderItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """向入库单添加明细项"""
    try:
        # 检查订单是否存在且属于当前用户仓库（行级锁：与"并发提交"串行化，
        # 防止"已提交单据出现未过账新明细"的竞争窗口）
        order = lock_order_header(db, InboundOrderHeader, order_id)

        if not order:
            raise HTTPException(status_code=404, detail="入库单不存在")
        
        # 检查权限
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无权限操作此入库单")
        
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
            raise HTTPException(status_code=400, detail="库位不属于入库单仓库")
        
        # 获取单价（如果未提供，使用货物默认单价）
        unit_price = item.unit_price if item.unit_price is not None else goods.price
        total_price = unit_price * item.quantity
        
        # 创建明细项
        new_item = InboundOrderItem(
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

@router.post("/inbound-orders/{order_id}/submit", summary="提交入库单")
async def submit_inbound_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """提交入库单，更新库存"""
    try:
        # 行级锁读取单据头：串行化同一草稿的并发提交，防止重复入库
        order = lock_order_header(db, InboundOrderHeader, order_id)
        
        if not order:
            raise HTTPException(status_code=404, detail="入库单不存在")
        
        # 检查权限
        user_warehouse = db.query(UserWarehouse).filter(
            UserWarehouse.user_id == current_user.id,
            UserWarehouse.warehouse_id == order.warehouse_id
        ).first()
        if not user_warehouse:
            raise HTTPException(status_code=403, detail="无权限操作此入库单")
        
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="只能提交草稿状态的单据")
        
        # 检查是否有明细
        if not order.items:
            raise HTTPException(status_code=400, detail="入库单没有明细项")
        
        # 开始事务（行级锁读取库存行，防止并发下丢失更新）
        # 先按 (货物,库位) 汇总入库量：同一组合的多条明细只处理一次库存，
        # 避免"无库存行时逐条查询并各插一条"在同一会话内触发复合唯一约束（500）
        inbound_qty: Dict[tuple, float] = {}
        for item in order.items:
            key = (item.goods_id, item.location_id)
            inbound_qty[key] = inbound_qty.get(key, 0.0) + (item.quantity or 0)

        for (goods_id, location_id), qty in inbound_qty.items():
            stock = lock_stock_row(db, order.warehouse_id, goods_id, location_id)
            if not stock:
                # 库存行尚不存在：先持咨询锁串行化并发建行，再重查
                #（后到者等前一个事务提交后能看到新行，从而改为更新）
                advisory_lock_stock_key(db, order.warehouse_id, goods_id, location_id)
                stock = lock_stock_row(db, order.warehouse_id, goods_id, location_id)
            if stock:
                stock.quantity += qty
                stock.update_time = datetime.now()
            else:
                stock = Stock(
                    warehouse_id=order.warehouse_id,
                    goods_id=goods_id,
                    location_id=location_id,
                    quantity=qty
                )
                db.add(stock)

        # 记录出入库流水（按原始明细逐条，保留审计轨迹）
        for item in order.items:
            record = InventoryRecord(
                warehouse_id=order.warehouse_id,
                goods_id=item.goods_id,
                location_id=item.location_id,
                type=InventoryType.IN,
                quantity=item.quantity,
                operator_id=current_user.id,
                remark=f"入库单：{order.order_no}"
            )
            db.add(record)
        
        # 更新订单状态
        order.status = "COMPLETED"
        order.submit_time = datetime.now()
        order.complete_time = datetime.now()
        
        db.commit()
        
        return {"message": "入库单提交成功", "order_no": order.order_no}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("提交入库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="提交入库单失败")

@router.get("/inbound-orders/", response_model=List[InboundOrderHeaderResponse], summary="获取入库单列表")
async def get_inbound_orders(
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取入库单列表"""
    try:
        query = db.query(InboundOrderHeader).join(
            Warehouse, InboundOrderHeader.warehouse_id == Warehouse.id
        ).join(
            User, InboundOrderHeader.operator_id == User.id
        )
        
        # 权限过滤
        if current_user.role != UserRole.ADMIN:
            # 获取用户有权限的仓库
            user_warehouse_ids = [
                uw.warehouse_id for uw in 
                db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
            ]
            if user_warehouse_ids:
                query = query.filter(InboundOrderHeader.warehouse_id.in_(user_warehouse_ids))
            else:
                query = query.filter(InboundOrderHeader.warehouse_id == -1)  # 返回空结果
        
        # 状态过滤
        if status:
            query = query.filter(InboundOrderHeader.status == status)
        
        # 日期过滤
        if start_date:
            start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
            query = query.filter(InboundOrderHeader.create_time >= start_datetime)
        
        if end_date:
            end_datetime = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(InboundOrderHeader.create_time < end_datetime)
        
        orders = query.order_by(InboundOrderHeader.create_time.desc()).all()
        
        result = []
        for order in orders:
            result.append({
                "id": order.id,
                "order_no": order.order_no,
                "warehouse_id": order.warehouse_id,
                "warehouse_name": order.warehouse.name,
                "supplier": order.supplier,
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
        logging.exception("获取入库单列表失败: %s", str(e))
        raise HTTPException(status_code=500, detail="获取入库单列表失败")

@router.get("/inbound-orders/{order_id}", response_model=InboundOrderDetailResponse, summary="获取入库单详情")
async def get_inbound_order_detail(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取入库单详情"""
    try:
        query = db.query(InboundOrderHeader).filter(InboundOrderHeader.id == order_id)

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            user_warehouse_ids = [
                uw.warehouse_id for uw in
                db.query(UserWarehouse).filter(UserWarehouse.user_id == current_user.id).all()
            ]
            if user_warehouse_ids:
                query = query.filter(InboundOrderHeader.warehouse_id.in_(user_warehouse_ids))
            else:
                raise HTTPException(status_code=403, detail="无权限查看此入库单")

        order = query.first()

        if not order:
            raise HTTPException(status_code=404, detail="入库单不存在")

        # 获取明细
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
            "supplier": order.supplier,
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
        logging.exception("获取入库单详情失败: %s", str(e))
        raise HTTPException(status_code=500, detail="获取入库单详情失败")


@router.put("/inbound-orders/{order_id}", response_model=InboundOrderHeaderResponse, summary="更新入库单")
async def update_inbound_order(
    order_id: int,
    order: InboundOrderHeaderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新入库单（仅允许更新草稿状态的订单）"""
    try:
        # 查询订单（行级锁：与"并发提交"串行化，锁内复核状态）
        db_order = lock_order_header(db, InboundOrderHeader, order_id)

        if not db_order:
            raise HTTPException(status_code=404, detail="入库单不存在")

        # 检查订单状态
        if db_order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许更新草稿状态的入库单")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            # 检查订单是否属于当前用户可操作的仓库
            user_warehouse = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == current_user.id,
                UserWarehouse.warehouse_id == db_order.warehouse_id
            ).first()
            if not user_warehouse:
                raise HTTPException(status_code=403, detail="无权限操作此入库单")

        # 更新订单信息
        db_order.supplier = order.supplier
        db_order.remark = order.remark
        db.commit()
        db.refresh(db_order)

        # 返回更新后的订单（与列表接口同构的响应结构）
        return {
            "id": db_order.id,
            "order_no": db_order.order_no,
            "warehouse_id": db_order.warehouse_id,
            "warehouse_name": db_order.warehouse.name if db_order.warehouse else "",
            "supplier": db_order.supplier,
            "operator_id": db_order.operator_id,
            "operator_name": db_order.operator.full_name if db_order.operator else "",
            "total_amount": db_order.total_amount or 0,
            "remark": db_order.remark,
            "status": db_order.status,
            "create_time": db_order.create_time,
            "submit_time": db_order.submit_time,
            "complete_time": db_order.complete_time,
            "item_count": len(db_order.items)
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("更新入库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="更新入库单失败")

@router.put("/inbound-orders/{order_id}/items/{item_id}", response_model=InboundOrderItemResponse, summary="更新入库单明细")
async def update_inbound_order_item(
    order_id: int,
    item_id: int,
    item: InboundOrderItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新入库单明细（仅允许更新草稿状态的订单的明细）"""
    try:
        # 查询订单（行级锁：与"并发提交"串行化，锁内复核状态）
        order = lock_order_header(db, InboundOrderHeader, order_id)

        if not order:
            raise HTTPException(status_code=404, detail="入库单不存在")

        # 检查订单状态
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许更新草稿状态的入库单的明细")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            user_warehouse = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == current_user.id,
                UserWarehouse.warehouse_id == order.warehouse_id
            ).first()
            if not user_warehouse:
                raise HTTPException(status_code=403, detail="无权限操作此入库单")

        # 查询明细项
        db_item = db.query(InboundOrderItem).filter(
            InboundOrderItem.id == item_id,
            InboundOrderItem.header_id == order_id
        ).first()

        if not db_item:
            raise HTTPException(status_code=404, detail="入库单明细不存在")

        # 查询货物信息
        goods = db.query(Goods).filter(Goods.barcode == item.goods_barcode).first()
        if not goods:
            raise HTTPException(status_code=404, detail="货物不存在")

        # 查询库位信息
        location = db.query(Location).filter(Location.location_code == item.location_code).first()
        if not location:
            raise HTTPException(status_code=404, detail="库位不存在")

        # 库位必须属于单据仓库（与新增明细的校验一致）：
        # 否则提交时会按"单据仓库 + 他仓库位"的组合写库存和流水，造成跨仓脏数据
        if location.warehouse_id != order.warehouse_id:
            raise HTTPException(status_code=400, detail="库位不属于入库单仓库")

        # 更新明细信息
        db_item.goods_id = goods.id
        db_item.goods_name = goods.name
        db_item.goods_barcode = goods.barcode
        db_item.goods_spec = goods.spec
        db_item.location_id = location.id
        db_item.location_code = location.location_code
        db_item.quantity = item.quantity
        # 单价可选：未提供时沿用原明细单价，再退化为货物默认单价（避免 None 参与乘法）
        if item.unit_price is not None:
            db_item.unit_price = item.unit_price
        elif db_item.unit_price is None:
            db_item.unit_price = goods.price or 0
        db_item.total_price = db_item.quantity * db_item.unit_price
        db_item.remark = item.remark
        db.commit()
        db.refresh(db_item)

        # 重新计算订单总金额
        recalculate_inbound_order_total(order_id, db)

        return {
            "id": db_item.id,
            "goods_id": db_item.goods_id,
            "goods_barcode": db_item.goods_barcode,
            "goods_name": db_item.goods_name,
            "location_id": db_item.location_id,
            "location_code": db_item.location_code,
            "quantity": db_item.quantity,
            "unit_price": db_item.unit_price,
            "total_price": db_item.total_price,
            "remark": db_item.remark
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("更新入库单明细失败: %s", str(e))
        raise HTTPException(status_code=500, detail="更新入库单明细失败")

@router.delete("/inbound-orders/{order_id}/items/{item_id}", summary="删除入库单明细")
async def delete_inbound_order_item(
    order_id: int,
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除入库单明细（仅允许删除草稿状态的订单的明细）"""
    try:
        # 查询订单（行级锁：与"并发提交"串行化，锁内复核状态）
        order = lock_order_header(db, InboundOrderHeader, order_id)

        if not order:
            raise HTTPException(status_code=404, detail="入库单不存在")

        # 检查订单状态
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许删除草稿状态的入库单的明细")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            user_warehouse = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == current_user.id,
                UserWarehouse.warehouse_id == order.warehouse_id
            ).first()
            if not user_warehouse:
                raise HTTPException(status_code=403, detail="无权限操作此入库单")

        # 查询明细项
        db_item = db.query(InboundOrderItem).filter(
            InboundOrderItem.id == item_id,
            InboundOrderItem.header_id == order_id
        ).first()

        if not db_item:
            raise HTTPException(status_code=404, detail="入库单明细不存在")

        # 删除明细项
        db.delete(db_item)
        db.commit()

        # 重新计算订单总金额
        recalculate_inbound_order_total(order_id, db)

        return {"message": "入库单明细删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("删除入库单明细失败: %s", str(e))
        raise HTTPException(status_code=500, detail="删除入库单明细失败")

@router.post("/inbound-orders/{order_id}/return", summary="退库")
async def return_inbound_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """根据入库单创建退库出库单"""
    try:
        # 查询入库单
        inbound_order = db.query(InboundOrderHeader).filter(InboundOrderHeader.id == order_id).first()

        if not inbound_order:
            raise HTTPException(status_code=404, detail="入库单不存在")

        # 检查订单状态
        if inbound_order.status != "COMPLETED":
            raise HTTPException(status_code=400, detail="仅允许对已完成的入库单进行退库操作")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            user_warehouse = db.query(UserWarehouse).filter(
                UserWarehouse.user_id == current_user.id,
                UserWarehouse.warehouse_id == inbound_order.warehouse_id
            ).first()
            if not user_warehouse:
                raise HTTPException(status_code=403, detail="无权限操作此入库单")

        # 检查是否已经有退库单（使用英文备注避免编码问题）
        existing_return_order = db.query(OutboundOrderHeader).filter(
            OutboundOrderHeader.remark.contains(f"Return: Original inbound order {inbound_order.order_no}")
        ).first()

        if existing_return_order:
            raise HTTPException(status_code=400, detail=f"该入库单已创建退库单 {existing_return_order.order_no}")

        # 生成退库出库单
        order_no = generate_order_no("OUT", db)
        outbound_order = OutboundOrderHeader(
            order_no=order_no,
            warehouse_id=inbound_order.warehouse_id,
            customer=inbound_order.supplier,  # 退库时客户填原供应商
            operator_id=current_user.id,
            total_amount=inbound_order.total_amount,
            remark=f"Return: Original inbound order {inbound_order.order_no}",
            status="DRAFT"
        )
        db.add(outbound_order)
        db.commit()
        db.refresh(outbound_order)

        # 转换入库单明细为出库单明细
        for inbound_item in inbound_order.items:
            outbound_item = OutboundOrderItem(
                header_id=outbound_order.id,
                goods_id=inbound_item.goods_id,
                location_id=inbound_item.location_id,
                quantity=inbound_item.quantity,
                unit_price=inbound_item.unit_price,
                total_price=inbound_item.total_price,
                remark=f"退库：原入库单明细"
            )
            db.add(outbound_item)

        db.commit()
        db.refresh(outbound_order)

        # 重新计算出库单总金额
        recalculate_outbound_order_total(outbound_order.id, db)

        return format_outbound_order_response(outbound_order)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("创建退库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="创建退库单失败")

@router.delete("/inbound-orders/{order_id}", summary="删除入库单")
async def delete_inbound_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除入库单（仅允许删除草稿状态的订单）"""
    try:
        # 查询订单（行级锁：与"并发提交"串行化，锁内复核状态）
        order = lock_order_header(db, InboundOrderHeader, order_id)

        if not order:
            raise HTTPException(status_code=404, detail="入库单不存在")

        # 检查订单状态
        if order.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅允许删除草稿状态的入库单")

        # 权限检查
        if current_user.role != UserRole.ADMIN:
            # 检查订单是否属于当前用户
            if order.operator_id != current_user.id:
                raise HTTPException(status_code=403, detail="无权限删除此入库单")

        # 删除订单（包括所有明细项）
        db.delete(order)
        db.commit()

        return {"message": "入库单删除成功"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception("删除入库单失败: %s", str(e))
        raise HTTPException(status_code=500, detail="删除入库单失败")
