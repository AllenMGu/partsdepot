"""备件申请邮件提醒。

规则（2026-11 需求）：
- 新申请提交：通知申请仓库的**仓库主管**（role=supervisor 且授权该仓）的 AD 邮箱；
  若该仓没有可解析邮箱的主管，回退通知该仓的管理员（role=admin 且授权该仓），
  再回退全部启用的管理员，避免提醒丢失（实施时记录日志）。
- 审批通过/驳回：优先通知申请人填写的联系邮箱（必须为合法邮箱）；申请人未留邮箱时
  通知该仓主管/管理员。

邮件失败绝不向业务接口抛错：所有异常在后台任务内记录日志。
"""

import logging
import re
from typing import List

from core.database import SessionLocal
from core.ldap import ldap_lookup_email
from core.models import Goods, Request, RequestItem, User, UserRole, UserWarehouse, Warehouse
from core import mail

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$", re.ASCII)
STATUS_TEXT = {"approved": "已通过", "rejected": "已驳回"}


def _warehouse_name(db, warehouse_id) -> str:
    if not warehouse_id:
        return "未指定仓库"
    wh = db.query(Warehouse).filter(Warehouse.id == warehouse_id).first()
    return wh.name if wh else f"仓库#{warehouse_id}"


def _reference(req: Request) -> str:
    ts = req.create_time
    date_part = ts.strftime("%Y%m%d") if ts else "00000000"
    return f"APP-{date_part}-{req.id:04d}"


def _emails_for_users(users) -> List[str]:
    emails: List[str] = []
    for user in users:
        if not getattr(user, "is_ldap_user", False):
            continue
        email = ldap_lookup_email(user.username)
        if email and email not in emails:
            emails.append(email)
    return emails


def _warehouse_recipients(db, warehouse_id) -> List[str]:
    """该仓主管优先；无则回退该仓管理员；再回退全部启用的管理员。"""
    if not warehouse_id:
        return []
    base = db.query(User).join(UserWarehouse, UserWarehouse.user_id == User.id).filter(
        UserWarehouse.warehouse_id == warehouse_id, User.is_active.is_(True))
    supervisors = _emails_for_users(base.filter(User.role == UserRole.SUPERVISOR).all())
    if supervisors:
        return supervisors
    admins = _emails_for_users(base.filter(User.role == UserRole.ADMIN).all())
    if admins:
        return admins
    logging.info("仓库 %s 未配置主管/管理员邮箱，回退通知全部管理员", warehouse_id)
    return _emails_for_users(db.query(User).filter(
        User.role == UserRole.ADMIN, User.is_active.is_(True)).all())


def _items_text(db, req: Request) -> str:
    rows = (
        db.query(RequestItem)
        .filter(RequestItem.request_id == req.id)
        .order_by(RequestItem.sort, RequestItem.id)
        .all()
    )
    if not rows:
        return "（无货物明细）"
    lines = []
    for item in rows:
        name = item.name or ""
        spec = f"（{item.spec}）" if item.spec else ""
        qty = f"{item.quantity:g}" if item.quantity is not None else ""
        unit = item.unit or ""
        lines.append(f"- {item.barcode} {name}{spec} × {qty}{unit}")
    return "\n".join(lines)


def _base_body(db, req: Request) -> str:
    lines = [
        f"申请编号：{_reference(req)}",
        f"申请人：{req.applicant_name}",
        f"部门：{req.department or '-'}",
        f"申请仓库：{_warehouse_name(db, req.warehouse_id)}",
        f"事由：{req.description or '-'}",
        "货物明细：",
        _items_text(db, req),
    ]
    if req.attachment_note:
        lines.append(f"备注：{req.attachment_note}")
    if req.create_time:
        lines.append(f"提交时间：{req.create_time:%Y-%m-%d %H:%M:%S}")
    base_url = mail._state.get("base_url") or ""
    if base_url:
        lines.append("")
        lines.append(f"请登录系统处理：{base_url}/request-admin.html")
    return "\n".join(lines)


def _send(db, recipients: List[str], subject: str, body: str) -> None:
    if not recipients:
        logging.info("邮件提醒跳过：无可解析的收件人邮箱（%s）", subject)
        return
    mail.load_smtp_config_from_db(db)
    ok, err = mail.send_mail(recipients, subject, body)
    if not ok:
        logging.warning("邮件提醒未发送（%s）：%s", subject, err)


def notify_request_created(request_id: int) -> None:
    """新申请提交后通知该仓主管（后台任务，异常不外抛）。"""
    db = SessionLocal()
    try:
        req = db.query(Request).filter(Request.id == request_id).first()
        if req is None:
            logging.warning("邮件提醒：申请单 %s 不存在", request_id)
            return
        recipients = _warehouse_recipients(db, req.warehouse_id)
        subject = f"【备品备件】新申请 {_reference(req)}（{_warehouse_name(db, req.warehouse_id)}）"
        _send(db, recipients, subject, _base_body(db, req))
    except Exception:
        logging.exception("新申请邮件提醒失败（request_id=%s）", request_id)
    finally:
        db.close()


def notify_request_status(request_id: int, status: str, handler: str = "") -> None:
    """审批通过/驳回后通知申请人（联系邮箱）；申请人无邮箱时通知该仓主管。"""
    db = SessionLocal()
    try:
        req = db.query(Request).filter(Request.id == request_id).first()
        if req is None:
            logging.warning("邮件提醒：申请单 %s 不存在", request_id)
            return
        status_text = STATUS_TEXT.get(status, status)
        contact = (req.contact or "").strip()
        recipients = [contact] if EMAIL_RE.match(contact) else _warehouse_recipients(db, req.warehouse_id)
        subject = f"【备品备件】申请 {_reference(req)} {status_text}"
        body = "\n".join([
            f"您的备件申请{status_text}。",
            "",
            _base_body(db, req),
            f"处理人：{handler or req.handler_name or '-'}",
            f"处理时间：{req.handle_time:%Y-%m-%d %H:%M:%S}" if req.handle_time else "",
        ])
        _send(db, recipients, subject, body)
    except Exception:
        logging.exception("审批状态邮件提醒失败（request_id=%s）", request_id)
    finally:
        db.close()
