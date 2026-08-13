from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import settings
from .db import db
from .services.scheduler import scheduler_service


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"


async def _scheduler_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.scheduler_poll_seconds)
        except asyncio.TimeoutError:
            await asyncio.to_thread(scheduler_service.tick)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.initialize()
    stop = asyncio.Event()
    task = asyncio.create_task(_scheduler_loop(stop)) if settings.scheduler_enabled else None
    try:
        yield
    finally:
        stop.set()
        if task:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="产品与项目经理周报助手",
    version="0.1.0",
    root_path=settings.normalized_base_path,
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(router)
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
