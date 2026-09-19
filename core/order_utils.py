"""单据工具：编号生成、行级锁、总额重算、响应格式化。"""

from sqlalchemy import tuple_
from sqlalchemy.orm import Session
from datetime import datetime
import zlib

from core.database import engine
from core.models import Stock, InboundOrderHeader, OutboundOrderHeader

# ===== 原 main.py 块: 单据工具 =====
def format_outbound_order_response(order):
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
        "item_count": len(order.items)
    }

def format_outbound_order_item_response(item):
    return {
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
    }

def recalculate_outbound_order_total(order_id, db):
    """重新计算出库单总金额"""
    order = db.query(OutboundOrderHeader).filter(OutboundOrderHeader.id == order_id).first()
    if order:
        total_amount = 0
        for item in order.items:
            total_amount += item.total_price
        order.total_amount = total_amount
        db.commit()
        db.refresh(order)

def recalculate_inbound_order_total(order_id, db):
    """重新计算入库单总金额"""
    order = db.query(InboundOrderHeader).filter(InboundOrderHeader.id == order_id).first()
    if order:
        total_amount = 0
        for item in order.items:
            total_amount += item.total_price
        order.total_amount = total_amount
        db.commit()
        db.refresh(order)

# 生成单据编号
def lock_stock_row(db: Session, warehouse_id: int, goods_id: int, location_id: int):
    """行级锁读取库存行（防止并发超卖/负库存）。
    PostgreSQL 使用 FOR UPDATE 行锁（跨会话互斥，同一会话可重入）；
    SQLite 等不支持时退化为普通查询。
    """
    q = db.query(Stock).filter(
        Stock.warehouse_id == warehouse_id,
        Stock.goods_id == goods_id,
        Stock.location_id == location_id
    )
    try:
        q = q.with_for_update()
    except Exception:
        pass
    return q.first()

def lock_stock_rows_for_keys(db: Session, warehouse_id: int, keys):
    """按 Stock.id 全局稳定顺序锁定指定 (goods_id, location_id) 库存行。

    多明细入/出库先一次性调用本函数，申请审批也按同一 Stock.id 顺序锁行，避免不同
    业务路径因明细顺序、库位编号与库存行创建顺序不同而形成循环等待。
    """
    normalized = sorted(set(keys))
    if not normalized:
        return []
    q = (
        db.query(Stock)
        .filter(
            Stock.warehouse_id == warehouse_id,
            tuple_(Stock.goods_id, Stock.location_id).in_(normalized),
        )
        .order_by(Stock.id)
    )
    try:
        q = q.with_for_update()
    except Exception:
        pass
    return q.all()

def lock_stock_rows_for_goods(db: Session, warehouse_id: int, goods_ids):
    """按 Stock.id 全局稳定顺序锁定仓库内指定货物的全部库存行。"""
    normalized = sorted(set(goods_ids))
    if not normalized:
        return []
    q = (
        db.query(Stock)
        .filter(Stock.warehouse_id == warehouse_id, Stock.goods_id.in_(normalized))
        .order_by(Stock.id)
    )
    try:
        q = q.with_for_update()
    except Exception:
        pass
    return q.all()

def lock_order_header(db: Session, model, order_id):
    """行级锁读取单据头（PostgreSQL FOR UPDATE），串行化同一单据的并发提交，
    防止草稿被并发重复提交导致库存被扣减/增加两次。SQLite 下退化为普通查询。"""
    q = db.query(model).filter(model.id == order_id)
    try:
        q = q.with_for_update()
    except Exception:
        pass
    return q.first()

def advisory_lock_stock_key(db: Session, warehouse_id: int, goods_id: int, location_id: int):
    """PostgreSQL 事务咨询锁：串行化"同一 (仓库,货物,库位) 库存行"的并发创建。
    行不存在时 FOR UPDATE 锁不到任何东西，两个并发首笔入库都会看到'无行'而各插一条，
    触发复合唯一约束（500）。先持咨询锁再重查：后到者等前一个事务提交后能看到新行，
    从而改为更新而不是插入。SQLite 下退化为 no-op。"""
    try:
        if engine.dialect.name == "postgresql":
            from sqlalchemy import text as _sa_text
            k = int(zlib.crc32(f"stockrow-{warehouse_id}-{goods_id}-{location_id}".encode("utf-8")))
            db.execute(_sa_text("SELECT pg_advisory_xact_lock(:k)"), {"k": k})
    except Exception:
        pass

def advisory_lock_idempotency_key(db: Session, operation: str, key: str):
    """串行化同一幂等键的首次提交，避免并发重试各自创建业务单据。"""
    try:
        if engine.dialect.name == "postgresql":
            from sqlalchemy import text as _sa_text
            lock_key = int(zlib.crc32(f"idempotency-{operation}-{key}".encode("utf-8")))
            db.execute(_sa_text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})
    except Exception:
        pass

def generate_order_no(prefix: str, db: Session) -> str:
    """生成单据编号（PostgreSQL 下用事务咨询锁串行化编号生成，防止并发撞号）"""
    today = datetime.now().strftime("%Y%m%d")
    try:
        if engine.dialect.name == "postgresql":
            from sqlalchemy import text as _sa_text
            db.execute(_sa_text("SELECT pg_advisory_xact_lock(:k)"),
                       {"k": int(zlib.crc32(f"orderno-{prefix}-{today}".encode("utf-8")))} )
    except Exception:
        pass
    # 取当天已存在单据的最大数字尾号 +1（而不是"计数+1"：
    # 计数+1 在删除较早单据后会复用仍存在的编号；最大尾号+1 不会）
    model = InboundOrderHeader if prefix == "IN" else OutboundOrderHeader
    prefix_len = len(prefix) + len(today)
    existing = db.query(model.order_no).filter(
        model.order_no.like(f"{prefix}{today}%")
    ).all()
    max_seq = 0
    for (no,) in existing:
        if isinstance(no, str) and len(no) > prefix_len and no[prefix_len:].isdigit():
            max_seq = max(max_seq, int(no[prefix_len:]))
    return f"{prefix}{today}{str(max_seq + 1).zfill(3)}"
