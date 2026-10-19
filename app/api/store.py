from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import StoreConnectIn
from ..services import store

router = APIRouter(prefix="/api/store", tags=["store"])


@router.get("")
def status(db: Session = Depends(get_db)):
    return store.public_view(store.get_config(db))


@router.post("/connect")
async def connect(body: StoreConnectIn, db: Session = Depends(get_db)):
    cfg = {"type": body.type, "url": body.url, "api_key": body.api_key, "currency": body.currency.upper()}
    try:
        n = await store.test_connection(cfg)
    except store.StoreError as e:
        raise HTTPException(400, str(e))
    store.save_config(db, cfg)
    return {**store.public_view(cfg), "products_found": n}


@router.post("/sync")
async def sync(db: Session = Depends(get_db)):
    try:
        return await store.sync(db)
    except store.StoreError as e:
        raise HTTPException(400, str(e))


@router.delete("")
def disconnect(db: Session = Depends(get_db)):
    store.clear_config(db)
    return {"connected": False}
