"""Pydantic 请求/响应模型。"""

from fastapi import status
from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional

from core.models import UserRole, InventoryType

# ===== 原 main.py 块: Pydantic 模型 =====
# ------------------- Pydantic模型 -------------------
class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str
    warehouse_ids: List[int] = Field(default_factory=list)  # 改为列表
    role: UserRole = UserRole.OPERATOR
    
    class Config:
        json_encoders = {
            UserRole: lambda v: v.value  # 确保枚举被正确序列化
        }

class UserResponse(BaseModel):
    id: int
    username: str
    full_name: str
    current_warehouse_id: Optional[int] = None
    current_warehouse_name: Optional[str] = None
    role: UserRole
    is_ldap_user: bool
    is_active: bool = True

    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None

    class Config:
        json_encoders = {
            UserRole: lambda v: v.value
        }

class WarehouseCreate(BaseModel):
    code: str
    name: str
    address: Optional[str] = ""

class WarehouseResponse(BaseModel):
    id: int
    code: str
    name: str
    address: str
    is_active: Optional[bool] = True

    class Config:
        from_attributes = True

class WarehouseUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    is_active: Optional[bool] = None

class LocationCreate(BaseModel):
    warehouse_id: int
    location_code: str
    name: str

class LocationResponse(BaseModel):
    id: int
    warehouse_id: int
    location_code: str
    name: str
    is_active: bool
    create_time: datetime

    class Config:
        from_attributes = True

class LocationUpdate(BaseModel):
    warehouse_id: Optional[int] = None
    location_code: Optional[str] = None
    name: Optional[str] = None
    is_active: Optional[bool] = None

class GoodsCreate(BaseModel):
    barcode: str
    name: str
    spec: Optional[str] = ""
    unit: Optional[str] = "个"
    price: Optional[float] = Field(default=0.0, ge=0)

class GoodsResponse(BaseModel):
    id: int
    barcode: str
    name: str
    spec: str
    unit: str
    price: float
    create_time: datetime

    class Config:
        from_attributes = True

class GoodsUpdate(BaseModel):
    barcode: Optional[str] = None
    name: Optional[str] = None
    spec: Optional[str] = None
    unit: Optional[str] = None
    price: Optional[float] = None

class InventoryCreate(BaseModel):
    goods_barcode: str  # 扫码传入条码
    location_code: str  # 扫码传入库位编码
    type: InventoryType
    quantity: float = Field(gt=0)
    remark: Optional[str] = ""

class CheckCreate(BaseModel):
    goods_barcode: str
    location_code: str
    check_quantity: float = Field(ge=0)

class StockResponse(BaseModel):
    id: int
    warehouse_id: int
    warehouse_name: str
    goods_id: int
    goods_name: str
    goods_barcode: str
    goods_price: float
    goods_spec: Optional[str] = ""
    goods_unit: Optional[str] = ""
    location_id: int
    location_code: str
    location_name: str
    quantity: float
    update_time: datetime

# 入库单相关模型
class InboundOrderItemCreate(BaseModel):
    goods_barcode: str  # 货物条码
    location_code: str  # 库位编码
    quantity: float = Field(gt=0)
    unit_price: Optional[float] = Field(default=None, ge=0)
    remark: Optional[str] = ""

class InboundOrderItemResponse(BaseModel):
    id: int
    goods_id: int
    goods_barcode: str
    goods_name: str
    location_id: int
    location_code: str
    quantity: float
    unit_price: float
    total_price: float
    remark: str
    
    class Config:
        from_attributes = True

class InboundOrderHeaderCreate(BaseModel):
    supplier: Optional[str] = ""
    remark: Optional[str] = ""

class InboundOrderHeaderResponse(BaseModel):
    id: int
    order_no: str
    warehouse_id: int
    warehouse_name: str
    supplier: str
    operator_id: int
    operator_name: str
    total_amount: float
    remark: str
    status: str
    create_time: datetime
    submit_time: Optional[datetime] = None
    complete_time: Optional[datetime] = None
    item_count: int = 0
    
    class Config:
        from_attributes = True

class InboundOrderDetailResponse(InboundOrderHeaderResponse):
    items: List[InboundOrderItemResponse] = []

# 出库单相关模型
class OutboundOrderItemCreate(BaseModel):
    goods_barcode: str
    location_code: str
    quantity: float = Field(gt=0)
    unit_price: Optional[float] = Field(default=None, ge=0)
    remark: Optional[str] = ""

class OutboundOrderItemResponse(BaseModel):
    id: int
    goods_id: int
    goods_barcode: str
    goods_name: str
    location_id: int
    location_code: str
    quantity: float
    unit_price: float
    total_price: float
    remark: str
    
    class Config:
        from_attributes = True

class OutboundOrderHeaderCreate(BaseModel):
    customer: Optional[str] = ""
    remark: Optional[str] = ""

class OutboundOrderHeaderResponse(BaseModel):
    id: int
    order_no: str
    warehouse_id: int
    warehouse_name: str
    customer: str
    operator_id: int
    operator_name: str
    total_amount: float
    remark: str
    status: str
    create_time: datetime
    submit_time: Optional[datetime] = None
    complete_time: Optional[datetime] = None
    item_count: int = 0
    
    class Config:
        from_attributes = True

class OutboundOrderDetailResponse(OutboundOrderHeaderResponse):
    items: List[OutboundOrderItemResponse] = []

# ===== 原 main.py 块: CheckOrder 模型 A =====
# 创建盘点单请求模型
class CheckOrderCreate(BaseModel):
    warehouse_id: Optional[int] = None
    remark: Optional[str] = None

# 盘点单响应模型
class CheckOrderHeaderResponse(BaseModel):
    id: int
    order_no: str
    warehouse_id: int
    warehouse_name: str
    operator_id: int
    operator_name: str
    remark: Optional[str]
    status: str
    create_time: datetime
    start_time: Optional[datetime]
    complete_time: Optional[datetime]
    item_count: int

    class Config:
        from_attributes = True

# 盘点单明细响应模型
class CheckOrderItemResponse(BaseModel):
    id: int
    goods_id: int
    goods_name: str
    goods_barcode: str
    location_id: int
    location_code: str
    location_name: str
    check_quantity: float
    actual_quantity: float
    diff_quantity: float
    create_time: datetime

    class Config:
        from_attributes = True

# 完整盘点单响应模型（包含明细）
class CheckOrderFullResponse(BaseModel):
    header: CheckOrderHeaderResponse
    items: List[CheckOrderItemResponse]

# ===== 原 main.py 块: CheckOrderItemCreate =====
# 添加盘点明细（扫码盘点）
class CheckOrderItemCreate(BaseModel):
    header_id: int
    goods_barcode: str
    location_code: str
    check_quantity: float = Field(ge=0)

# ------------------- 申请单（免登录公共提交 + 管理端） -------------------
class RequestSubmit(BaseModel):
    applicant_name: str = Field(..., min_length=1, max_length=100, description="申请人")
    department: Optional[str] = Field(None, max_length=100, description="部门（可选）")
    contact: str = Field(..., min_length=3, max_length=200, description="联系方式（电话/邮箱）")
    category: str = Field(..., min_length=1, max_length=50, description="申请类别")
    description: str = Field(..., min_length=1, max_length=2000, description="事由描述")
    attachment_note: Optional[str] = Field(None, max_length=500, description="附件说明（可选）")
    # 相关货物（可选）：客户端只提交 条码+数量；名称/规格/单位由后端按条码查库填充，
    # 不接收客户端快照字段（防伪造：真实条码配假名称、或提交不存在的条码）
    goods_barcode: Optional[str] = Field(None, max_length=100, description="相关货物条码（可选，必须存在于货物表）")
    goods_quantity: Optional[float] = Field(None, gt=0, description="相关货物数量（可选，选货物时必填且>0）")

class RequestResponse(BaseModel):
    id: int
    applicant_name: str
    department: Optional[str] = None
    contact: str
    category: str
    description: str
    attachment_note: Optional[str] = None
    goods_barcode: Optional[str] = None
    goods_name: Optional[str] = None
    goods_spec: Optional[str] = None
    goods_unit: Optional[str] = None
    goods_quantity: Optional[float] = None
    status: str
    handler_name: Optional[str] = None
    handle_time: Optional[datetime] = None
    create_time: datetime
    update_time: Optional[datetime] = None

    class Config:
        from_attributes = True

class RequestArchiveResponse(BaseModel):
    id: int
    original_id: int
    applicant_name: str
    department: Optional[str] = None
    contact: str
    category: str
    description: str
    attachment_note: Optional[str] = None
    goods_barcode: Optional[str] = None
    goods_name: Optional[str] = None
    goods_spec: Optional[str] = None
    goods_unit: Optional[str] = None
    goods_quantity: Optional[float] = None
    status: str
    handler_name: Optional[str] = None
    handle_time: Optional[datetime] = None
    create_time: datetime
    update_time: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    archive_batch: Optional[str] = None

    class Config:
        from_attributes = True

class RequestStatusUpdate(BaseModel):
    status: str = Field(..., description="approved 或 rejected")
