"""货物管理路由（含 Excel 导入/导出）。"""

from fastapi import HTTPException, Depends, APIRouter, File, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Optional
import logging
import pandas as pd
import io

from core.config import MAX_IMPORT_FILE_SIZE_BYTES
from core.models import UserRole, User, Goods
from core.schemas import GoodsCreate, GoodsResponse, GoodsUpdate
from core.security import get_current_user
from core.deps import get_db, read_upload_with_size_limit

router = APIRouter()

# ===== 原 main.py 块: 货物 =====
# ------------------- 货物管理接口 -------------------
@router.post("/goods/", response_model=GoodsResponse, summary="新增货物")
async def create_goods(
    goods: GoodsCreate, 
    current_user: User = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    # 货物主数据维护仅限管理员
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")
    db_goods = db.query(Goods).filter(Goods.barcode == goods.barcode).first()
    if db_goods:
        raise HTTPException(status_code=400, detail="货物条码已存在")
    
    new_goods = Goods(**goods.dict())
    db.add(new_goods)
    db.commit()
    db.refresh(new_goods)
    return new_goods

@router.get("/goods/", response_model=List[GoodsResponse], summary="获取所有货物（支持搜索）")
async def get_goods(
    keyword: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取货物列表，支持通过货物名称、条码或规格搜索"""
    query = db.query(Goods)

    if keyword:
        query = query.filter(
            Goods.name.contains(keyword) |
            Goods.barcode.contains(keyword) |
            Goods.spec.contains(keyword)
        )

    return query.all()

@router.get("/goods/export", summary="导出货物数据")
async def export_goods(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """导出货物数据为Excel文件"""
    try:
        # 获取所有货物数据
        goods_list = db.query(Goods).all()

        # 转换为DataFrame
        data = []
        for goods in goods_list:
            data.append({
                "条码": goods.barcode,
                "货物名称": goods.name,
                "规格型号": goods.spec,
                "单位": goods.unit,
                "单价": goods.price,
                "创建时间": goods.create_time.strftime("%Y-%m-%d %H:%M:%S")
            })

        df = pd.DataFrame(data)

        # 写入Excel文件
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='货物数据')

        output.seek(0)

        # 返回响应
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=goods_export.xlsx"}
        )
    except Exception as e:
        logging.exception("导出失败: %s", str(e))
        raise HTTPException(status_code=500, detail="导出失败")

@router.get("/goods/{id}", response_model=GoodsResponse, summary="获取单个货物详情")
async def get_goods_detail(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_goods = db.query(Goods).filter(Goods.id == id).first()
    if not db_goods:
        raise HTTPException(status_code=404, detail="货物未找到")
    return db_goods

@router.put("/goods/{id}", response_model=GoodsResponse, summary="修改货物信息")
async def update_goods(
    id: int,
    goods: GoodsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 货物主数据维护仅限管理员
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")
    db_goods = db.query(Goods).filter(Goods.id == id).first()
    if not db_goods:
        raise HTTPException(status_code=404, detail="货物未找到")
    
    for key, value in goods.dict(exclude_unset=True).items():
        setattr(db_goods, key, value)
    
    db.commit()
    db.refresh(db_goods)
    return db_goods

@router.delete("/goods/{id}", summary="删除货物")
async def delete_goods(
    id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")

    db_goods = db.query(Goods).filter(Goods.id == id).first()
    if not db_goods:
        raise HTTPException(status_code=404, detail="货物未找到")

    db.delete(db_goods)
    db.commit()
    return {"message": "删除成功"}

@router.get("/goods/export", summary="导出货物数据")
async def export_goods(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """导出货物数据为Excel文件"""
    try:
        # 获取所有货物数据
        goods_list = db.query(Goods).all()

        # 转换为DataFrame
        data = []
        for goods in goods_list:
            data.append({
                "条码": goods.barcode,
                "货物名称": goods.name,
                "规格型号": goods.spec,
                "单位": goods.unit,
                "单价": goods.price,
                "创建时间": goods.create_time.strftime("%Y-%m-%d %H:%M:%S")
            })

        df = pd.DataFrame(data)

        # 写入Excel文件
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='货物数据')

        output.seek(0)

        # 返回响应
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=goods_export.xlsx"}
        )
    except Exception as e:
        logging.exception("导出失败: %s", str(e))
        raise HTTPException(status_code=500, detail="导出失败")

@router.post("/goods/import", summary="导入货物数据")
async def import_goods(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """从Excel文件导入货物数据"""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限操作")

    try:
        # 读取Excel文件
        if not file.filename.lower().endswith((".xls", ".xlsx")):
            raise HTTPException(status_code=400, detail="仅支持导入 .xls 或 .xlsx 文件")
        contents = await read_upload_with_size_limit(file, MAX_IMPORT_FILE_SIZE_BYTES)
        df = pd.read_excel(io.BytesIO(contents))

        # 检查必要的列
        required_columns = ["条码", "货物名称", "规格型号", "单位", "单价"]
        for col in required_columns:
            if col not in df.columns:
                raise HTTPException(status_code=400, detail=f"缺少必要列：{col}")

        # 导入数据
        success_count = 0
        error_count = 0
        errors = []

        for index, row in df.iterrows():
            try:
                # 检查条码是否已存在
                existing_goods = db.query(Goods).filter(Goods.barcode == str(row["条码"])).first()
                if existing_goods:
                    error_count += 1
                    errors.append(f"第{index+1}行：条码{row['条码']}已存在")
                    continue

                # 创建新货物
                new_goods = Goods(
                    barcode=str(row["条码"]),
                    name=str(row["货物名称"]),
                    spec=str(row["规格型号"]) if pd.notna(row["规格型号"]) else "",
                    unit=str(row["单位"]) if pd.notna(row["单位"]) else "个",
                    price=float(row["单价"]) if pd.notna(row["单价"]) else 0.0
                )

                db.add(new_goods)
                success_count += 1
            except Exception as e:
                error_count += 1
                errors.append(f"第{index+1}行：数据处理失败")

        db.commit()

        return {
            "success_count": success_count,
            "error_count": error_count,
            "errors": errors
        }
    except Exception as e:
        db.rollback()
        logging.exception("导入失败: %s", str(e))
        raise HTTPException(status_code=500, detail="导入失败")
