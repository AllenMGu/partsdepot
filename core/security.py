"""认证核心：密码哈希、JWT 生成、当前用户依赖、认证 Cookie。"""

from fastapi import HTTPException, Depends, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional
from passlib.context import CryptContext
from jose import JWTError, jwt
import os

from core.config import SECRET_KEY, ALGORITHM
from core.models import User
from core.deps import get_db

# ===== 原 main.py 块: pwd/oauth2/cookie =====
# 密码加密上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
AUTH_COOKIE_NAME = "access_token"
AUTH_COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "lax")
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"

# ===== 原 main.py 块: verify/hash =====
# 密码验证
def verify_password(plain_password: str, hashed_password: str):
    return pwd_context.verify(plain_password, hashed_password)

# 密码加密
def get_password_hash(password: str):
    return pwd_context.hash(password)

# ===== 原 main.py 块: create_access_token =====
# 创建JWT Token
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# ===== 原 main.py 块: get_current_user =====
# 获取当前用户
async def get_current_user(request: Request, db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )

    tokens_to_try: List[str] = []
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        header_token = authorization.split(" ", 1)[1].strip()
        if header_token and header_token.lower() not in {"null", "undefined"}:
            tokens_to_try.append(header_token)

    cookie_token = request.cookies.get(AUTH_COOKIE_NAME)
    if cookie_token and cookie_token not in tokens_to_try:
        tokens_to_try.append(cookie_token)

    username: Optional[str] = None
    for token in tokens_to_try:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
            if username:
                break
        except JWTError:
            continue

    if username is None:
        raise credentials_exception
    
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户已被禁用")
    return user
