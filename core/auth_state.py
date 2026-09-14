"""登录失败计数与账号锁定状态（存于 config 表）。"""

from fastapi import HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import json

from core.config import MAX_LOGIN_FAILURES, LOGIN_LOCK_MINUTES, LOGIN_FAILURE_WINDOW_MINUTES
from core.models import Config

# ===== 原 main.py 块: 登录失败状态 =====
def _login_fail_key(username: str) -> str:
    return f"login_fail:{(username or '').strip().lower()}"

def _parse_lock_time(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None

def get_login_fail_state(db: Session, username: str) -> Dict[str, Any]:
    key = _login_fail_key(username)
    record = db.query(Config).filter(Config.key == key).first()
    if not record or not record.value:
        return {"count": 0, "locked_until": None, "window_start": None}
    try:
        payload = json.loads(record.value)
        return {
            "count": int(payload.get("count", 0)),
            "locked_until": _parse_lock_time(payload.get("locked_until", "")),
            "window_start": _parse_lock_time(payload.get("window_start", ""))
        }
    except Exception:
        return {"count": 0, "locked_until": None, "window_start": None}

def save_login_fail_state(
    db: Session,
    username: str,
    count: int,
    locked_until: Optional[datetime],
    window_start: Optional[datetime]
):
    key = _login_fail_key(username)
    payload = {
        "count": max(0, int(count)),
        "locked_until": locked_until.isoformat() if locked_until else "",
        "window_start": window_start.isoformat() if window_start else ""
    }
    record = db.query(Config).filter(Config.key == key).first()
    if record:
        record.value = json.dumps(payload, ensure_ascii=False)
        record.update_time = datetime.now()
    else:
        record = Config(
            key=key,
            value=json.dumps(payload, ensure_ascii=False),
            description="登录失败计数与锁定状态"
        )
        db.add(record)
    db.commit()

def clear_login_fail_state(db: Session, username: str):
    key = _login_fail_key(username)
    record = db.query(Config).filter(Config.key == key).first()
    if not record:
        return
    db.delete(record)
    db.commit()

def ensure_not_locked(db: Session, username: str):
    state = get_login_fail_state(db, username)
    locked_until = state.get("locked_until")
    window_start = state.get("window_start")
    now = datetime.utcnow()
    if locked_until and locked_until > now:
        raise HTTPException(
            status_code=423,
            detail=f"密码错误次数过多，账号已锁定，请于 {locked_until.strftime('%Y-%m-%d %H:%M:%S')} 后重试"
        )
    if locked_until and locked_until <= now:
        clear_login_fail_state(db, username)
        return

    if window_start and (now - window_start) > timedelta(minutes=LOGIN_FAILURE_WINDOW_MINUTES):
        clear_login_fail_state(db, username)

def handle_login_failure(db: Session, username: str):
    state = get_login_fail_state(db, username)
    now = datetime.utcnow()
    window_start = state.get("window_start")
    if not window_start or (now - window_start) > timedelta(minutes=LOGIN_FAILURE_WINDOW_MINUTES):
        count = 1
        window_start = now
    else:
        count = int(state.get("count", 0)) + 1

    locked_until = None
    if count >= MAX_LOGIN_FAILURES:
        locked_until = now + timedelta(minutes=LOGIN_LOCK_MINUTES)
        count = 0
        window_start = None
    save_login_fail_state(db, username, count, locked_until, window_start)
    if locked_until:
        raise HTTPException(
            status_code=423,
            detail=f"密码错误次数过多，账号已锁定，请于 {locked_until.strftime('%Y-%m-%d %H:%M:%S')} 后重试"
        )
    raise HTTPException(status_code=401, detail="用户名或密码错误")
