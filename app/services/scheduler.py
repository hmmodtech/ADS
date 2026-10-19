"""Tiny in-process scheduler: periodic optimizer pass + optional store sync. No external broker needed."""
from __future__ import annotations

import asyncio
import logging
import time

from ..config import get_settings
from ..db import SessionLocal

log = logging.getLogger("scheduler")


async def scheduler_loop(orchestrator) -> None:
    s = get_settings()
    last_opt = last_sync = time.monotonic()
    while True:
        await asyncio.sleep(60)
        now = time.monotonic()
        try:
            if s.optimizer_interval_min > 0 and now - last_opt >= s.optimizer_interval_min * 60:
                last_opt = now
                n = await orchestrator.optimize_all()
                log.info("optimizer pass finished (%s live campaigns)", n)
            if s.store_sync_interval_min > 0 and now - last_sync >= s.store_sync_interval_min * 60:
                last_sync = now
                from . import store

                db = SessionLocal()
                try:
                    if store.get_config(db):
                        r = await store.sync(db)
                        log.info("store sync: %s", r)
                finally:
                    db.close()
        except Exception:
            log.exception("scheduler iteration failed")
