"""按仓库隔离的货物档案和导入/导出。"""

import io
import json
import math
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
                          Warehouse, Request, RequestItem, RequestStatus, GoodsStockAlertAudit)
from core.schemas import GoodsCreate, GoodsResponse, GoodsUpdate
from core.security import get_current_user

router = APIRouter()


def _scope(db: Session, user: User, warehouse_id: Optional[int], *, manage: bool = False) -> int:
    # 明确传入的仓库不能与登录会话当前仓库不符，避免切仓后旧页面跨仓写入。
    selected = user.current_warehouse_id
    if warehouse_id is not None and selected is not None and warehouse_id != selected:
        raise HTTPException(status_code=403, detail="请先切换到目标仓库")
    return require_warehouse(db, user, warehouse_id or selected, manage=manage)


def _alert_config(goods):
    return {"stock_alert_enabled": goods.stock_alert_enabled,
            "min_stock_quantity": goods.min_stock_quantity}


def _validate_alert_config(config):
    quantity = config["min_stock_quantity"]
    if quantity is not None and (not math.isfinite(quantity) or quantity < 0):
        raise ValueError("最低库存必须是非负有限数字")
    if config["stock_alert_enabled"] and quantity is None:
        raise ValueError("启用预警时必须填写最低库存")


def _audit_alert_change(db, goods, user, before):
    after = _alert_config(goods)
    configured = after["stock_alert_enabled"] or after["min_stock_quantity"] is not None
    if before != after and (before is not None or configured):
        db.add(GoodsStockAlertAudit(
            goods_id=goods.id, warehouse_id=goods.warehouse_id, operator_id=user.id,
            before_json=json.dumps(before) if before is not None else None,
            after_json=json.dumps(after),
        ))


def _excel_alert_config(row):
    config = {}
    enabled = row.get("是否预警")
    if pd.notna(enabled) and str(enabled).strip():
        value = str(enabled).strip().lower()
        if value in ("是", "true", "1", "1.0"):
            config["stock_alert_enabled"] = True
        elif value in ("否", "false", "0", "0.0"):
            config["stock_alert_enabled"] = False
        else:
            raise ValueError("是否预警仅接受是/否、true/false、1/0")
    quantity = row.get("最低库存")
    if pd.notna(quantity) and str(quantity).strip():
        try:
            config["min_stock_quantity"] = float(quantity)
        except (TypeError, ValueError):
            raise ValueError("最低库存必须是非负有限数字") from None
    return config


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
    try:
        _validate_alert_config(_alert_config(goods))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.add(row)
    try:
        db.flush()
        _audit_alert_change(db, row, current_user, None)
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
                     spec=g.spec, unit=g.unit, price=None, create_time=g.create_time, **_alert_config(g)) for g in rows]
    return rows


@router.get("/goods/export", summary="导出当前仓库货物")
async def export_goods(warehouse_id: Optional[int] = None, template: bool = False, current_user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    wid = _scope(db, current_user, warehouse_id, manage=True)
    rows = [] if template else db.query(Goods).filter(Goods.warehouse_id == wid).order_by(Goods.id).all()
    columns = ["条码", "货物名称", "规格型号", "单位", "单价", "创建时间", "是否预警", "最低库存"]
    data = [{"条码": g.barcode, "货物名称": g.name, "规格型号": g.spec, "单位": g.unit,
             "单价": g.price, "是否预警": "是" if g.stock_alert_enabled else "否",
             "最低库存": g.min_stock_quantity, "创建时间": g.create_time.strftime("%Y-%m-%d %H:%M:%S")}
            for g in rows]
    filename = "goods_template.xlsx" if template else f"goods_{wid}.xlsx"
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(data, columns=columns).to_excel(writer, index=False, sheet_name="货物数据")
    output.seek(0)
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename={filename}"})


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
                    spec=g.spec, unit=g.unit, price=None, create_time=g.create_time, **_alert_config(g))
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
    before = _alert_config(row)
    try:
        _validate_alert_config({**before, **{key: data[key] for key in before if key in data}})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for key, value in data.items():
        setattr(row, key, value)
    _audit_alert_change(db, row, current_user, before)
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
        with pd.ExcelFile(io.BytesIO(contents)) as workbook:
            df = workbook.parse(dtype={"条码": str})
            alert_columns = {"是否预警", "最低库存"}.intersection(df.columns)
            if alert_columns:
                raw_alerts = workbook.parse(usecols=list(alert_columns), keep_default_na=False)
                for col in alert_columns:
                    df[col] = raw_alerts[col]
    except Exception:
        raise HTTPException(status_code=400, detail="Excel 文件无法读取")
    required = ["条码", "货物名称", "规格型号", "单位", "单价"]
    for col in required:
        if col not in df.columns:
            raise HTTPException(status_code=400, detail=f"缺少必要列：{col}")
    # Lock existing targets in consistent order for concurrent config changes.
    codes = sorted({str(value).strip() for value in df["条码"].dropna()} - {""})
    existing = {g.barcode: g for g in db.query(Goods)
                .filter(Goods.warehouse_id == wid, Goods.barcode.in_(codes))
                .order_by(Goods.id).with_for_update().all()}
    errors = []
    seen = set()
    audit_entries = []
    for index, row in df.iterrows():
        code = str(row["条码"]).strip() if pd.notna(row["条码"]) else ""
        if not code or code in seen:
            errors.append(f"第{index+2}行：条码为空或当前仓库已存在")
            continue
        goods = existing.get(code)
        before = _alert_config(goods) if goods else None
        try:
            config = _excel_alert_config(row)
            if goods and not config:
                raise ValueError("当前仓库条码已存在")
            _validate_alert_config({"stock_alert_enabled": False, "min_stock_quantity": None,
                                    **(before or {}), **config})
            if goods:
                for key, value in config.items():
                    setattr(goods, key, value)
            else:
                price = float(row["单价"]) if pd.notna(row["单价"]) else 0.0
                if price < 0:
                    raise ValueError("单价不能为负数")
                goods = Goods(warehouse_id=wid, barcode=code, name=str(row["货物名称"]),
                              spec=str(row["规格型号"]) if pd.notna(row["规格型号"]) else "",
                              unit=str(row["单位"]) if pd.notna(row["单位"]) else "个",
                              price=price, **config)
                db.add(goods)
            audit_entries.append((goods, before))
            seen.add(code)
        except (TypeError, ValueError) as exc:
            errors.append(f"第{index+2}行：{exc}")
    try:
        db.flush()
        for goods, before in audit_entries:
            _audit_alert_change(db, goods, current_user, before)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="导入冲突，请刷新当前仓库货物后重试")
    return {"success_count": len(audit_entries), "error_count": len(errors), "errors": errors}
