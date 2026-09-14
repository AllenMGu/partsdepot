"""多仓库管理系统（WMS）应用入口。

拆分说明（见 core/ 与 api/ 各模块 docstring）：
- main:app 启动方式保持不变（uvicorn main:app）；
- 全部 /api 路由仍按原单体 main.py 中的注册顺序装配（见下方 include_router 序列）；
- 配置/模型/依赖/认证在 core/，七个业务域路由在 api/；
- 前端静态文件仍从“main.py 所在目录”的 frontend/ 加载（路径行为不变）。
"""

from fastapi import FastAPI, APIRouter
from sqlalchemy.orm import Session
import logging
import os

from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
# 注：建表由 core/models.py 在导入时执行（Base.metadata.create_all，
#     等价于原单体 main.py 中模型定义后的那次调用），此处不再重复。
from core.database import SessionLocal
from core.models import UserRole, Warehouse, UserWarehouse, User
from core.security import get_password_hash

# ------------------- FastAPI应用初始化 -------------------
app = FastAPI(title="多仓库管理系统API")

# 跨域配置（可用环境变量 CORS_ORIGINS 逗号分隔覆盖；默认保持原白名单）
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost,http://127.0.0.1").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

# ------------------- 路由装配（顺序与原单体 main.py 完全一致） -------------------
from core import auth as auth_routes
from core import ldap as ldap_routes
from api import users, warehouses, locations, goods, stock, check, inbound, outbound

api_router = APIRouter()
api_router.include_router(auth_routes.router)        # /token /logout
api_router.include_router(users.router)              # 用户新增/切换仓库/仓库列表
api_router.include_router(ldap_routes.router)        # /ldap/config GET/PUT、/ldap/import-users
api_router.include_router(users.router_post)         # 用户列表/修改/分配/删除/取消分配
api_router.include_router(warehouses.router)
api_router.include_router(locations.router)
api_router.include_router(goods.router)
api_router.include_router(stock.router)              # 扫码出入库
api_router.include_router(stock.router_query)        # 库存查询
api_router.include_router(check.router)              # 扫码盘点
api_router.include_router(stock.router_logs)         # 出入库记录
api_router.include_router(check.router_report)       # 盘点记录/统计/差异
api_router.include_router(check.router_orders)       # 盘点单管理
api_router.include_router(inbound.router)
api_router.include_router(outbound.router)

# 初始管理员自举（见 README）：
# 设置 ADMIN_USERNAME/ADMIN_PASSWORD 且数据库尚无任何用户时，
# 创建初始管理员并授权所有现有仓库；账号一旦存在（含已创建的管理员）即不再触发，
# 因此创建成功后即可移除该环境变量。
def _bootstrap_admin_if_requested(db: Session):
    admin_username = os.getenv("ADMIN_USERNAME", "").strip()
    admin_password = os.getenv("ADMIN_PASSWORD", "").strip()
    if not admin_username or not admin_password:
        return
    if db.query(User).count() > 0:
        return
    admin = User(
        username=admin_username,
        hashed_password=get_password_hash(admin_password),
        full_name=admin_username,
        role=UserRole.ADMIN,
        is_active=True,
        is_ldap_user=False
    )
    db.add(admin)
    db.flush()
    warehouses = db.query(Warehouse).all()
    for warehouse in warehouses:
        db.add(UserWarehouse(user_id=admin.id, warehouse_id=warehouse.id, is_default=False))
    if warehouses:
        admin.current_warehouse_id = warehouses[0].id
    db.commit()
    logging.warning("已通过 ADMIN_USERNAME 创建初始管理员 %s（已授权 %d 个现有仓库）；首次登录后请移除该环境变量",
                    admin_username, len(warehouses))

with SessionLocal() as _bootstrap_db:
    try:
        _bootstrap_admin_if_requested(_bootstrap_db)
    except Exception:
        _bootstrap_db.rollback()
        logging.exception("初始管理员自举失败")

# 包含路由到app
app.include_router(api_router, prefix="/api")

# 前端静态托管（见 README）：仓库自带 frontend/ 目录时直接由后端托管；
# 生产环境通常由 nginx 优先服务静态文件，此挂载用于独立部署/本地开发场景
_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
if os.path.isfile(os.path.join(_FRONTEND_DIR, "index.html")):
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
