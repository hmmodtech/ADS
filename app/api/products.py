from __future__ import annotations

import io
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Campaign, Product
from ..schemas import ProductIn, ProductPatch
from .serializers import product_out

router = APIRouter(prefix="/api/products", tags=["products"])
MAX_UPLOAD = 8 * 1024 * 1024


def _get(db: Session, pid: str) -> Product:
    p = db.get(Product, pid)
    if not p:
        raise HTTPException(404, "المنتج غير موجود")
    return p


@router.get("")
def list_products(q: str = "", db: Session = Depends(get_db)):
    query = db.query(Product).order_by(Product.created_at.desc())
    if q:
        like = f"%{q}%"
        query = query.filter((Product.name_en.ilike(like)) | (Product.name_ar.ilike(like)) | (Product.brand.ilike(like)))
    return [product_out(p) for p in query.limit(500).all()]


@router.post("", status_code=201)
def create_product(body: ProductIn, db: Session = Depends(get_db)):
    data = body.model_dump()
    data["prices"] = {k: v for k, v in data["prices"].items()}
    p = Product(**data, source="manual")
    db.add(p)
    db.commit()
    return product_out(p)


@router.get("/{pid}")
def get_product(pid: str, db: Session = Depends(get_db)):
    return product_out(_get(db, pid))


@router.patch("/{pid}")
def patch_product(pid: str, body: ProductPatch, db: Session = Depends(get_db)):
    p = _get(db, pid)
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "currency" and v:
            from ..geo import USD_RATES
            if v.upper() not in USD_RATES:
                raise HTTPException(422, "عملة غير مدعومة")
            v = v.upper()
        if k == "prices" and v is not None:
            from ..geo import COUNTRIES
            bad = [c for c in v if c.upper() not in COUNTRIES]
            if bad:
                raise HTTPException(422, f"دول غير مدعومة: {bad}")
            v = {c.upper(): x for c, x in v.items()}
        setattr(p, k, v)
    db.commit()
    return product_out(p)


@router.delete("/{pid}")
def delete_product(pid: str, db: Session = Depends(get_db)):
    p = _get(db, pid)
    in_use = db.query(Campaign).filter(Campaign.product_id == pid).count()
    if in_use:
        p.active = False
        db.commit()
        return {"status": "deactivated", "message": f"لديه {in_use} حملة؛ تم تعطيله بدل الحذف"}
    db.delete(p)
    db.commit()
    return {"status": "deleted"}


@router.post("/{pid}/images")
async def upload_image(pid: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    p = _get(db, pid)
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "الصورة أكبر من 8MB")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(415, "الملف ليس صورة صالحة")
    # Re-encode: strips metadata/payloads and normalises the format.
    has_alpha = img.mode in ("RGBA", "LA", "P")
    folder = get_settings().data_dir / "uploads" / pid
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex[:10]}.{'png' if has_alpha else 'jpg'}"
    path = folder / name
    if has_alpha:
        img.convert("RGBA").save(path, "PNG", optimize=True)
    else:
        img.convert("RGB").save(path, "JPEG", quality=92)
    p.images = [*(p.images or []), f"/uploads/{pid}/{name}"]
    db.commit()
    return product_out(p)


@router.delete("/{pid}/images")
def delete_image(pid: str, ref: str, db: Session = Depends(get_db)):
    p = _get(db, pid)
    if ref not in (p.images or []):
        raise HTTPException(404, "الصورة غير موجودة")
    p.images = [i for i in p.images if i != ref]
    if ref.startswith("/uploads/"):
        f = get_settings().data_dir / ref.lstrip("/")
        if f.resolve().is_relative_to((get_settings().data_dir / "uploads").resolve()):
            Path(f).unlink(missing_ok=True)
    db.commit()
    return product_out(p)
