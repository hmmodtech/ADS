from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..agents import orchestrator
from ..config import get_settings
from ..db import get_db
from ..models import Campaign, Product
from ..platforms import PlatformError, get_adapter
from ..schemas import ApproveIn, BudgetIn, CampaignIn, CopiesIn
from ..services.events import add_event
from .serializers import campaign_out

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])
EDITABLE = ("draft", "blocked", "pending_review", "failed")


def _get(db: Session, cid: str) -> Campaign:
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "الحملة غير موجودة")
    return c


def _out(c: Campaign, detail=False):
    return campaign_out(c, detail, busy=orchestrator.is_busy(c.id))


@router.post("", status_code=201)
async def create_campaign(body: CampaignIn, db: Session = Depends(get_db)):
    p = db.get(Product, body.product_id)
    if not p:
        raise HTTPException(404, "المنتج غير موجود")
    if not p.active:
        raise HTTPException(409, "المنتج غير نشط")
    data = body.model_dump(exclude={"custom_copy", "name", "product_id"})
    name = body.name or f"{p.display_name} - {'/'.join(body.countries)}"
    c = Campaign(**data, name=name, product_id=p.id, initial_daily_budget_usd=body.daily_budget_usd,
                 status="draft")
    if body.custom_copy:
        c.copies, c.custom_copy = body.custom_copy, True
    db.add(c)
    db.commit()
    add_event(db, c.id, "api", "أُنشئت الحملة")
    orchestrator.spawn(orchestrator.generate(c.id))
    return _out(c)


@router.get("")
def list_campaigns(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Campaign).order_by(Campaign.created_at.desc())
    if status:
        q = q.filter(Campaign.status == status)
    return [_out(c) for c in q.limit(300).all()]


@router.get("/{cid}")
def get_campaign(cid: str, db: Session = Depends(get_db)):
    return _out(_get(db, cid), detail=True)


@router.put("/{cid}/copies")
async def edit_copies(cid: str, body: CopiesIn, db: Session = Depends(get_db)):
    c = _get(db, cid)
    if c.status not in EDITABLE:
        raise HTTPException(409, "لا يمكن تعديل النصوص في هذه الحالة")
    if orchestrator.is_busy(cid):
        raise HTTPException(409, "الحملة قيد المعالجة")
    unknown = [cc for cc in body.copies if cc not in c.countries]
    if unknown:
        raise HTTPException(422, f"دول ليست ضمن الحملة: {unknown}")
    merged = {**(c.copies or {})}
    for cc, langs in body.copies.items():
        merged[cc] = {**(merged.get(cc) or {}), **{lang: blk.model_dump() for lang, blk in langs.items()}}
    c.copies, c.custom_copy, c.assets = merged, True, []
    db.commit()
    add_event(db, cid, "api", "عُدّلت النصوص يدوياً - إعادة الفحص وإنتاج الوسائط")
    orchestrator.spawn(orchestrator.generate(cid, only_finalize=True))
    return _out(c)


@router.post("/{cid}/regenerate")
async def regenerate(cid: str, db: Session = Depends(get_db)):
    c = _get(db, cid)
    if c.status not in EDITABLE:
        raise HTTPException(409, "لا يمكن إعادة الإنتاج في هذه الحالة")
    if orchestrator.is_busy(cid):
        raise HTTPException(409, "الحملة قيد المعالجة")
    c.custom_copy, c.copies, c.assets, c.compliance = False, {}, [], {}
    db.commit()
    orchestrator.spawn(orchestrator.generate(cid))
    return _out(c)


@router.post("/{cid}/recheck")
async def recheck(cid: str, db: Session = Depends(get_db)):
    c = _get(db, cid)
    if c.status not in EDITABLE:
        raise HTTPException(409, "لا يمكن إعادة الفحص في هذه الحالة")
    if orchestrator.is_busy(cid):
        raise HTTPException(409, "الحملة قيد المعالجة")
    orchestrator.spawn(orchestrator.generate(cid, only_finalize=True))
    return _out(c)


@router.post("/{cid}/approve")
async def approve(cid: str, body: ApproveIn | None = None, db: Session = Depends(get_db)):
    _get(db, cid)
    try:
        c = await orchestrator.approve(cid, activate=bool(body and body.activate))
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.expire_all()
    return _out(_get(db, cid), detail=True)


@router.post("/{cid}/pause")
async def pause(cid: str, db: Session = Depends(get_db)):
    _get(db, cid)
    try:
        await orchestrator.set_active(cid, False)
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.expire_all()
    return _out(_get(db, cid))


@router.post("/{cid}/resume")
async def resume(cid: str, db: Session = Depends(get_db)):
    _get(db, cid)
    try:
        await orchestrator.set_active(cid, True)
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.expire_all()
    return _out(_get(db, cid))


@router.post("/{cid}/refresh")
async def refresh(cid: str, db: Session = Depends(get_db)):
    _get(db, cid)
    try:
        decisions = await orchestrator.refresh_one(cid)
    except ValueError as e:
        raise HTTPException(404, str(e))
    db.expire_all()
    return {**_out(_get(db, cid), detail=True), "decisions": decisions}


@router.post("/{cid}/budget")
async def set_budget(cid: str, body: BudgetIn, db: Session = Depends(get_db)):
    c = _get(db, cid)
    if c.status in ("generating", "deploying"):
        raise HTTPException(409, "الحملة قيد المعالجة")
    if c.external_ids:
        try:
            for platform in c.external_ids:
                await get_adapter(platform).set_budget(c, body.daily_budget_usd)
        except PlatformError as e:
            raise HTTPException(502, str(e))
    old, c.daily_budget_usd = c.daily_budget_usd, body.daily_budget_usd
    db.commit()
    add_event(db, cid, "api", f"عُدّلت الميزانية اليومية يدوياً {old:g}$ ← {body.daily_budget_usd:g}$", "decision")
    return _out(c)


@router.delete("/{cid}")
def delete_campaign(cid: str, db: Session = Depends(get_db)):
    c = _get(db, cid)
    if c.status in ("live", "deploying", "generating") or orchestrator.is_busy(cid):
        raise HTTPException(409, "أوقف الحملة أولاً")
    real = [p for p, v in (c.external_ids or {}).items() if not v.get("simulated")]
    media = get_settings().media_dir / cid
    db.delete(c)
    db.commit()
    shutil.rmtree(media, ignore_errors=True)
    msg = "حُذفت من النظام فقط؛ احذفها يدوياً من " + "، ".join(real) if real else "تم الحذف"
    return {"status": "deleted", "message": msg}


@router.get("/{cid}/export")
def export_package(cid: str, db: Session = Depends(get_db)):
    """ZIP with media + ready-to-paste copy: lets you upload manually to Ads Manager when API access
    is unavailable (e.g. payment/account restrictions)."""
    c = _get(db, cid)
    if not c.assets and not c.copies:
        raise HTTPException(409, "لا يوجد محتوى جاهز للتصدير بعد")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("campaign.json", json.dumps(campaign_out(c, True), ensure_ascii=False, indent=2))
        lines = [f"الحملة: {c.name}", f"المنصات: {', '.join(c.platforms)}", f"الدول: {', '.join(c.countries)}",
                 f"الميزانية اليومية المقترحة: {c.daily_budget_usd:g}$", f"الجمهور: {c.age_min}-{c.age_max}, {c.gender}",
                 f"رابط الوجهة: {c.landing_url}", ""]
        for cc, langs in (c.copies or {}).items():
            for lang, blk in langs.items():
                lines += [f"===== {cc} / {lang} =====", f"العنوان: {blk.get('headline')}",
                          f"النص الرئيسي: {blk.get('primary_text')}", f"الوصف: {blk.get('description')}",
                          f"زر الدعوة: {blk.get('cta')}", ""]
        z.writestr("ads_copy.txt", "\n".join(lines))
        for a in c.assets or []:
            path = Path(a.get("path", ""))
            if path.exists():
                z.write(path, f"media/{path.name}")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{c.id}.zip"'})
