"""SMTP 邮件发送。

配置优先级与 LDAP 一致：进程环境变量 > 数据库 config 表 > 未配置（空值）。
SMTP_HOST 为空表示未启用：send_mail 直接返回失败原因，不影响业务主流程。

数据库配置项（管理员可通过 /api/notify/config 维护）：
    smtp_host / smtp_port / smtp_security(starttls|ssl|none)
    smtp_user / smtp_password / smtp_from
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Dict, List, Optional, Tuple

import core.config as app_config
from core.models import Config

_DB_KEYS = {
    "host": "smtp_host",
    "port": "smtp_port",
    "security": "smtp_security",
    "user": "smtp_user",
    "password": "smtp_password",
    "from_addr": "smtp_from",
}

# 运行时配置（load_smtp_config_from_db 刷新；send_mail 读取）
_state: Dict[str, Any] = {
    "host": app_config.SMTP_HOST,
    "port": app_config.SMTP_PORT,
    "security": app_config.SMTP_SECURITY,
    "user": app_config.SMTP_USER,
    "password": app_config.SMTP_PASSWORD,
    "from_addr": app_config.SMTP_FROM,
    "timeout": app_config.SMTP_TIMEOUT,
    "base_url": app_config.PUBLIC_BASE_URL,
}

_ENV_NAMES = {
    "host": "SMTP_HOST",
    "port": "SMTP_PORT",
    "security": "SMTP_SECURITY",
    "user": "SMTP_USER",
    "password": "SMTP_PASSWORD",
    "from_addr": "SMTP_FROM",
}


def load_smtp_config_from_db(db) -> Dict[str, Any]:
    """重新加载 SMTP 配置（环境变量优先，其次数据库，均无则清空）。"""
    for name, env_key in _ENV_NAMES.items():
        env_val = os.getenv(env_key, "").strip()
        if env_val:
            _state[name] = env_val
            continue
        row = db.query(Config).filter(Config.key == _DB_KEYS[name]).first()
        _state[name] = (str(row.value).strip() if row and row.value is not None else "")
    try:
        _state["port"] = int(_state.get("port") or 587)
    except (TypeError, ValueError):
        _state["port"] = 587
    _state["security"] = str(_state.get("security") or "starttls").lower()
    if _state["security"] not in ("starttls", "ssl", "none"):
        _state["security"] = "starttls"
    _state["timeout"] = app_config.SMTP_TIMEOUT
    _state["base_url"] = app_config.PUBLIC_BASE_URL
    return dict(_state)


def get_smtp_config(db) -> Dict[str, Any]:
    """返回当前配置（密码掩码），供管理接口展示。"""
    load_smtp_config_from_db(db)
    return {
        "smtp_host": _state["host"],
        "smtp_port": _state["port"],
        "smtp_security": _state["security"],
        "smtp_user": _state["user"],
        "smtp_password": "******" if _state["password"] else "",
        "smtp_from": _state["from_addr"],
        "configured": bool(_state["host"]),
    }


def save_smtp_config(db, data: Dict[str, Any]) -> None:
    """保存 SMTP 配置到 config 表（密码传 '******' 或空表示不修改）。"""
    from datetime import datetime

    for name, db_key in _DB_KEYS.items():
        if db_key not in data:
            continue
        raw_value = data[db_key]
        value = "" if raw_value is None else str(raw_value).strip()
        if name == "password" and value in ("", "******"):
            continue
        row = db.query(Config).filter(Config.key == db_key).first()
        if row:
            row.value = value
            row.update_time = datetime.now()
        else:
            db.add(Config(key=db_key, value=value, description=f"邮件通知 {db_key}"))
    db.commit()
    load_smtp_config_from_db(db)


def smtp_configured() -> bool:
    return bool(_state.get("host"))


def _build_message(recipients: List[str], subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    from_addr = _state.get("from_addr") or _state.get("user") or ""
    if from_addr:
        msg["From"] = formataddr(("备品备件管理系统", from_addr))
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    return msg


def send_mail(recipients: List[str], subject: str, body: str) -> Tuple[bool, str]:
    """发送邮件。返回 (是否成功, 失败原因)；任何异常都不向上抛出。"""
    to_list = [r.strip() for r in (recipients or []) if r and r.strip()]
    if not to_list:
        return False, "无收件人"
    if not smtp_configured():
        return False, "SMTP 未配置"
    msg = _build_message(to_list, subject, body)
    host = _state["host"]
    port = int(_state.get("port") or 587)
    security = _state.get("security") or "starttls"
    timeout = int(_state.get("timeout") or 15)
    try:
        if security == "ssl":
            context = ssl.create_default_context()
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
        try:
            server.ehlo()
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if _state.get("user") and _state.get("password"):
                server.login(_state["user"], _state["password"])
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass
        logging.info("邮件已发送：%s ← %s", ", ".join(to_list), subject)
        return True, ""
    except Exception as e:  # 邮件失败不得影响业务
        logging.error("邮件发送失败（收件人 %s）：%s", ", ".join(to_list), e)
        return False, str(e)
