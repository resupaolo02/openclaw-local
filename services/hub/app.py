"""
Hub — Monolithic Command Center for OpenClaw.
Consolidates all microservices into a single FastAPI application.

Mounts:
  /            → Landing page
  /chat/*      → Chat UI + LLM streaming
  /finance/*   → Finance tracker
  /nutrition/* → Nutrition tracker
  /calendar/*  → Google Calendar integration
  /monitor/*   → System dashboard
  /heartbeat/* → Heartbeat system
  (root)       → Core API (containers, exec, sessions, skills, maintenance)
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

# ── Router imports ───────────────────────────────────────────────────────────

from routers import calendar as calendar_mod
from routers import chat as chat_mod
from routers import core as core_mod
from routers import finance as finance_mod
from routers import heartbeat as heartbeat_mod
from routers import monitor as monitor_mod
from routers import nutrition as nutrition_mod


# ── Configuration ────────────────────────────────────────────────────────────

STATIC_DIR = Path("/app/static")
CORS_ORIGIN = os.getenv("CORS_ORIGIN", "https://openclaw-frostbite.duckdns.org")

logger = logging.getLogger("hub")

# Filter health-check noise from access logs
class _HealthFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "/health" not in msg

logging.getLogger("uvicorn.access").addFilter(_HealthFilter())


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all module clients on startup, clean up on shutdown."""
    await heartbeat_mod.init_client()
    await chat_mod.init_clients()
    if hasattr(core_mod, "init_clients"):
        await core_mod.init_clients()
    if hasattr(finance_mod, "init_db"):
        await finance_mod.init_db()
    if hasattr(nutrition_mod, "init_db"):
        await nutrition_mod.init_db()
    if hasattr(nutrition_mod, "init_client"):
        await nutrition_mod.init_client()

    logger.info("Hub started — all modules initialized")
    yield

    await heartbeat_mod.close_client()
    await chat_mod.close_clients()
    if hasattr(core_mod, "close_clients"):
        await core_mod.close_clients()
    if hasattr(nutrition_mod, "close_client"):
        await nutrition_mod.close_client()

    logger.info("Hub shutdown — all clients closed")


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="OpenClaw Hub", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[CORS_ORIGIN, "http://localhost:8000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Mount routers ────────────────────────────────────────────────────────────

# Core-API routes at root level (no prefix) — /health, /containers, /exec, etc.
app.include_router(core_mod.router, tags=["core"])

# Service routers with path prefixes (matching Traefik routes)
app.include_router(chat_mod.router, prefix="/chat", tags=["chat"])
app.include_router(finance_mod.router, prefix="/finance", tags=["finance"])
app.include_router(nutrition_mod.router, prefix="/nutrition", tags=["nutrition"])
app.include_router(calendar_mod.router, prefix="/calendar", tags=["calendar"])
app.include_router(monitor_mod.router, prefix="/monitor", tags=["monitor"])
app.include_router(heartbeat_mod.router, prefix="/heartbeat", tags=["heartbeat"])


# ── Static HTML pages ────────────────────────────────────────────────────────

def _serve_html(service: str) -> HTMLResponse:
    html_path = STATIC_DIR / service / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse(f"<h1>{service} UI not found</h1>", status_code=404)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing_page():
    return _serve_html("landing")


@app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
@app.get("/chat/", response_class=HTMLResponse, include_in_schema=False)
async def chat_page():
    return _serve_html("chat")


@app.get("/finance", response_class=HTMLResponse, include_in_schema=False)
@app.get("/finance/", response_class=HTMLResponse, include_in_schema=False)
async def finance_page():
    return _serve_html("finance")


@app.get("/nutrition", response_class=HTMLResponse, include_in_schema=False)
@app.get("/nutrition/", response_class=HTMLResponse, include_in_schema=False)
async def nutrition_page():
    return _serve_html("nutrition")


@app.get("/calendar", response_class=HTMLResponse, include_in_schema=False)
@app.get("/calendar/", response_class=HTMLResponse, include_in_schema=False)
async def calendar_page():
    return _serve_html("calendar")


@app.get("/monitor", response_class=HTMLResponse, include_in_schema=False)
@app.get("/monitor/", response_class=HTMLResponse, include_in_schema=False)
async def monitor_page():
    return _serve_html("monitor")


@app.get("/heartbeat", response_class=HTMLResponse, include_in_schema=False)
@app.get("/heartbeat/", response_class=HTMLResponse, include_in_schema=False)
async def heartbeat_page():
    return _serve_html("heartbeat")
