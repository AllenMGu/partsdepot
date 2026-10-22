"""按仓库隔离的货物档案和导入/导出。"""

import io
import logging
from typing import List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.access import require_warehouse
from core.config import MAX_IMPORT_FILE_SIZE_BYTES
from core.deps import get_db, read_upload_with_size_limit
from core.models import (Goods, User, UserRole, Stock, InventoryRecord, CheckRecord,
                         CheckOrderItem, InboundOrderItem, OutboundOrderItem,
                          Warehouse, Request, RequestItem, RequestStatus)
from core.schemas import GoodsCreate, GoodsResponse, GoodsUpdate
from core.security import get_current_user

router = APIRouter()


def _scope(db: Session, user: User, warehouse_id: Optional[int], *, manage: bool = False) -> int:
    # 明确传入的仓库不能与登录会话当前仓库不符，避免切仓后旧页面跨仓写入。
    selected = user.current_warehouse_id
    if warehouse_id is not None and selected is not None and warehouse_id != selected:
        raise HTTPException(status_code=403, detail="请先切换到目标仓库")
    return require_warehouse(db, user, warehouse_id or selected, manage=manage)


def _pending_request_ref_exists(db: Session, warehouse_id: int, barcode: str) -> bool:
    """同仓待审批申请是否引用该条码（删除/改条码共用，评审 P1）。

    覆盖审批的两条仓库解析路径：
    1) 申请单明确指定本仓库；
    2) 历史申请单未指定仓库，审批时解析到本仓库（全系统恰好一个启用仓库）。
    多仓且历史单未指定仓库时审批本就会因"无法确定仓库"被拒，不构成新增故障。
    """
    same_wh_ref = (
        db.query(RequestItem.id)
        .join(Request, RequestItem.request_id == Request.id)
        .filter(
            RequestItem.barcode == barcode,
            Request.warehouse_id == warehouse_id,
            Request.status == RequestStatus.PENDING.value,
        )
        .first()
    )
    if same_wh_ref:
        return True
    legacy_ref = (
        db.query(RequestItem.id)
        .join(Request, RequestItem.request_id == Request.id)
        .filter(
            RequestItem.barcode == barcode,
            Request.warehouse_id.is_(None),
            Request.status == RequestStatus.PENDING.value,
        )
        .first()
    )
    if legacy_ref:
        active_ids = [w.id for w in db.query(Warehouse.id).filter(Warehouse.is_active.is_(True)).all()]
        if len(active_ids) == 1 and active_ids[0] == warehouse_id:
            return True
    return False


@router.post("/goods/", response_model=GoodsResponse, summary="新增本仓库货物")
async def create_goods(goods: GoodsCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    warehouse_id = _scope(db, current_user, goods.warehouse_id, manage=True)
    if db.query(Goods.id).filter(Goods.warehouse_id == warehouse_id, Goods.barcode == goods.barcode).first():
        raise HTTPException(status_code=400, detail="当前仓库货物条码已存在")
    row = Goods(**goods.model_dump(exclude={"warehouse_id"}), warehouse_id=warehouse_id)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="当前仓库货物条码已存在")
    db.refresh(row)
    return row


@router.get("/goods/", response_model=List[GoodsResponse], summary="查询当前仓库货物")
async def get_goods(keyword: Optional[str] = None, warehouse_id: Optional[int] = None,
                    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Read-only lookups may target any granted warehouse (e.g. an application opened
    # from another warehouse). Mutations still require the selected warehouse.
    wid = require_warehouse(db, current_user, warehouse_id) if warehouse_id is not None else _scope(db, current_user, None)
    query = db.query(Goods).filter(Goods.warehouse_id == wid)
    if keyword:
        query = query.filter(Goods.name.contains(keyword) | Goods.barcode.contains(keyword) | Goods.spec.contains(keyword))
    rows = query.order_by(Goods.id).all()
    if current_user.role == UserRole.VIEWER:
        return [dict(id=g.id, warehouse_id=g.warehouse_id, barcode=g.barcode, name=g.name,
                     spec=g.spec, unit=g.unit, price=None, create_time=g.create_time) for g in rows]
    return rows


@router.get("/goods/export", summary="导出当前仓库货物")
async def export_goods(warehouse_id: Optional[int] = None, current_user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    wid = _scope(db, current_user, warehouse_id, manage=True)
    rows = db.query(Goods).filter(Goods.warehouse_id == wid).order_by(Goods.id).all()
    columns = ["条码", "货物名称", "规格型号", "单位", "单价", "创建时间"]
    data = [{"条码": g.barcode, "货物名称": g.name, "规格型号": g.spec, "单位": g.unit,
             "单价": g.price, "创建时间": g.create_time.strftime("%Y-%m-%d %H:%M:%S")}
            for g in rows]
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(data, columns=columns).to_excel(writer, index=False, sheet_name="货物数据")
    output.seek(0)
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename=goods_{wid}.xlsx"})


def _get_visible(db: Session, user: User, goods_id: int, *, manage: bool = False, lock: bool = False) -> Goods:
    q = db.query(Goods).filter(Goods.id == goods_id)
    if lock:
        # 删除路径需与"提交申请/修改明细"的货物行锁互斥（评审 P2 并发竞态）
        try:
            q = q.with_for_update()
        except Exception:
            pass
    row = q.first()
    if not row:
        raise HTTPException(status_code=404, detail="货物未找到")
    wid = _scope(db, user, None, manage=manage)
    if row.warehouse_id != wid:
        raise HTTPException(status_code=404, detail="货物未找到")
    return row


@router.get("/goods/{id}", response_model=GoodsResponse, summary="获取本仓库货物")
async def get_goods_detail(id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    g = _get_visible(db, current_user, id)
    if current_user.role == UserRole.VIEWER:
        return dict(id=g.id, warehouse_id=g.warehouse_id, barcode=g.barcode, name=g.name,
                    spec=g.spec, unit=g.unit, price=None, create_time=g.create_time)
    return g


@router.put("/goods/{id}", response_model=GoodsResponse, summary="修改本仓库货物")
async def update_goods(id: int, goods: GoodsUpdate, current_user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    # lock=True：与"提交申请/修改申请明细"共用同一货物行锁（评审 P1），
    # 条码改写的检查与提交路径的行锁在同一行上串行化，避免竞态。
    row = _get_visible(db, current_user, id, manage=True, lock=True)
    data = goods.model_dump(exclude_unset=True)
    new_barcode = data.get("barcode")
    if new_barcode is not None and new_barcode != row.barcode:
        # 评审 P1：申请明细只存条码快照（无 goods_id 外键），本仓待审批申请引用旧条码时
        # 改条码会让审批按旧条码找不到档案而卡死。持行锁、同事务内检查，存在则 409。
        if _pending_request_ref_exists(db, row.warehouse_id, row.barcode):
            raise HTTPException(status_code=409,
                                detail="该货物已被本仓库待审批申请单引用，不能修改条码；请先驳回或处理相关申请单")
    for key, value in data.items():
        setattr(row, key, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="当前仓库货物条码已存在")
    db.refresh(row)
    return row


@router.delete("/goods/{id}", summary="删除本仓库货物")
async def delete_goods(id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # lock=True：与"提交申请/修改申请明细"在货物档案行上互斥（评审 P2），
    # 防止检查通过后并发提交产生悬空的待审批货物引用。
    row = _get_visible(db, current_user, id, manage=True, lock=True)
    # SQLite installations may not enable FK enforcement; keep history references intact.
    for model in (Stock, InventoryRecord, CheckRecord, CheckOrderItem, InboundOrderItem, OutboundOrderItem):
        if db.query(model.id).filter(model.goods_id == id).first():
            raise HTTPException(status_code=409, detail="货物已有库存或业务记录，无法删除")
    # 申请单货物明细按条码引用档案（无 goods_id 外键）：删除后待审批申请将无法
    # 通过审批（找不到本仓库货物档案，400 卡死）。仓库解析路径与改条码检查共用。
    if _pending_request_ref_exists(db, row.warehouse_id, row.barcode):
        raise HTTPException(status_code=409, detail="该货物已被本仓库待审批申请单引用，无法删除；请先驳回或处理相关申请单")
    db.delete(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="货物已有库存或业务记录，无法删除")
    return {"message": "删除成功"}


@router.post("/goods/import", summary="导入当前仓库货物")
async def import_goods(file: UploadFile = File(...), warehouse_id: Optional[int] = None,
                       current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wid = _scope(db, current_user, warehouse_id, manage=True)
    if not file.filename or not file.filename.lower().endswith((".xls", ".xlsx")):
        raise HTTPException(status_code=400, detail="仅支持导入 .xls 或 .xlsx 文件")
    contents = await read_upload_with_size_limit(file, MAX_IMPORT_FILE_SIZE_BYTES)
    try:
        df = pd.read_excel(io.BytesIO(contents), dtype={"条码": str})
    except Exception:
        raise HTTPException(status_code=400, detail="Excel 文件无法读取")
    required = ["条码", "货物名称", "规格型号", "单位", "单价"]
    for col in required:
        if col not in df.columns:
            raise HTTPException(status_code=400, detail=f"缺少必要列：{col}")
    existing = {code for (code,) in db.query(Goods.barcode).filter(Goods.warehouse_id == wid).all()}
    errors = []
    count = 0
    for index, row in df.iterrows():
        code = str(row["条码"]).strip() if pd.notna(row["条码"]) else ""
        if not code or code in existing:
            errors.append(f"第{index+2}行：条码为空或当前仓库已存在")
            continue
        try:
            price = float(row["单价"]) if pd.notna(row["单价"]) else 0.0
            if price < 0:
                raise ValueError("negative price")
            db.add(Goods(warehouse_id=wid, barcode=code, name=str(row["货物名称"]),
                         spec=str(row["规格型号"]) if pd.notna(row["规格型号"]) else "",
                         unit=str(row["单位"]) if pd.notna(row["单位"]) else "个", price=price))
            existing.add(code)
            count += 1
        except (TypeError, ValueError):
            errors.append(f"第{index+2}行：数据格式错误")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="导入冲突，请刷新当前仓库货物后重试")
    return {"success_count": count, "error_count": len(errors), "errors": errors}
