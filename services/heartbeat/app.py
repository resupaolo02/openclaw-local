"""
Heartbeat Service — View and trigger OpenClaw heartbeat system.
Delegates Docker exec to core-api.
"""

import asyncio
import datetime
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

CORE_API  = os.getenv("CORE_API_URL", "http://core-api:8000")
WORKSPACE = Path("/workspace")

_http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    _http_client = httpx.AsyncClient(base_url=CORE_API, timeout=30.0)
    yield
    await _http_client.aclose()


app = FastAPI(title="Heartbeat Service", lifespan=lifespan)

# Filter health-check noise from access logs
class _HealthFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "/api/health" not in msg

logging.getLogger("uvicorn.access").addFilter(_HealthFilter())
logger = logging.getLogger("heartbeat")


async def _core_post(path: str, body: dict | None = None) -> dict:
    assert _http_client is not None
    r = await _http_client.post(path, json=body or {})
    return r.json()


def get_heartbeat_data() -> dict:
    out: dict[str, Any] = {
        "state":         {},
        "tasks":         [],
        "last_event":    None,
        "running":       False,
        "running_tasks": [],
    }
    state_file = WORKSPACE / "memory" / "heartbeat-state.json"
    if state_file.exists():
        try:
            out["state"] = json.loads(state_file.read_text())
        except Exception:
            pass
    hb_md = WORKSPACE / "HEARTBEAT.md"
    if hb_md.exists():
        try:
            content = hb_md.read_text()
            tasks: list[dict] = []
            current: dict | None = None
            for line in content.splitlines():
                h3 = re.match(r"^###\s+\d+\.\s+(.+)", line)
                if h3:
                    if current:
                        tasks.append(current)
                    current = {"name": h3.group(1).strip(), "bullets": []}
                elif current and re.match(r"^- ", line):
                    current["bullets"].append(line[2:].strip())
            if current:
                tasks.append(current)
            out["tasks"] = tasks
        except Exception:
            pass
    return out


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    html_path = Path("/app/index.html")
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>Heartbeat not found</h1>")


@app.get("/api/heartbeat")
async def heartbeat_status():
    loop = asyncio.get_running_loop()
    data_fut = loop.run_in_executor(None, get_heartbeat_data)

    # Fire both core-api calls concurrently alongside the file read
    async def _fetch_last_event():
        try:
            result = await _core_post("/exec", {"cmd": "openclaw system heartbeat last --json"})
            if result.get("output") and result["output"].strip() not in ("null", ""):
                try:
                    return json.loads(result["output"])
                except Exception:
                    return {"raw": result["output"]}
        except Exception:
            pass
        return None

    async def _fetch_running_tasks():
        try:
            result = await _core_post("/exec", {"cmd": "openclaw tasks list --json --status running"})
            if result.get("output"):
                try:
                    parsed = json.loads(result["output"])
                    return parsed.get("tasks", parsed if isinstance(parsed, list) else [])
                except Exception:
                    pass
        except Exception:
            pass
        return []

    data, last_event, running_tasks = await asyncio.gather(
        data_fut, _fetch_last_event(), _fetch_running_tasks()
    )
    data["last_event"] = last_event
    data["running"] = len(running_tasks) > 0
    data["running_tasks"] = running_tasks[:5]
    return data


@app.post("/api/heartbeat/trigger")
async def heartbeat_trigger():
    result = await _core_post("/exec", {
        "cmd": "openclaw system event --mode now --json "
               "--text 'Manual heartbeat triggered from Command Center'"
    })
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    output = result.get("output", "")
    try:
        parsed = json.loads(output) if output else {}
    except Exception:
        parsed = {"raw": output}
    return {
        "triggered": True,
        "exit_code": result.get("exit_code"),
        "result":    parsed,
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
    }


@app.get("/api/health", include_in_schema=False)
async def health():
    return {"status": "ok"}
