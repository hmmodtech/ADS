"""Orchestrator: runs the pipeline and owns every campaign state transition.

draft -> generating -> (blocked | pending_review) -> approved -> deploying -> (live | paused | failed)
A human approval step sits between generation and spending money (unless AUTO_APPROVE=true).
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal, utcnow
from ..models import Campaign
from ..platforms import PlatformError, get_adapter
from ..services.events import add_event
from ..services.landing import resolve_landing
from .compliance import ComplianceAgent
from .copywriter import CopywriterAgent
from .creative import CreativeAgent
from .deployer import DeployerAgent
from .optimizer import OptimizerAgent
from .strategist import StrategistAgent

log = logging.getLogger("orchestrator")
MAX_COPY_REVISIONS = 2


class Orchestrator:
    def __init__(self):
        self.strategist = StrategistAgent()
        self.copywriter = CopywriterAgent()
        self.creative = CreativeAgent()
        self.compliance = ComplianceAgent()
        self.deployer = DeployerAgent()
        self.optimizer = OptimizerAgent()
        self._busy: set[str] = set()
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------- scheduling
    def spawn(self, coro) -> None:
        """Run a pipeline stage in the background and keep a reference so it isn't garbage collected."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def is_busy(self, cid: str) -> bool:
        return cid in self._busy

    # --------------------------------------------------------------- generate
    async def generate(self, cid: str, only_finalize: bool = False) -> None:
        if cid in self._busy:
            return
        self._busy.add(cid)
        db = SessionLocal()
        try:
            c = db.get(Campaign, cid)
            if not c:
                return
            p = c.product
            c.status, c.status_reason = "generating", None
            c.landing_url = resolve_landing(p, c)
            db.commit()
            add_event(db, cid, "orchestrator", "بدء خط الإنتاج" + (" (إعادة فحص)" if only_finalize else ""))

            if not only_finalize:
                if not (c.custom_copy and c.copies):
                    c.strategy = await self.strategist.run(db, c, p)
                    db.commit()
                    c.copies = await self.copywriter.run(db, c, p, c.strategy)
                    db.commit()
                else:
                    add_event(db, cid, "orchestrator", "استُخدمت النصوص اليدوية كما هي (تبقى خاضعة للفحص)")

            result = await self.compliance.run(db, c, p)
            # automatic rewrite loop - only for AI/template copy, never touch the user's own text
            revisions = 0
            while not result["passed"] and not c.custom_copy and revisions < MAX_COPY_REVISIONS:
                text_issues = [i for i in result["issues"] if i["severity"] == "critical" and i.get("country")]
                if not text_issues:
                    break
                revisions += 1
                add_event(db, cid, "orchestrator", f"إعادة كتابة النصوص (محاولة {revisions}) بعد ملاحظات الامتثال", "decision")
                c.copies = await self.copywriter.run(db, c, p, c.strategy, feedback=text_issues)
                db.commit()
                result = await self.compliance.run(db, c, p)

            c.compliance = result
            db.commit()
            if not result["passed"]:
                c.status = "blocked"
                c.status_reason = "؛ ".join(i["message"] for i in result["issues"] if i["severity"] == "critical")[:500]
                db.commit()
                add_event(db, cid, "orchestrator", "الحملة محجوبة: عدّل النصوص/البيانات ثم أعد الفحص", "warn")
                return

            c.assets = await self.creative.run(db, c, p)
            db.commit()
            result = await self.compliance.run(db, c, p)  # re-check including the produced media
            c.compliance = result
            if not result["passed"]:
                c.status = "blocked"
                c.status_reason = "؛ ".join(i["message"] for i in result["issues"] if i["severity"] == "critical")[:500]
                db.commit()
                return
            c.status, c.status_reason = "pending_review", "بانتظار موافقتك قبل أي صرف"
            db.commit()
            add_event(db, cid, "orchestrator", "الحملة جاهزة للمراجعة", "decision")
            if get_settings().auto_approve:
                add_event(db, cid, "orchestrator", "AUTO_APPROVE مفعّل - موافقة تلقائية", "warn")
                self._busy.discard(cid)
                await self.approve(cid, activate=True, db=db)
        except Exception as e:
            log.exception("pipeline failed for %s", cid)
            db.rollback()
            c = db.get(Campaign, cid)
            if c:
                c.status, c.status_reason = "failed", f"خطأ في خط الإنتاج: {e}"[:500]
                db.commit()
                add_event(db, cid, "orchestrator", f"فشل خط الإنتاج: {e}", "error")
        finally:
            self._busy.discard(cid)
            db.close()

    # ---------------------------------------------------------------- approve
    async def approve(self, cid: str, activate: bool = False, db: Session | None = None) -> Campaign:
        own = db is None
        db = db or SessionLocal()
        try:
            c = db.get(Campaign, cid)
            if not c:
                raise ValueError("الحملة غير موجودة")
            if c.status not in ("pending_review", "failed", "paused") or not (c.compliance or {}).get("passed"):
                raise ValueError("لا يمكن الاعتماد: الحملة ليست في حالة مراجعة أو لم تجتز فحص الامتثال")
            if cid in self._busy:
                raise ValueError("الحملة قيد المعالجة")
            self._busy.add(cid)
            try:
                p = c.product
                if p.track_stock and not p.in_stock:
                    raise ValueError("المنتج نفد من المخزون")
                if c.daily_budget_usd > get_settings().max_daily_budget_usd:
                    raise ValueError("الميزانية اليومية تتجاوز الحد الأقصى المسموح")
                c.approved_at = utcnow()
                c.status = "deploying"
                db.commit()
                add_event(db, cid, "orchestrator", f"اعتُمدت الحملة ({'تشغيل فوري' if activate else 'إنشاء متوقف'})", "decision")
                await self.deployer.run(db, c, p, activate)
            finally:
                self._busy.discard(cid)
            db.refresh(c)
            return c
        finally:
            if own:
                db.close()

    # --------------------------------------------------------- pause / resume
    async def set_active(self, cid: str, active: bool) -> Campaign:
        db = SessionLocal()
        try:
            c = db.get(Campaign, cid)
            if not c:
                raise ValueError("الحملة غير موجودة")
            if not c.external_ids:
                raise ValueError("الحملة لم تُنشر على أي منصة بعد")
            if active:
                if c.status not in ("paused",):
                    raise ValueError("يمكن تشغيل الحملات المتوقفة فقط")
                if c.product.track_stock and not c.product.in_stock:
                    raise ValueError("المنتج نفد من المخزون")
            elif c.status != "live":
                raise ValueError("الحملة ليست قيد التشغيل")
            errors = []
            ext = {k: dict(v) for k, v in c.external_ids.items()}
            for platform in c.external_ids:
                try:
                    await get_adapter(platform).set_active(c, active)
                    ext[platform]["active"] = active
                except PlatformError as e:
                    errors.append(f"{platform}: {e}")
            if errors:
                add_event(db, cid, "orchestrator", "؛ ".join(errors), "error")
                raise ValueError("؛ ".join(errors))
            c.external_ids = ext
            c.status = "live" if active else "paused"
            c.status_reason = None if active else "أوقفتها يدوياً"
            if active and not c.launched_at:
                c.launched_at = utcnow()
            db.commit()
            add_event(db, cid, "orchestrator", "تم التشغيل" if active else "تم الإيقاف", "decision")
            return c
        finally:
            db.close()

    # ---------------------------------------------------------------- optimize
    async def optimize_all(self) -> int:
        db = SessionLocal()
        n = 0
        try:
            for c in db.query(Campaign).filter(Campaign.status == "live").all():
                if c.id in self._busy:
                    continue
                try:
                    await self.optimizer.run(db, c, c.product)
                    n += 1
                except Exception as e:
                    log.exception("optimizer failed for %s", c.id)
                    add_event(db, c.id, "optimizer", f"خطأ: {e}", "error")
        finally:
            db.close()
        return n

    async def refresh_one(self, cid: str) -> list[str]:
        db = SessionLocal()
        try:
            c = db.get(Campaign, cid)
            if not c:
                raise ValueError("الحملة غير موجودة")
            return await self.optimizer.run(db, c, c.product)
        finally:
            db.close()


orchestrator = Orchestrator()
