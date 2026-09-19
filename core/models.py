"""数据库模型（ORM）与枚举定义。"""

from fastapi import status
from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey, Boolean, Enum, UniqueConstraint, CheckConstraint, text
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from core.database import engine, Base

# ===== 原 main.py 块: 枚举+ORM模型 =====
# ------------------- 枚举定义 -------------------
class UserRole(str, enum.Enum):
    ADMIN = "admin"       # 仓库管理员，可管理所有数据
    OPERATOR = "operator" # 操作员，仅可做出入库和盘点

class InventoryType(str, enum.Enum):
    IN = "入库"
    OUT = "出库"

# ------------------- 数据库模型 -------------------
# 0. 配置表
class Config(Base):
    __tablename__ = "config"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, index=True, comment="配置项名称")
    value = Column(String(500), comment="配置项值")
    description = Column(String(500), comment="配置项描述")
    create_time = Column(DateTime, default=datetime.now)
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now)

# 1. 仓库表
class Warehouse(Base):
    __tablename__ = "warehouses"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, index=True, comment="仓库编码")
    name = Column(String(100), comment="仓库名称")
    address = Column(String(200), comment="仓库地址")
    is_active = Column(Boolean, default=True, comment="是否启用")
    create_time = Column(DateTime, default=datetime.now)

# 添加用户-仓库关联表（多对多）
class UserWarehouse(Base):
    __tablename__ = "user_warehouses"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), comment="用户ID")
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="仓库ID")
    is_default = Column(Boolean, default=False, comment="是否默认仓库")
    create_time = Column(DateTime, default=datetime.now)

# 2. 用户表
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, comment="用户名")
    hashed_password = Column(String(100), comment="加密密码")
    full_name = Column(String(100), comment="真实姓名")
    role = Column(Enum(UserRole), default=UserRole.OPERATOR, comment="角色")
    is_active = Column(Boolean, default=True, comment="是否启用")
    is_ldap_user = Column(Boolean, default=False, comment="是否是LDAP用户")
    create_time = Column(DateTime, default=datetime.now)
    current_warehouse_id = Column(Integer, nullable=True, comment="当前选择的仓库ID")
    
    # 多对多关联
    warehouses = relationship("Warehouse", secondary="user_warehouses",
                            backref="users", lazy="dynamic")

# 3. 库位表（关联仓库）
class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="所属仓库ID")
    location_code = Column(String(50), unique=True, index=True, comment="库位编码")
    name = Column(String(100), comment="库位名称")
    is_active = Column(Boolean, default=True, comment="是否启用")
    create_time = Column(DateTime, default=datetime.now)
    
    # 关联关系
    warehouse = relationship("Warehouse")

# 4. 货物表（全局货物，多仓库共享）
class Goods(Base):
    __tablename__ = "goods"
    id = Column(Integer, primary_key=True, index=True)
    barcode = Column(String(100), unique=True, index=True, comment="货物条码")
    name = Column(String(100), comment="货物名称")
    spec = Column(String(100), comment="规格型号")
    unit = Column(String(20), comment="单位")
    price = Column(Float, comment="单价")
    create_time = Column(DateTime, default=datetime.now)

# 5. 库存表（关联仓库+库位+货物）
class Stock(Base):
    __tablename__ = "stock"
    id = Column(Integer, primary_key=True, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="仓库ID")
    goods_id = Column(Integer, ForeignKey("goods.id"), comment="货物ID")
    location_id = Column(Integer, ForeignKey("locations.id"), comment="库位ID")
    quantity = Column(Float, default=0, comment="库存数量")
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 复合唯一索引，防止相同仓库、货物、库位的重复记录
    __table_args__ = (
        UniqueConstraint('warehouse_id', 'goods_id', 'location_id', name='_warehouse_goods_location_uc'),
        CheckConstraint('quantity >= 0', name='stock_quantity_non_negative'),
    )

    # 关联关系
    warehouse = relationship("Warehouse")
    goods = relationship("Goods")
    location = relationship("Location")

# 6. 出入库记录表
class InventoryRecord(Base):
    __tablename__ = "inventory_records"
    id = Column(Integer, primary_key=True, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="仓库ID")
    goods_id = Column(Integer, ForeignKey("goods.id"), comment="货物ID")
    location_id = Column(Integer, ForeignKey("locations.id"), comment="库位ID")
    type = Column(Enum(InventoryType), comment="类型：入库/出库")
    quantity = Column(Float, comment="数量")
    operator_id = Column(Integer, ForeignKey("users.id"), comment="操作员ID")
    remark = Column(String(500), comment="备注")
    create_time = Column(DateTime, default=datetime.now)
    
    # 关联关系
    warehouse = relationship("Warehouse")
    goods = relationship("Goods")
    location = relationship("Location")
    operator = relationship("User")

# 7. 盘点单表头
class CheckOrderHeader(Base):
    __tablename__ = "check_order_header"
    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String(50), unique=True, index=True, comment="盘点单号")
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="仓库ID")
    operator_id = Column(Integer, ForeignKey("users.id"), comment="操作员ID")
    remark = Column(String(500), comment="备注")
    status = Column(String(20), default="DRAFT", comment="状态: DRAFT-草稿, IN_PROGRESS-盘点中, COMPLETED-已完成")
    create_time = Column(DateTime, default=datetime.now)
    start_time = Column(DateTime, comment="开始时间")
    complete_time = Column(DateTime, comment="完成时间")

    # 关联关系
    warehouse = relationship("Warehouse")
    operator = relationship("User")
    items = relationship("CheckOrderItem", back_populates="header", cascade="all, delete-orphan")

# 8. 盘点单明细
class CheckOrderItem(Base):
    __tablename__ = "check_order_item"
    id = Column(Integer, primary_key=True, index=True)
    header_id = Column(Integer, ForeignKey("check_order_header.id"), comment="单据头ID")
    goods_id = Column(Integer, ForeignKey("goods.id"), comment="货物ID")
    location_id = Column(Integer, ForeignKey("locations.id"), comment="库位ID")
    check_quantity = Column(Float, comment="盘点数量")
    actual_quantity = Column(Float, comment="系统库存数量")
    diff_quantity = Column(Float, comment="差异数量")
    create_time = Column(DateTime, default=datetime.now)

    # 关联关系
    header = relationship("CheckOrderHeader", back_populates="items")
    goods = relationship("Goods")
    location = relationship("Location")

# 9. 盘点记录表（保留历史记录）
class CheckRecord(Base):
    __tablename__ = "check_records"
    id = Column(Integer, primary_key=True, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="仓库ID")
    goods_id = Column(Integer, ForeignKey("goods.id"), comment="货物ID")
    location_id = Column(Integer, ForeignKey("locations.id"), comment="库位ID")
    check_quantity = Column(Float, comment="盘点数量")
    actual_quantity = Column(Float, comment="实际库存数量")
    operator_id = Column(Integer, ForeignKey("users.id"), comment="操作员ID")
    check_time = Column(DateTime, default=datetime.now)

    # 关联关系
    warehouse = relationship("Warehouse")
    goods = relationship("Goods")
    location = relationship("Location")
    operator = relationship("User")

# 8. 入库单表头
class InboundOrderHeader(Base):
    __tablename__ = "inbound_order_header"
    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String(50), unique=True, index=True, comment="入库单号")
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="仓库ID")
    supplier = Column(String(200), comment="供应商")
    operator_id = Column(Integer, ForeignKey("users.id"), comment="操作员ID")
    total_amount = Column(Float, default=0, comment="总金额")
    remark = Column(String(500), comment="备注")
    status = Column(String(20), default="DRAFT", comment="状态")
    create_time = Column(DateTime, default=datetime.now)
    submit_time = Column(DateTime, comment="提交时间")
    complete_time = Column(DateTime, comment="完成时间")
    
    # 关联关系
    warehouse = relationship("Warehouse")
    operator = relationship("User")
    items = relationship("InboundOrderItem", back_populates="header", cascade="all, delete-orphan")

# 9. 入库单明细
class InboundOrderItem(Base):
    __tablename__ = "inbound_order_item"
    id = Column(Integer, primary_key=True, index=True)
    header_id = Column(Integer, ForeignKey("inbound_order_header.id"), comment="单据头ID")
    goods_id = Column(Integer, ForeignKey("goods.id"), comment="货物ID")
    location_id = Column(Integer, ForeignKey("locations.id"), comment="库位ID")
    quantity = Column(Float, comment="数量")
    unit_price = Column(Float, comment="单价")
    total_price = Column(Float, comment="总价")
    remark = Column(String(500), comment="备注")
    create_time = Column(DateTime, default=datetime.now)
    
    # 关联关系
    header = relationship("InboundOrderHeader", back_populates="items")
    goods = relationship("Goods")
    location = relationship("Location")

# 10. 出库单表头
class OutboundOrderHeader(Base):
    __tablename__ = "outbound_order_header"
    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String(50), unique=True, index=True, comment="出库单号")
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="仓库ID")
    customer = Column(String(200), comment="客户")
    operator_id = Column(Integer, ForeignKey("users.id"), comment="操作员ID")
    total_amount = Column(Float, default=0, comment="总金额")
    remark = Column(String(500), comment="备注")
    status = Column(String(20), default="DRAFT", comment="状态")
    create_time = Column(DateTime, default=datetime.now)
    submit_time = Column(DateTime, comment="提交时间")
    complete_time = Column(DateTime, comment="完成时间")
    
    # 关联关系
    warehouse = relationship("Warehouse")
    operator = relationship("User")
    items = relationship("OutboundOrderItem", back_populates="header", cascade="all, delete-orphan")

# 11. 出库单明细
class OutboundOrderItem(Base):
    __tablename__ = "outbound_order_item"
    id = Column(Integer, primary_key=True, index=True)
    header_id = Column(Integer, ForeignKey("outbound_order_header.id"), comment="单据头ID")
    goods_id = Column(Integer, ForeignKey("goods.id"), comment="货物ID")
    location_id = Column(Integer, ForeignKey("locations.id"), comment="库位ID")
    quantity = Column(Float, comment="数量")
    unit_price = Column(Float, comment="单价")
    total_price = Column(Float, comment="总价")
    remark = Column(String(500), comment="备注")
    create_time = Column(DateTime, default=datetime.now)
    
    # 关联关系
    header = relationship("OutboundOrderHeader", back_populates="items")
    goods = relationship("Goods")
    location = relationship("Location")

# 12. 申请单（免登录公共提交；详见 api/requests.py）
class RequestStatus(str, enum.Enum):
    PENDING = "pending"    # 待处理
    APPROVED = "approved"  # 已通过
    REJECTED = "rejected"  # 已驳回

# 12.1 申请单（近期/活跃）
class Request(Base):
    __tablename__ = "requests"
    id = Column(Integer, primary_key=True, index=True)
    applicant_name = Column(String(100), nullable=False, comment="申请人")
    department = Column(String(100), comment="部门")
    contact = Column(String(200), nullable=False, comment="邮箱")
    category = Column(String(50), comment="申请类别（已停用，保留兼容）")
    description = Column(Text, nullable=False, comment="事由描述")
    attachment_note = Column(String(500), comment="备注（附件文件名与交付方式等，v1 不支持二进制上传）")
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="申请仓库（通过审批时从该仓库扣减库存；历史数据为空）")
    goods_barcode = Column(String(100), comment="（已停用，货物改存 request_items）条码")
    goods_name = Column(String(100), comment="（已停用，货物改存 request_items）名称")
    goods_spec = Column(String(100), comment="（已停用，货物改存 request_items）规格型号")
    goods_unit = Column(String(20), comment="（已停用，货物改存 request_items）单位")
    goods_quantity = Column(Float, comment="（已停用，货物改存 request_items）数量")
    status = Column(String(20), default=RequestStatus.PENDING.value, index=True, comment="状态: pending/approved/rejected")
    handler_name = Column(String(100), comment="处理人")
    handle_time = Column(DateTime, comment="处理时间")
    outbound_order_no = Column(String(50), index=True, comment="出库单号（审批通过自动生成出库单时写入；无明细仅留痕的单为空）")
    create_time = Column(DateTime, default=datetime.now, index=True)
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    # ORM 级联（评审 P1 方案 A）：对象级删除 Request 时连同明细删除；
    # passive_deletes=True 让数据库 ondelete=CASCADE 兜底，避免删除前额外加载明细
    items = relationship("RequestItem", backref="request", cascade="all, delete-orphan", passive_deletes=True)

# 12.2 申请单归档表（由归档任务从 requests 移入，数据不丢失）
class RequestArchive(Base):
    __tablename__ = "requests_archive"
    # 同一原申请单最多归档一次：并发归档（后台任务 vs 手动触发、多 worker）的幂等兜底
    __table_args__ = (UniqueConstraint("original_id", name="uq_requests_archive_original_id"),)
    id = Column(Integer, primary_key=True, index=True)
    original_id = Column(Integer, index=True, comment="原申请单ID（requests.id），全表唯一")
    applicant_name = Column(String(100), nullable=False, comment="申请人")
    department = Column(String(100), comment="部门")
    contact = Column(String(200), nullable=False, comment="邮箱")
    category = Column(String(50), comment="申请类别（已停用，保留兼容）")
    description = Column(Text, nullable=False, comment="事由描述")
    attachment_note = Column(String(500), comment="备注")
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), comment="申请仓库（原单仓库，归档时原样保留）")
    goods_barcode = Column(String(100), comment="（已停用，货物改存 request_items_archive）条码")
    goods_name = Column(String(100), comment="（已停用，货物改存 request_items_archive）名称")
    goods_spec = Column(String(100), comment="（已停用，货物改存 request_items_archive）规格型号")
    goods_unit = Column(String(20), comment="（已停用，货物改存 request_items_archive）单位")
    goods_quantity = Column(Float, comment="（已停用，货物改存 request_items_archive）数量")
    status = Column(String(20), default=RequestStatus.PENDING.value, comment="归档时状态")
    handler_name = Column(String(100), comment="处理人")
    handle_time = Column(DateTime, comment="处理时间")
    create_time = Column(DateTime, nullable=False, comment="原提交时间")
    update_time = Column(DateTime, comment="原最后更新时间")
    archived_at = Column(DateTime, default=datetime.now, comment="归档时间")
    archive_batch = Column(String(20), index=True, comment="归档批次，如 2026-10")
    outbound_order_no = Column(String(50), comment="出库单号（归档时从原申请单保留）")

# 12.3 申请单货物明细（一行一种货物，提交时按条码查库快照；替代 requests 单货物字段）
class RequestItem(Base):
    __tablename__ = "request_items"
    id = Column(Integer, primary_key=True, index=True)
    # 真实外键 + 级联删除（评审 P1）：申请单被删时明细由数据库级联清除，不留孤儿行；
    # 归档流程仍在同一事务内显式删除明细（双保险，且不依赖数据库级联是否启用）
    request_id = Column(Integer, ForeignKey("requests.id", ondelete="CASCADE"), index=True,
                        comment="所属申请单ID（requests.id，级联删除）")
    sort = Column(Integer, default=0, comment="行序（0 起）")
    barcode = Column(String(100), nullable=False, comment="货物条码（必须存在于货物表）")
    name = Column(String(100), comment="货物名称（提交时快照）")
    spec = Column(String(100), comment="规格型号（提交时快照）")
    unit = Column(String(20), comment="单位（提交时快照）")
    quantity = Column(Float, nullable=False, comment="数量（>0）")

# 12.4 归档货物明细（归档时从 request_items 拷贝，数据不丢失）
class RequestItemArchive(Base):
    __tablename__ = "request_items_archive"
    id = Column(Integer, primary_key=True, index=True)
    # 外键指向归档单（防同类孤儿行）；original_request_id 是历史引用（原单已删），不能建 FK
    archive_id = Column(Integer, ForeignKey("requests_archive.id", ondelete="CASCADE"), index=True,
                        comment="所属归档单ID（requests_archive.id，级联删除）")
    original_request_id = Column(Integer, index=True, comment="原申请单ID（requests.id，历史引用）")
    sort = Column(Integer, default=0, comment="行序（0 起）")
    barcode = Column(String(100), nullable=False, comment="货物条码")
    name = Column(String(100), comment="货物名称")
    spec = Column(String(100), comment="规格型号")
    unit = Column(String(20), comment="单位")
    quantity = Column(Float, nullable=False, comment="数量")

# 12.5 申请单货物修改审计：仅记录管理员在 pending 状态下对明细的变更。
# before/after 使用 JSON 文本保存，避免改变现有 request_items 结构并兼容 SQLite/PostgreSQL。
class RequestItemAudit(Base):
    __tablename__ = "request_item_audits"
    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(Integer, ForeignKey("requests.id", ondelete="CASCADE"), index=True, nullable=False)
    request_item_id = Column(Integer, ForeignKey("request_items.id", ondelete="SET NULL"), nullable=True, index=True)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(30), nullable=False, comment="ADD_ITEM/UPDATE_ITEM/DELETE_ITEM")
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    create_time = Column(DateTime, default=datetime.now, nullable=False, index=True)

    request = relationship("Request")
    operator = relationship("User")

# 12.6 归档后的申请单货物修改审计。归档时从 request_item_audits 搬移，保留完整历史。
class RequestItemAuditArchive(Base):
    __tablename__ = "request_item_audits_archive"
    id = Column(Integer, primary_key=True, index=True)
    archive_id = Column(Integer, ForeignKey("requests_archive.id", ondelete="CASCADE"), index=True, nullable=False)
    original_request_id = Column(Integer, index=True, nullable=False)
    request_item_id = Column(Integer, nullable=True, index=True)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String(30), nullable=False)
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    create_time = Column(DateTime, default=datetime.now, nullable=False, index=True)

    archive = relationship("RequestArchive")
    operator = relationship("User")

# 12.7 批量扫码业务幂等记录。业务提交成功后保存响应，重试同一 key 直接返回原结果。
class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("operation", "idempotency_key", name="uq_idempotency_operation_key"),
    )
    id = Column(Integer, primary_key=True, index=True)
    operation = Column(String(50), nullable=False)
    idempotency_key = Column(String(200), nullable=False)
    # nullable 兼容已存在的历史记录；新写入记录始终填充这两个字段。
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    request_hash = Column(String(64), nullable=True, index=True)
    response_json = Column(Text, nullable=False)
    create_time = Column(DateTime, default=datetime.now, nullable=False, index=True)

    operator = relationship("User")

# 创建所有表
Base.metadata.create_all(bind=engine)

# 存量库兼容迁移（create_all 不会改动已存在的表结构）：
# 申请类别停用后允许为空。生产库 requests / requests_archive 当前无数据，DROP NOT NULL 零风险。
# 测试库（SQLite）由 create_all 按新模型（nullable）直接建表，无需迁移。
# fail-fast（评审 P2）：迁移失败直接让启动失败——若带着旧 NOT NULL 约束继续运行，
# 每张新申请都会在 commit 时 500，"启动成功但服务不可用"比拒绝启动更糟。
if engine.dialect.name == "postgresql":
    with engine.begin() as _mig_conn:
        _mig_conn.execute(text("ALTER TABLE requests ALTER COLUMN category DROP NOT NULL"))
        _mig_conn.execute(text("ALTER TABLE requests_archive ALTER COLUMN category DROP NOT NULL"))
        # 申请仓库（通过审批时从该仓库扣减库存）：新增可空列，存量行保持 NULL。
        # ADD COLUMN IF NOT EXISTS 保证重复启动幂等。
        _mig_conn.execute(text(
            "ALTER TABLE requests ADD COLUMN IF NOT EXISTS warehouse_id INTEGER REFERENCES warehouses(id)"))
        _mig_conn.execute(text(
            "ALTER TABLE requests_archive ADD COLUMN IF NOT EXISTS warehouse_id INTEGER REFERENCES warehouses(id)"))
        # 出库单号（审批通过自动生成出库单）：新增可空列，存量行保持 NULL（历史已审批单无出库单）。
        _mig_conn.execute(text("ALTER TABLE requests ADD COLUMN IF NOT EXISTS outbound_order_no VARCHAR(50)"))
        _mig_conn.execute(text("ALTER TABLE requests_archive ADD COLUMN IF NOT EXISTS outbound_order_no VARCHAR(50)"))
else:
    # SQLite：ADD COLUMN 无 IF NOT EXISTS，先查 information_schema 等价物（PRAGMA）再补列，
    # 保证重复启动幂等。
    def _sqlite_has_col(table: str, col: str) -> bool:
        with engine.connect() as _c:
            names = [r[1] for r in _c.execute(text(f"PRAGMA table_info({table})")).fetchall()]
        return col in names

    if not _sqlite_has_col("requests", "warehouse_id"):
        with engine.begin() as _mig_conn:
            _mig_conn.execute(text("ALTER TABLE requests ADD COLUMN warehouse_id INTEGER"))
    if not _sqlite_has_col("requests_archive", "warehouse_id"):
        with engine.begin() as _mig_conn:
            _mig_conn.execute(text("ALTER TABLE requests_archive ADD COLUMN warehouse_id INTEGER"))
    if not _sqlite_has_col("requests", "outbound_order_no"):
        with engine.begin() as _mig_conn:
            _mig_conn.execute(text("ALTER TABLE requests ADD COLUMN outbound_order_no VARCHAR(50)"))
    if not _sqlite_has_col("requests_archive", "outbound_order_no"):
        with engine.begin() as _mig_conn:
            _mig_conn.execute(text("ALTER TABLE requests_archive ADD COLUMN outbound_order_no VARCHAR(50)"))
