"""备件申请邮件提醒。

规则（2026-11 需求）：
- 新申请提交：通知申请仓库的**仓库主管**（role=supervisor 且授权该仓）的 AD 邮箱；
  若该仓没有可解析邮箱的主管，回退通知该仓的管理员（role=admin 且授权该仓），
  再回退全部启用的管理员，避免提醒丢失（实施时记录日志）。
- 审批通过/驳回：优先通知申请人填写的联系邮箱（必须为合法邮箱）；申请人未留邮箱时
  通知该仓主管/管理员。

执行方式（重要）：邮件内容与收件人**在请求内**用当前数据库会话准备好（prepare_*），
后台任务只做 LDAP 查邮箱与 SMTP 发送（不含任何数据库访问）。这样避免在 SQLite
单连接（StaticPool）环境下，后台任务另开会话与请求会话共用同一连接导致游标错乱
（CI 曾出现 RuntimeError: number of values in row ... differ from column processors）。

邮件失败绝不向业务接口抛错：所有异常在后台任务内记录日志。
"""

import logging
import re
from typing import Dict, List, Optional

from core import mail
from core.ldap import load_ldap_config_from_db, ldap_lookup_email
from core.models import Request, RequestItem, User, UserRole, UserWarehouse, Warehouse

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$", re.ASCII)
STATUS_TEXT = {"approved": "已通过", "rejected": "已驳回"}


def _warehouse_name(db, warehouse_id: Optional[int]) -> str:
    if not warehouse_id:
        return "未指定仓库"
    wh = db.query(Warehouse).filter(Warehouse.id == warehouse_id).first()
    return wh.name if wh else f"仓库#{warehouse_id}"


def _reference(req: Request) -> str:
    ts = req.create_time
    date_part = ts.strftime("%Y%m%d") if ts else "00000000"
    return f"APP-{date_part}-{req.id:04d}"


def _recipient_groups(db, warehouse_id: Optional[int]) -> List[List[str]]:
    """按该仓主管、该仓管理员、其余管理员的顺序收集 LDAP 登录名。"""
    if not warehouse_id:
        return []
    base = db.query(User).join(UserWarehouse, UserWarehouse.user_id == User.id).filter(
        UserWarehouse.warehouse_id == warehouse_id, User.is_active.is_(True))

    def names(query):
        return [u.username for u in query.all() if u.is_ldap_user]

    supervisors = names(base.filter(User.role == UserRole.SUPERVISOR))
    warehouse_admins = names(base.filter(User.role == UserRole.ADMIN))
    all_admins = names(db.query(User).filter(User.role == UserRole.ADMIN, User.is_active.is_(True)))
    warehouse_admin_names = set(warehouse_admins)
    other_admins = [name for name in all_admins if name not in warehouse_admin_names]
    return [supervisors, warehouse_admins, other_admins]


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


def prepare_request_created(db, request_id: int) -> Optional[Dict]:
    """在请求内准备"新申请"邮件（按优先级准备候选账号，后台再解析 AD 邮箱）。"""
    req = db.query(Request).filter(Request.id == request_id).first()
    if req is None:
        return None
    recipient_groups = _recipient_groups(db, req.warehouse_id)
    if not any(recipient_groups):
        logging.info("申请 %s 无可通知的仓库主管/管理员 LDAP 账号，跳过邮件", request_id)
        return None
    return {
        "recipient_groups": recipient_groups,
        "subject": f"【备品备件】新申请 {_reference(req)}（{_warehouse_name(db, req.warehouse_id)}）",
        "body": _base_body(db, req),
    }


def prepare_request_status(db, request_id: int, status: str, handler: str = "") -> Optional[Dict]:
    """在请求内准备"审批状态"邮件：优先申请人邮箱，否则该仓主管/管理员。"""
    req = db.query(Request).filter(Request.id == request_id).first()
    if req is None:
        return None
    status_text = STATUS_TEXT.get(status, status)
    contact = (req.contact or "").strip()
    payload: Dict = {
        "subject": f"【备品备件】申请 {_reference(req)} {status_text}",
        "body": "\n".join([
            f"您的备件申请{status_text}。",
            "",
            _base_body(db, req),
            f"处理人：{handler or req.handler_name or '-'}",
            f"处理时间：{req.handle_time:%Y-%m-%d %H:%M:%S}" if req.handle_time else "",
        ]),
    }
    if EMAIL_RE.match(contact):
        payload["addresses"] = [contact]
    else:
        recipient_groups = _recipient_groups(db, req.warehouse_id)
        if not any(recipient_groups):
            logging.info("申请 %s 无申请人邮箱且无主管/管理员 LDAP 账号，跳过邮件", request_id)
            return None
        payload["recipient_groups"] = recipient_groups
    return payload


def deliver_email(payload: Optional[Dict]) -> None:
    """后台任务：解析 AD 邮箱并发送（不访问数据库；异常只记日志）。"""
    if not payload:
        return
    try:
        recipients: List[str] = list(payload.get("addresses") or [])
        if not recipients:
            for priority, usernames in enumerate(payload.get("recipient_groups") or []):
                for username in usernames:
                    email = (ldap_lookup_email(username) or "").strip()
                    if EMAIL_RE.fullmatch(email) and email not in recipients:
                        recipients.append(email)
                if recipients:
                    if priority:
                        logging.info("邮件提醒回退至管理员（优先级 %s，%s）", priority, payload.get("subject"))
                    break
        if not recipients:
            logging.info("邮件提醒跳过：无可解析的收件人邮箱（%s）", payload.get("subject"))
            return
        ok, err = mail.send_mail(recipients, payload.get("subject", ""), payload.get("body", ""))
        if not ok:
            logging.warning("邮件提醒未发送（%s）：%s", payload.get("subject"), err)
    except Exception:
        logging.exception("邮件提醒发送失败（%s）", payload.get("subject"))


def queue_request_created(db, background_tasks, request_id: int) -> None:
    """请求内调用：准备内容并排入后台发送（任何异常不影响业务）。"""
    try:
        load_ldap_config_from_db(db)
        mail.load_smtp_config_from_db(db)
        payload = prepare_request_created(db, request_id)
        if payload:
            background_tasks.add_task(deliver_email, payload)
    except Exception:
        logging.exception("新申请邮件提醒准备失败（request_id=%s）", request_id)


def queue_request_status(db, background_tasks, request_id: int, status: str, handler: str = "") -> None:
    try:
        load_ldap_config_from_db(db)
        mail.load_smtp_config_from_db(db)
        payload = prepare_request_status(db, request_id, status, handler)
        if payload:
            background_tasks.add_task(deliver_email, payload)
    except Exception:
        logging.exception("审批状态邮件提醒准备失败（request_id=%s）", request_id)
