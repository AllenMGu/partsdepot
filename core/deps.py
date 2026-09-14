"""通用依赖：数据库会话、上传体积限制。"""

from fastapi import HTTPException, UploadFile
from typing import List

from core.config import MAX_IMPORT_FILE_SIZE_MB
from core.database import SessionLocal

# ===== 原 main.py 块: get_db =====
# 获取数据库会话
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ===== 原 main.py 块: 上传限流 =====
async def read_upload_with_size_limit(upload_file: UploadFile, max_bytes: int) -> bytes:
    """读取上传文件并限制最大体积，防止大文件占满内存。"""
    total = 0
    chunks: List[bytes] = []
    while True:
        chunk = await upload_file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"上传文件过大，最大允许 {MAX_IMPORT_FILE_SIZE_MB}MB"
            )
        chunks.append(chunk)
    return b"".join(chunks)
