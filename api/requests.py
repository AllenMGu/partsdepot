"""申请单路由：免登录公共提交（内网）+ 管理端查看/处理 + 定期归档。

安全与限流说明：
- 提交接口不做登录校验（内网使用），按来源 IP 做滑动窗口限流（默认 10 次/10 分钟）；
- 所有字段做长度校验与首尾去空白；前端渲染统一走 escapeHTML；
- v1 不提供二进制附件上传（免登录上传端点存在被恶意占用磁盘/存储的风险），
  以"备注"文本字段代替（写清文件名与交付方式）。
- 相关货物支持多行（request_items 表）：每行 条码+数量，名称/规格/单位由后端
  按条码查库快照；申请类别已停用（列保留兼容，不再必填/校验）。

归档说明：
- 超过阈值天数（config 表 request_archive_days，默认 30 天）且已处理
  （approved/rejected）的申请单移入 requests_archive 表（保留全部字段 + 归档批次），
  原表删除对应行；未处理（pending）的申请保留在"近期申请"，由管理员处理后再归档；
- 触发方式：应用内后台任务定期自检（见 main.py），也可由管理员手动触发
  POST /api/requests/archive-now；
- 并发安全：PostgreSQL 事务级咨询锁串行化并发归档；requests_archive.original_id
  唯一约束兜底（唯一冲突 → 回滚重试，幂等）；
- 上次归档时间记录在 config 表 request_archive_last_run，重启不丢失。
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request as FastAPIRequest
from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.models import (
    Config, Goods, InventoryRecord, InventoryType, Request, RequestArchive,
    RequestItem, RequestItemArchive, RequestStatus, Stock, User, UserRole, Warehouse,
    OutboundOrderHeader, OutboundOrderItem,
)
from core.order_utils import generate_order_no, lock_stock_rows_for_goods
from core.schemas import (
    PublicStockLookup,
    RequestArchiveResponse,
    RequestItemResponse,
    RequestResponse,
    RequestStatusUpdate,
    RequestSubmit,
)
from core.security import get_current_user
from core.deps import get_db

router = APIRouter()

# ------------------- 公共提交限流（滑动窗口：按 IP + 全局兜底） -------------------
RATE_WINDOW_SECONDS = 10 * 60   # 10 分钟窗口
RATE_MAX_PER_IP = 10            # 单 IP 窗口内最多 10 次
RATE_MAX_GLOBAL = 60            # 全局窗口内最多 60 次（防伪造 XFF 头绕过单 IP 限制）
_rate_lock = threading.Lock()
_rate_hits: dict = {}           # key(ip或__global__) -> [monotonic 时间戳]

def _client_ip(request: FastAPIRequest) -> str:
    """来源 IP：优先 nginx 设置的 X-Real-IP（= $remote_addr，客户端无法伪造），
    其次 X-Forwarded-For 最后一个值（nginx $proxy_add_x_forwarded_for 追加的真实 IP），
    最后回退到 TCP 对端地址（直连场景）。"""
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"

def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    cutoff = now - RATE_WINDOW_SECONDS
    with _rate_lock:
        global_hits = [t for t in _rate_hits.get("__global__", []) if t > cutoff]
        ip_hits = [t for t in _rate_hits.get(ip, []) if t > cutoff]
        if len(ip_hits) >= RATE_MAX_PER_IP:
            raise HTTPException(
                status_code=429,
                detail=f"提交过于频繁，请稍后再试（限制：{RATE_WINDOW_SECONDS // 60} 分钟内最多 {RATE_MAX_PER_IP} 次）"
            )
        if len(global_hits) >= RATE_MAX_GLOBAL:
            raise HTTPException(status_code=429, detail="当前提交人数较多，请稍后再试")
        global_hits.append(now)
        ip_hits.append(now)
        _rate_hits["__global__"] = global_hits
        _rate_hits[ip] = ip_hits

# ------------------- 公开货物搜索限流（独立桶，比提交更宽松，但仍防枚举滥用） -------------------
SEARCH_RATE_WINDOW_SECONDS = 60      # 搜索专用 1 分钟窗口（搜索是键入即查的交互，不宜用 10 分钟窗口）
SEARCH_RATE_MAX_PER_IP = 30          # 单 IP 1 分钟内最多 30 次搜索
SEARCH_RATE_MAX_GLOBAL = 120         # 全局 1 分钟内最多 120 次

def _check_search_rate_limit(ip: str, cost: int = 1) -> None:
    """搜索公共接口的加权滑动窗口限流。批量查询按条码数计费。"""
    cost = max(1, int(cost))
    now = time.monotonic()
    cutoff = now - SEARCH_RATE_WINDOW_SECONDS
    with _rate_lock:
        global_hits = [t for t in _rate_hits.get("search:__global__", []) if t > cutoff]
        ip_hits = [t for t in _rate_hits.get("search:" + ip, []) if t > cutoff]
        if len(ip_hits) + cost > SEARCH_RATE_MAX_PER_IP:
            raise HTTPException(status_code=429, detail="搜索过于频繁，请稍后再试")
        if len(global_hits) + cost > SEARCH_RATE_MAX_GLOBAL:
            raise HTTPException(status_code=429, detail="当前搜索人数较多，请稍后再试")
        global_hits.extend([now] * cost)
        ip_hits.extend([now] * cost)
        _rate_hits["search:__global__"] = global_hits
        _rate_hits["search:" + ip] = ip_hits

@router.get("/public/goods-search", summary="货物搜索（免登录，公开申请页联动用）")
def search_goods_public(
    q: str,
    request: FastAPIRequest,
    db: Session = Depends(get_db),
    warehouse_id: Optional[int] = None,
):
    """按条码/名称模糊搜索货物，供免登录申请页选择相关货物。

    安全边界：仅返回 条码/名称/规格/单位 + 可用库存等非敏感字段（不含单价等）；
    可用库存为产品需求——申请人在申请页选备件时直接看到现有库存，便于判断是否足够。
    单次最多 20 条，独立限流桶防止被用来枚举全量货物目录。

    warehouse_id（可选）：指定时可用库存**仅按该仓库汇总**——必须与审批实际扣减的
    仓库一致，否则多仓环境下页面会显示"其他仓库的库存"造成误导（例如所选仓 0 件、
    他仓 100 件时显示 100，审批却必然库存不足）。仓库不存在或已停用 → 400。
    不指定时返回全仓合计（兼容未升级的旧客户端）。
    """
    keyword = (q or "").strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="请输入货物条码或名称")
    _check_search_rate_limit(_client_ip(request))
    # 仓库过滤（v2）：申请页库存展示必须与审批扣减仓库一致
    wh = None
    if warehouse_id is not None:
        wh = db.query(Warehouse).filter(Warehouse.id == warehouse_id).first()
        if wh is None or not wh.is_active:
            raise HTTPException(status_code=400, detail=f"仓库不存在或已停用：{warehouse_id}")
    like = f"%{keyword}%"
    rows = (
        db.query(Goods.id, Goods.barcode, Goods.name, Goods.spec, Goods.unit)
        .filter(or_(Goods.barcode.like(like), Goods.name.like(like)))
        .order_by(Goods.id)
        .limit(20)
        .all()
    )
    if not rows:
        return []
    # 可用库存 = 指定仓库时该仓库（全部库位）合计；未指定时为全仓合计（无库存行视为 0）
    stock_q = (
        db.query(Goods.id, func.coalesce(func.sum(Stock.quantity), 0))
        .join(Stock, Stock.goods_id == Goods.id)
        .filter(Goods.id.in_([r[0] for r in rows]))
    )
    if wh is not None:
        stock_q = stock_q.filter(Stock.warehouse_id == wh.id)
    stock_map = dict(stock_q.group_by(Goods.id).all())
    return [
        {
            "barcode": b,
            "name": n or "",
            "spec": s or "",
            "unit": u or "",
            "available_stock": float(stock_map.get(gid) or 0),
        }
        for (gid, b, n, s, u) in rows
    ]

@router.post("/public/stock-lookup", summary="批量按仓可用库存查询（免登录，申请页切仓批量刷新用）")
def stock_lookup_public(
    payload: PublicStockLookup,
    request: FastAPIRequest,
    db: Session = Depends(get_db)
):
    """一次调用批量返回多条码在指定仓库的可用库存（评审 P2 跟进）。

    背景：申请页切换仓库时，旧实现按已选行数逐条调用 goods-search 刷新库存，
    行数超过 30 时部分请求会撞搜索限流（30 次/分钟）返回 429，页面残留旧仓库存
    误导申请人。前端改为一次批量查询，并按实际查询的唯一条码数量计算限流成本。

    契约：
    - JSON 请求体包含 warehouse_id + barcodes；仅按指定仓库汇总。
    - barcodes 为 1~200 条、每条 ≤100 字符；使用 POST 避免批量参数生成超长 URL。
    - 响应按输入顺序去重返回；条码不存在或该仓无库存 → available_stock=0。
    - 与 goods-search 共用搜索限流桶；每 10 个唯一条码消耗 1 个配额。
    - 安全边界：仅返回 条码 + 可用库存 非敏感字段（不含名称/单价等）。
    """
    codes = payload.barcodes
    seen = set()
    unique_codes = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            unique_codes.append(c)
    _check_search_rate_limit(_client_ip(request), cost=(len(unique_codes) + 9) // 10)
    wh = db.query(Warehouse).filter(Warehouse.id == payload.warehouse_id).first()
    if wh is None or not wh.is_active:
        raise HTTPException(status_code=400, detail=f"仓库不存在或已停用：{payload.warehouse_id}")
    goods_rows = db.query(Goods.id, Goods.barcode).filter(Goods.barcode.in_(unique_codes)).all()
    id_by_code = {bc: gid for (gid, bc) in goods_rows}
    gids = [id_by_code[c] for c in unique_codes if c in id_by_code]
    stock_map = {}
    if gids:
        stock_map = dict(
            db.query(Goods.id, func.coalesce(func.sum(Stock.quantity), 0))
            .join(Stock, Stock.goods_id == Goods.id)
            .filter(Goods.id.in_(gids), Stock.warehouse_id == wh.id)
            .group_by(Goods.id)
            .all()
        )
    return [
        {
            "barcode": c,
            "available_stock": float(stock_map.get(id_by_code[c]) or 0) if c in id_by_code else 0,
        }
        for c in unique_codes
    ]

@router.get("/public/warehouses", summary="启用仓库列表（免登录，公开申请页仓库选择用）")
def list_warehouses_public(
    request: FastAPIRequest,
    db: Session = Depends(get_db)
):
    """返回启用仓库的 id/code/name，供免登录申请页选择"申请仓库"。

    安全边界：仅暴露 id/code/name 非敏感字段；与货物搜索共用独立限流桶，防止枚举滥用。
    """
    _check_search_rate_limit(_client_ip(request))
    rows = (
        db.query(Warehouse.id, Warehouse.code, Warehouse.name)
        .filter(Warehouse.is_active.is_(True))
        .order_by(Warehouse.id)
        .all()
    )
    return [{"id": wid, "code": code or "", "name": name or ""} for (wid, code, name) in rows]

@router.post("/requests/", status_code=201, summary="提交申请（免登录）")
def submit_request(
    payload: RequestSubmit,
    request: FastAPIRequest,
    db: Session = Depends(get_db)
):
    # 内网公共提交：限流 + 字段校验（Pydantic），不要求登录
    _check_rate_limit(_client_ip(request))

    # 申请仓库（可选）：指定时必须为启用中的仓库（防写入失效 ID）；
    # 不指定时允许提交（审批时按"唯一启用仓库"兜底或拒绝，见 _apply_approval）
    if payload.warehouse_id is not None:
        wh = db.query(Warehouse).filter(Warehouse.id == payload.warehouse_id).first()
        if not wh or not wh.is_active:
            raise HTTPException(status_code=422, detail=f"申请仓库不存在或已停用：{payload.warehouse_id}")

    # 相关货物（可选，可多行，快照存储）：每行只接受 条码+数量；名称/规格/单位一律由
    # 后端按条码精确查库填充（不信任客户端快照字段）；条码必须真实存在
    items_in = payload.items or []
    if len(items_in) > 200:
        raise HTTPException(status_code=422, detail="相关货物最多 200 行")
    resolved_items = []
    for idx, it in enumerate(items_in, start=1):
        barcode = (it.barcode or "").strip()
        if not barcode:
            raise HTTPException(status_code=422, detail=f"第 {idx} 行货物缺少条码")
        row = db.query(Goods).filter(Goods.barcode == barcode).order_by(Goods.id).first()
        if not row:
            raise HTTPException(status_code=422, detail=f"第 {idx} 行货物不存在：{barcode}（请确认条码有效）")
        resolved_items.append((barcode, row.name, row.spec, row.unit, it.quantity))

    new_request = Request(
        applicant_name=payload.applicant_name.strip(),
        department=(payload.department or "").strip() or None,
        contact=payload.contact.strip(),
        category=(payload.category or "").strip() or None,
        description=payload.description.strip(),
        attachment_note=(payload.attachment_note or "").strip() or None,
        warehouse_id=payload.warehouse_id,
        status=RequestStatus.PENDING.value,
    )
    db.add(new_request)
    db.flush()  # 取得 id 后挂明细
    for sort, (barcode, name, spec, unit, quantity) in enumerate(resolved_items):
        db.add(RequestItem(
            request_id=new_request.id,
            sort=sort,
            barcode=barcode,
            name=name or None,
            spec=spec or None,
            unit=unit or None,
            quantity=quantity,
        ))
    db.commit()
    db.refresh(new_request)

    reference = f"APP-{new_request.create_time:%Y%m%d}-{new_request.id:04d}"
    logging.info("收到公共申请 %s（申请人：%s，货物 %d 行）", reference, new_request.applicant_name, len(resolved_items))
    return {
        "id": new_request.id,
        "reference": reference,
        "message": "申请已提交。请保存申请编号，处理进展请联系受理管理员跟进（当前系统暂不提供免登录查询）",
    }

# ------------------- 归档逻辑（手动/自动共用） -------------------
ARCHIVE_DAYS_KEY = "request_archive_days"
ARCHIVE_LAST_RUN_KEY = "request_archive_last_run"
DEFAULT_ARCHIVE_DAYS = 30

def get_archive_days(db: Session) -> int:
    """归档阈值天数（config 表可覆盖，默认 30 天）。"""
    cfg = db.query(Config).filter(Config.key == ARCHIVE_DAYS_KEY).first()
    if cfg and cfg.value:
        try:
            return max(1, int(cfg.value))
        except ValueError:
            pass
    return DEFAULT_ARCHIVE_DAYS

def _set_archive_last_run(db: Session) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cfg = db.query(Config).filter(Config.key == ARCHIVE_LAST_RUN_KEY).first()
    if cfg:
        cfg.value = ts
    else:
        db.add(Config(
            key=ARCHIVE_LAST_RUN_KEY,
            value=ts,
            description="申请单最近一次归档时间（自动写入，勿手工修改）"
        ))

def get_archive_last_run(db: Session) -> Optional[datetime]:
    cfg = db.query(Config).filter(Config.key == ARCHIVE_LAST_RUN_KEY).first()
    if cfg and cfg.value:
        try:
            return datetime.strptime(cfg.value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return None

def _archive_once(db: Session, cutoff: datetime, batch: str, now: datetime) -> int:
    """单次归档尝试（单事务）。

    归档策略：仅归档已处理（approved/rejected）的申请单；
    pending 保留在"近期申请"，由管理员处理后再归档，避免漏处理申请从待办消失。
    0 条可归档时不写任何数据（包括 last_run 标记），保持"空库/无变化"语义。
    """
    rows = (
        db.query(Request)
        .filter(
            Request.create_time < cutoff,
            Request.status.in_([RequestStatus.APPROVED.value, RequestStatus.REJECTED.value]),
        )
        .order_by(Request.id)
        .all()
    )
    if not rows:
        return 0
    for r in rows:
        arc = RequestArchive(
            original_id=r.id,
            applicant_name=r.applicant_name,
            department=r.department,
            contact=r.contact,
            category=r.category,
            description=r.description,
            attachment_note=r.attachment_note,
            warehouse_id=r.warehouse_id,
            status=r.status,
            handler_name=r.handler_name,
            handle_time=r.handle_time,
            outbound_order_no=r.outbound_order_no,
            create_time=r.create_time,
            update_time=r.update_time,
            archived_at=now,
            archive_batch=batch,
        )
        db.add(arc)
        db.flush()  # 取归档单 id，挂货物明细
        items = (
            db.query(RequestItem)
            .filter(RequestItem.request_id == r.id)
            .order_by(RequestItem.sort, RequestItem.id)
            .all()
        )
        for it in items:
            db.add(RequestItemArchive(
                archive_id=arc.id,
                original_request_id=r.id,
                sort=it.sort,
                barcode=it.barcode,
                name=it.name,
                spec=it.spec,
                unit=it.unit,
                quantity=it.quantity,
            ))
    # 只删除本次实际归档的行（按 id 精确删除，避免误伤并发新写入）。
    # 先删活跃明细再删主表（评审 P1）：明细已复制到 request_items_archive，
    # 显式删除保证不留孤儿行，不依赖数据库级联（SQLite 默认不启用外键）
    _ids = [r.id for r in rows]
    db.query(RequestItem).filter(RequestItem.request_id.in_(_ids)).delete(synchronize_session=False)
    db.query(Request).filter(Request.id.in_(_ids)).delete(synchronize_session=False)
    _set_archive_last_run(db)
    db.commit()
    logging.info("申请单归档完成：%d 条（阈值 %d 天，批次 %s）", len(rows), get_archive_days(db), batch)
    return len(rows)

def archive_old_requests(db: Session) -> int:
    """把超过阈值天数且已处理的申请单移入归档表；返回归档条数。

    并发安全（评审 P1）：
    - PostgreSQL：事务级咨询锁 pg_advisory_xact_lock 串行化并发归档
      （后台任务 vs 手动触发、多 worker 互斥），随事务提交/回滚自动释放；
    - requests_archive.original_id 唯一约束兜底：并发事务若抢先提交，
      本事务提交时唯一冲突 → 回滚并重试（幂等收敛，不产生重复归档）；
    - SQLite（测试/单连接）天然串行，唯一约束同样生效。
    """
    cutoff = datetime.now() - timedelta(days=get_archive_days(db))
    batch = datetime.now().strftime("%Y-%m")
    now = datetime.now()

    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(20260915)"))

    for _attempt in range(3):
        try:
            return _archive_once(db, cutoff, batch, now)
        except IntegrityError:
            # 并发事务抢先归档了同一批申请单（original_id 唯一约束冲突）：
            # 回滚后重查——对方已删原表行，重试将 0 条或仅归档剩余行
            db.rollback()
            logging.warning("申请单归档遇到并发冲突，回滚重试")
    # 理论上 3 次内必收敛（对方事务提交后行已不可见）；兜底返回 0，原表数据不丢失，
    # 由下一次自检/手动触发再归档
    logging.error("申请单归档重试 3 次仍冲突，本次放弃（原表数据未动）")
    return 0

def archive_due(db: Session) -> int:
    """判断是否到了归档期（从未归档过，或距上次归档已超过阈值天数），到点则执行。"""
    last_run = get_archive_last_run(db)
    due = last_run is None or (datetime.now() - last_run) >= timedelta(days=get_archive_days(db))
    if not due:
        return 0
    return archive_old_requests(db)

# ------------------- 管理端接口（需登录，仅限管理员） -------------------
def _require_admin(current_user: User) -> None:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="申请单管理仅限管理员操作")

def _warehouse_name_map(db: Session, warehouse_ids: list) -> dict:
    """仓库 id → 名称 映射（仅查出现过的 id，避免无关查询）。"""
    ids = {wid for wid in warehouse_ids if wid is not None}
    if not ids:
        return {}
    return dict(db.query(Warehouse.id, Warehouse.name).filter(Warehouse.id.in_(ids)).all())

def _request_payload(r: Request, items: list, warehouse_name: Optional[str] = None) -> dict:
    return {
        "id": r.id,
        "applicant_name": r.applicant_name,
        "department": r.department,
        "contact": r.contact,
        "category": r.category,
        "description": r.description,
        "attachment_note": r.attachment_note,
        "warehouse_id": r.warehouse_id,
        "warehouse_name": warehouse_name,
        "items": items,
        "status": r.status,
        "handler_name": r.handler_name,
        "handle_time": r.handle_time,
        "outbound_order_no": r.outbound_order_no,
        "create_time": r.create_time,
        "update_time": r.update_time,
    }

def _request_payload_archive(a: RequestArchive, items: list, warehouse_name: Optional[str] = None) -> dict:
    return {
        "id": a.id,
        "original_id": a.original_id,
        "applicant_name": a.applicant_name,
        "department": a.department,
        "contact": a.contact,
        "category": a.category,
        "description": a.description,
        "attachment_note": a.attachment_note,
        "warehouse_id": a.warehouse_id,
        "warehouse_name": warehouse_name,
        "items": items,
        "status": a.status,
        "handler_name": a.handler_name,
        "handle_time": a.handle_time,
        "outbound_order_no": a.outbound_order_no,
        "create_time": a.create_time,
        "update_time": a.update_time,
        "archived_at": a.archived_at,
        "archive_batch": a.archive_batch,
    }

def _group_items(db: Session, ids: list) -> dict:
    """按父单 id 归组货物明细（保持行序）。"""
    if not ids:
        return {}
    rows = (
        db.query(RequestItem)
        .filter(RequestItem.request_id.in_(ids))
        .order_by(RequestItem.sort, RequestItem.id)
        .all()
    )
    grouped = {}
    for it in rows:
        grouped.setdefault(it.request_id, []).append(it)
    return grouped

def _group_archive_items(db: Session, archive_ids: list) -> dict:
    if not archive_ids:
        return {}
    rows = (
        db.query(RequestItemArchive)
        .filter(RequestItemArchive.archive_id.in_(archive_ids))
        .order_by(RequestItemArchive.sort, RequestItemArchive.id)
        .all()
    )
    grouped = {}
    for it in rows:
        grouped.setdefault(it.archive_id, []).append(it)
    return grouped

@router.get("/requests/", response_model=List[RequestResponse], summary="近期申请单列表（管理员）")
def list_requests(
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _require_admin(current_user)
    query = db.query(Request).order_by(Request.create_time.desc(), Request.id.desc())
    if status:
        query = query.filter(Request.status == status)
    reqs = query.all()
    grouped = _group_items(db, [r.id for r in reqs])
    wmap = _warehouse_name_map(db, [r.warehouse_id for r in reqs])
    return [_request_payload(r, grouped.get(r.id, []), wmap.get(r.warehouse_id)) for r in reqs]

def _apply_approval(db: Session, req: Request, current_user: User) -> None:
    """通过申请单 = 从申请仓库扣减库存（与状态变更同事务；任一步失败整体回滚）。

    规则：
    - 无货物明细的申请单：通过仅留痕，不动库存；
    - 仓库解析：优先申请单指定的 warehouse_id；历史数据未指定时，系统恰好一个启用仓库
      则默认该仓；多仓且未指定 → 拒绝（无法确定扣哪个仓，要求重新提交）；
    - 同一货物多行明细汇总为需求数量；
    - 库存行按行 id 顺序（库位先后）逐行扣减至需求满足；
    - 库存不足 → 400，库存不变（出库单同事务创建，失败一并回滚，不留半成品单据）；
    - 每条被扣减的库存行写一条出库流水（inventory_records），出入库记录可追溯；
    - **出库单**（产品需求：确认后出库必须有出库单）：同事务生成一张 COMPLETED 状态
      的出库单（outbound_order_header/item，出库单模块既有表结构与列表页直接可见），
      单号走 generate_order_no("OUT") 既有号段（PG 咨询锁防并发撞号）；
      明细按实际扣减的 (货物,库位) 行逐条落（数量=该行实扣，单价=货物档案价）；
      申请单回填 outbound_order_no（归档时随原单保留），双向可查；
    - 并发安全：先锁申请单行（调用方），再将涉及货物的库存行统一按 **Stock.id 升序**
      FOR UPDATE；手工出库/入库/盘点使用同一顺序——全局加锁顺序确定，跨流程的
      多货、多库位反序操作不会死锁（PostgreSQL；
      SQLite 退化为普通查询，配合单 worker/写串行不超卖），与并发出库互斥；
    - 审批时再次校验仓库启用状态：提交后仓库被停用 → 400 拒绝扣减。
    """
    items = (
        db.query(RequestItem)
        .filter(RequestItem.request_id == req.id)
        .order_by(RequestItem.sort, RequestItem.id)
        .all()
    )
    if not items:
        return  # 无明细：通过仅留痕

    # 需求数量（同一货物多行汇总；提交时条码已校验存在，此处按条码回查货物）
    required: dict = {}
    names: dict = {}
    gprice: dict = {}
    for it in items:
        g = db.query(Goods).filter(Goods.barcode == it.barcode).order_by(Goods.id).first()
        if g is None:
            raise HTTPException(status_code=400, detail=f"货物 {it.barcode} 已不存在，无法扣减库存（请联系管理员处理）")
        required[g.id] = required.get(g.id, 0.0) + (it.quantity or 0)
        names[g.id] = g.name or it.name or it.barcode
        gprice[g.id] = g.price

    # 仓库解析
    warehouse_id = req.warehouse_id
    if warehouse_id is None:
        active = (
            db.query(Warehouse).filter(Warehouse.is_active.is_(True)).order_by(Warehouse.id).all()
        )
        if len(active) == 1:
            warehouse_id = active[0].id
        elif not active:
            raise HTTPException(status_code=400, detail="系统没有启用中的仓库，无法扣减库存（请联系管理员）")
        else:
            raise HTTPException(status_code=400, detail="该申请单未指定申请仓库，且系统存在多个启用仓库，无法确定扣减哪个仓库（请驳回后让申请人重新提交并选择仓库）")
    wh = db.query(Warehouse).filter(Warehouse.id == warehouse_id).first()
    if wh is None:
        raise HTTPException(status_code=400, detail="该申请单的仓库已不存在，无法扣减库存（请联系管理员处理）")
    if not wh.is_active:
        raise HTTPException(
            status_code=400,
            detail=f"申请仓库 {wh.name or wh.code} 已停用，无法扣减库存（请驳回后由申请人重新提交并选择有效仓库）"
        )

    reference = f"APP-{req.create_time:%Y%m%d}-{req.id:04d}"
    # 所有出库路径统一为“库存锁 → 号段锁”：扫码出库先锁库存再生成单号，审批也必须
    # 使用相同顺序，否则两者并发时会形成“审批持号段等库存、扫码持库存等号段”的死锁。
    all_rows = lock_stock_rows_for_goods(db, warehouse_id, required.keys())
    rows_by_goods = {}
    for row in all_rows:
        rows_by_goods.setdefault(row.goods_id, []).append(row)

    # 持有库存锁后先完成全部库存校验；任何一项不足都不获取号段锁，也不修改库存。
    for goods_id, qty in sorted(required.items()):
        rows = rows_by_goods.get(goods_id, [])
        available = sum(r.quantity or 0 for r in rows)
        if available < qty:
            raise HTTPException(
                status_code=400,
                detail=f"库存不足：{names.get(goods_id, f'#{goods_id}')} 需要 {qty:g}，"
                       f"仓库 {wh.name or wh.code} 现有 {available:g}（审批已回滚，库存未变动）"
            )

    # 单号随当前事务提交/回滚；库存校验通过后再取号，锁顺序与扫码出库保持一致。
    order_no = generate_order_no("OUT", db)
    out_items: list = []  # [(goods_id, location_id, 实扣数量, 单价)]
    for goods_id, qty in sorted(required.items()):
        rows = rows_by_goods.get(goods_id, [])
        remaining = qty
        now = datetime.now()
        for r in rows:
            if remaining <= 0:
                break
            take = min(r.quantity or 0, remaining)
            if take <= 0:
                continue
            r.quantity = (r.quantity or 0) - take
            r.update_time = now
            remaining -= take
            price = gprice.get(goods_id) or 0.0
            out_items.append((goods_id, r.location_id, take, price))
            db.add(InventoryRecord(
                warehouse_id=warehouse_id,
                goods_id=goods_id,
                location_id=r.location_id,
                type=InventoryType.OUT,
                quantity=take,
                operator_id=current_user.id,
                remark=f"申请单 {reference} 通过扣减（出库单 {order_no}）",
            ))

    # 出库单（同事务）：表头 COMPLETED + 按实扣 (货物,库位) 落明细；申请单回填单号。
    # 客户=申请人（出库对象），操作员=审批人（执行出库动作的人）。
    now = datetime.now()
    total_amount = sum(qty * (price or 0.0) for _, _, qty, price in out_items)
    order = OutboundOrderHeader(
        order_no=order_no,
        warehouse_id=warehouse_id,
        customer=req.applicant_name,
        operator_id=current_user.id,
        total_amount=total_amount,
        remark=(f"申请单 {reference} 审批通过自动出库"
                f"（申请人：{req.applicant_name}；邮箱：{req.contact}）")[:500],
        status="COMPLETED",
        create_time=now,
        submit_time=now,
        complete_time=now,
    )
    db.add(order)
    db.flush()  # 取单据头 id 挂明细
    for goods_id, location_id, take, price in out_items:
        db.add(OutboundOrderItem(
            header_id=order.id,
            goods_id=goods_id,
            location_id=location_id,
            quantity=take,
            unit_price=price,
            total_price=take * (price or 0.0),
            remark=f"申请单 {reference}",
        ))
    req.outbound_order_no = order_no

@router.post("/requests/{id}/status", response_model=RequestResponse, summary="通过/驳回申请单（管理员）")
def set_request_status(
    id: int,
    payload: RequestStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _require_admin(current_user)
    if payload.status not in (RequestStatus.APPROVED.value, RequestStatus.REJECTED.value):
        raise HTTPException(status_code=400, detail="状态只能为 approved 或 rejected")

    # 行级锁串行化同一申请单的并发处理（PostgreSQL FOR UPDATE；SQLite 忽略）
    req_q = db.query(Request).filter(Request.id == id)
    try:
        req_q = req_q.with_for_update()
    except Exception:
        pass
    req = req_q.first()
    if req is None:
        raise HTTPException(status_code=404, detail="申请单不存在（可能已归档，请到归档列表查看）")

    # 状态机：仅 pending 可处理；通过/驳回均为终态，不可再次处理——
    # 通过会扣库存，允许"通过→驳回→再通过"会造成重复扣减，v2 明确禁止
    if req.status != RequestStatus.PENDING.value:
        state_text = {"approved": "已通过", "rejected": "已驳回"}.get(req.status, req.status)
        raise HTTPException(status_code=409, detail=f"该申请单已处理（{state_text}），不能再次处理")

    # 通过 = 扣减申请仓库库存（同事务；失败则状态与库存都不变）。驳回不动库存。
    if payload.status == RequestStatus.APPROVED.value:
        _apply_approval(db, req, current_user)

    req.status = payload.status
    req.handler_name = current_user.username
    req.handle_time = datetime.now()
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(req)
    logging.info("申请单 %s 处理为 %s（处理人：%s）", req.id, payload.status, current_user.username)
    # 评审一致性项：返回完整载荷（含真实货物明细 + 申请仓库名称），与列表接口契约一致，
    # 不能直接 return req（那样 items 会退化成空数组）
    grouped = _group_items(db, [req.id])
    wmap = _warehouse_name_map(db, [req.warehouse_id]) if req.warehouse_id else {}
    return _request_payload(req, grouped.get(req.id, []), wmap.get(req.warehouse_id))

@router.get("/requests/archive/", response_model=List[RequestArchiveResponse], summary="归档申请单列表（管理员）")
def list_archived_requests(
    batch: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _require_admin(current_user)
    page = max(1, page)
    page_size = min(200, max(1, page_size))
    query = db.query(RequestArchive).order_by(RequestArchive.id.desc())
    if batch:
        query = query.filter(RequestArchive.archive_batch == batch)
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    grouped = _group_archive_items(db, [a.id for a in rows])
    wmap = _warehouse_name_map(db, [a.warehouse_id for a in rows])
    return [_request_payload_archive(a, grouped.get(a.id, []), wmap.get(a.warehouse_id)) for a in rows]

@router.get("/requests/archive/meta", summary="归档元信息（管理员）")
def archive_meta(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _require_admin(current_user)
    return {
        "archive_days": get_archive_days(db),
        "last_run": get_archive_last_run(db),
        "archived_total": db.query(RequestArchive).count(),
        "active_total": db.query(Request).count(),
    }

@router.post("/requests/archive-now", summary="立即执行归档（管理员）")
def archive_now(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _require_admin(current_user)
    count = archive_old_requests(db)
    logging.info("管理员 %s 手动触发申请单归档：%d 条", current_user.username, count)
    return {"archived": count}
