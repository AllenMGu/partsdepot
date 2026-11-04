"""邮件通知配置与测试路由（仅管理员）。"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core import mail
from core.deps import get_db
from core.ldap import ldap_lookup_email
from core.models import User, UserRole
from core.security import get_current_user

router = APIRouter()


class NotifyConfigUpdate(BaseModel):
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_security: Optional[str] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None


class NotifyTestRequest(BaseModel):
    to: Optional[str] = None


def _require_admin(user: User) -> None:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")


@router.get("/notify/config", summary="获取邮件通知（SMTP）配置")
def get_notify_config(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current_user)
    return mail.get_smtp_config(db)


@router.put("/notify/config", summary="更新邮件通知（SMTP）配置")
def update_notify_config(
    payload: NotifyConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    mail.save_smtp_config(db, payload.model_dump(exclude_unset=True))
    return mail.get_smtp_config(db)


@router.post("/notify/test", summary="发送测试邮件（验证 SMTP 配置）")
def send_test_notify(
    payload: NotifyTestRequest = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    to = ((payload.to if payload else "") or "").strip()
    if not to and current_user.is_ldap_user:
        mail.load_smtp_config_from_db(db)
        to = ldap_lookup_email(current_user.username)
    if not to:
        raise HTTPException(status_code=400, detail="无法确定收件人：请传入 to，或使用已配置 AD 邮箱的 LDAP 账号")
    mail.load_smtp_config_from_db(db)
    ok, err = mail.send_mail([to], "【备品备件】邮件通知测试", "这是一封来自备品备件管理系统的测试邮件。")
    return {"sent": ok, "to": to, "detail": err}
