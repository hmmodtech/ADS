import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agents import orchestrator
from .api import campaigns, products, store, system
from .config import get_settings
from .db import init_db
from .security import get_admin_key, require_api_key
from .services.scheduler import scheduler_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
settings = get_settings()
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    key = get_admin_key()
    settings.media_dir  # ensure dirs exist
    (settings.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
    log.info("=" * 60)
    log.info("Mode: %s | Gemini: %s", settings.platform_mode, "ON" if settings.llm_enabled else "OFF (template mode)")
    if not settings.admin_api_key:
        log.info("Dashboard access key (also in %s): %s", settings.data_dir / ".admin_key", key)
    log.info("=" * 60)
    task = asyncio.create_task(scheduler_loop(orchestrator)) if settings.scheduler_enabled else None
    yield
    if task:
        task.cancel()


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_list, allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["X-API-Key", "Content-Type"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


for r in (system.router, products.router, campaigns.router, store.router):
    app.include_router(r, dependencies=[Depends(require_api_key)])

(settings.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")
app.mount("/uploads", StaticFiles(directory=settings.data_dir / "uploads"), name="uploads")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/health")
def health():
    return {"status": "ok", "version": settings.version, "mode": settings.platform_mode}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")
