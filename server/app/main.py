from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.config import validate_production_settings
from app.database import init_db
from app.routes import admin, client

app = FastAPI(title="TradeAssistant License Server", version="1.0.0")
app.include_router(client.router)
app.include_router(admin.router)

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def on_startup() -> None:
    validate_production_settings()
    init_db()


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/admin", response_class=HTMLResponse)
def admin_page() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "admin.html", media_type="text/html; charset=utf-8")


@app.get("/admin/trades", response_class=HTMLResponse)
def trades_page() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "trades.html", media_type="text/html; charset=utf-8")


@app.get("/admin/positions", response_class=HTMLResponse)
def positions_page() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "positions.html", media_type="text/html; charset=utf-8")
