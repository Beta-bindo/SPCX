from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import validate_production_settings
from app.database import init_db
from app.routes import admin, client


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_settings()
    init_db()
    yield


app = FastAPI(title="TradeAssistant License Server", version="1.0.0", lifespan=lifespan)
app.include_router(client.router)
app.include_router(admin.router)

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


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


@app.get("/admin/dashboard", response_class=HTMLResponse)
def dashboard_page() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "dashboard.html", media_type="text/html; charset=utf-8")


@app.get("/admin/audit", response_class=HTMLResponse)
def audit_page() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "audit.html", media_type="text/html; charset=utf-8")
