"""申请单路由：免登录公共提交（内网）+ 管理端查看/处理 + 定期归档。

安全与限流说明：
- 提交接口不做登录校验（内网使用），按来源 IP 做滑动窗口限流（默认 10 次/10 分钟）；
- 所有字段做长度校验与首尾去空白；前端渲染统一走 escapeHTML；
- v1 不提供二进制附件上传（免登录上传端点存在被恶意占用磁盘/存储的风险），
  以"附件说明"文本字段代替（写清文件名与交付方式）。

归档说明：
- 超过阈值天数（config 表 request_archive_days，默认 30 天）的申请单移入
  requests_archive 表（保留全部字段 + 归档批次），原表删除对应行；
- 触发方式：应用内后台任务定期自检（见 main.py），也可由管理员手动触发
  POST /api/requests/archive-now；
- 上次归档时间记录在 config 表 request_archive_last_run，重启不丢失。
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request as FastAPIRequest
from sqlalchemy.orm import Session

from core.models import Config, Request, RequestArchive, RequestStatus, User, UserRole
from core.schemas import (
    RequestArchiveResponse,
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

# ------------------- 申请类别（前端下拉与后端一致，增改需同步） -------------------
REQUEST_CATEGORIES = ["出入库申请", "设备/工具", "账号权限", "场地/空间", "其他"]

@router.get("/requests/categories", summary="申请类别列表（免登录）")
def get_request_categories():
    return REQUEST_CATEGORIES

@router.post("/requests/", status_code=201, summary="提交申请（免登录）")
def submit_request(
    payload: RequestSubmit,
    request: FastAPIRequest,
    db: Session = Depends(get_db)
):
    # 内网公共提交：限流 + 字段校验（Pydantic），不要求登录
    _check_rate_limit(_client_ip(request))

    new_request = Request(
        applicant_name=payload.applicant_name.strip(),
        department=(payload.department or "").strip() or None,
        contact=payload.contact.strip(),
        category=payload.category.strip(),
        description=payload.description.strip(),
        attachment_note=(payload.attachment_note or "").strip() or None,
        status=RequestStatus.PENDING.value,
    )
    db.add(new_request)
    db.commit()
    db.refresh(new_request)

    reference = f"APP-{new_request.create_time:%Y%m%d}-{new_request.id:04d}"
    logging.info("收到公共申请 %s（申请人：%s，类别：%s）", reference, new_request.applicant_name, new_request.category)
    return {
        "id": new_request.id,
        "reference": reference,
        "message": "申请已提交，请保存申请编号以便后续查询",
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

def archive_old_requests(db: Session) -> int:
    """把超过阈值天数的申请单移入归档表；返回归档条数。单事务，失败整体回滚。

    注意：0 条可归档时不写任何数据（包括 last_run 标记），
    保持"空库/无变化"语义，避免在测试库留下状态行。
    """
    cutoff = datetime.now() - timedelta(days=get_archive_days(db))
    batch = datetime.now().strftime("%Y-%m")
    now = datetime.now()

    rows = db.query(Request).filter(Request.create_time < cutoff).order_by(Request.id).all()
    if not rows:
        return 0
    for r in rows:
        db.add(RequestArchive(
            original_id=r.id,
            applicant_name=r.applicant_name,
            department=r.department,
            contact=r.contact,
            category=r.category,
            description=r.description,
            attachment_note=r.attachment_note,
            status=r.status,
            handler_name=r.handler_name,
            handle_time=r.handle_time,
            create_time=r.create_time,
            update_time=r.update_time,
            archived_at=now,
            archive_batch=batch,
        ))
    db.query(Request).filter(Request.create_time < cutoff).delete(synchronize_session=False)
    _set_archive_last_run(db)
    db.commit()
    logging.info("申请单归档完成：%d 条（阈值 %d 天，批次 %s）", len(rows), get_archive_days(db), batch)
    return len(rows)

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

@router.get("/requests/", response_model=List[RequestResponse], summary="近期申请单列表（管理员）")
def list_requests(
    status: Optional[str] = None,
    category: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _require_admin(current_user)
    query = db.query(Request).order_by(Request.create_time.desc(), Request.id.desc())
    if status:
        query = query.filter(Request.status == status)
    if category:
        query = query.filter(Request.category == category)
    return query.all()

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

    req = db.query(Request).filter(Request.id == id).first()
    if req is None:
        raise HTTPException(status_code=404, detail="申请单不存在（可能已归档，请到归档列表查看）")

    req.status = payload.status
    req.handler_name = current_user.username
    req.handle_time = datetime.now()
    db.commit()
    db.refresh(req)
    logging.info("申请单 %s 处理为 %s（处理人：%s）", req.id, payload.status, current_user.username)
    return req

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
    return query.offset((page - 1) * page_size).limit(page_size).all()

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
