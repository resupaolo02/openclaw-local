"""Monitor router — system dashboard with host metrics, GPU, and container stats."""

import asyncio
import os
import subprocess
import time
import threading
from pathlib import Path
from typing import Any

import httpx
import psutil
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

HOST_PROC = Path("/host/proc")

_state_lock = threading.Lock()
_NET_SNAP: dict[str, Any] = {}
_CPU_SNAP: dict[str, Any] = {}

STATUS_CACHE_TTL = 10
_STATUS_CACHE: dict | None = None
_STATUS_CACHE_TS: float = 0.0


def _safe(fn, fallback=None):
    try:
        return fn()
    except Exception:
        return fallback


def _take_cpu_snapshot() -> tuple[int, int]:
    try:
        vals = [int(x) for x in (HOST_PROC / "stat").read_text().splitlines()[0].split()[1:8]]
        total, idle = sum(vals), vals[3] + vals[4]
        with _state_lock:
            _CPU_SNAP["total"] = total
            _CPU_SNAP["idle"] = idle
            _CPU_SNAP["ts"] = time.monotonic()
        return total, idle
    except Exception:
        return 0, 0


def get_host_metrics() -> dict:
    out: dict[str, Any] = {}

    # RAM
    try:
        mem: dict[str, int] = {}
        for line in (HOST_PROC / "meminfo").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                mem[parts[0].rstrip(":")] = int(parts[1])
        total_kb = mem.get("MemTotal", 0)
        avail_kb = mem.get("MemAvailable", 0)
        used_kb  = total_kb - avail_kb
        out["mem_total_gb"] = round(total_kb / 1048576, 1)
        out["mem_used_gb"]  = round(used_kb  / 1048576, 1)
        out["mem_percent"]  = round(used_kb / total_kb * 100, 1) if total_kb else 0
    except Exception:
        out.update({"mem_total_gb": 0, "mem_used_gb": 0, "mem_percent": 0})

    # CPU %
    try:
        with _state_lock:
            prev_total = _CPU_SNAP.get("total", 0)
            prev_idle  = _CPU_SNAP.get("idle", 0)
        new_total, new_idle = _take_cpu_snapshot()
        dt = new_total - prev_total
        if dt > 0 and prev_total > 0:
            out["cpu_percent"] = round((1 - (new_idle - prev_idle) / dt) * 100, 1)
        else:
            out["cpu_percent"] = 0.0
    except Exception:
        out["cpu_percent"] = _safe(lambda: psutil.cpu_percent(interval=0), 0.0)

    # Load average
    try:
        load = (HOST_PROC / "loadavg").read_text().split()
        out["cpu_load_1m"]  = float(load[0])
        out["cpu_load_5m"]  = float(load[1])
        out["cpu_load_15m"] = float(load[2])
    except Exception:
        la = _safe(os.getloadavg, (0, 0, 0))
        out.update({"cpu_load_1m": la[0], "cpu_load_5m": la[1], "cpu_load_15m": la[2]})

    # CPU count & temperature
    try:
        out["cpu_count"] = (HOST_PROC / "cpuinfo").read_text().count("processor\t:")
    except Exception:
        out["cpu_count"] = _safe(psutil.cpu_count, 0)
    out["cpu_temp"] = "—"
    try:
        temps = psutil.sensors_temperatures()
        for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
            entries = temps.get(key, [])
            if entries:
                out["cpu_temp"] = f"{entries[0].current:.0f}°C"
                break
    except Exception:
        pass

    # Disk
    try:
        disk = psutil.disk_usage("/")
        out["disk_total_gb"] = round(disk.total / 1e9, 1)
        out["disk_used_gb"]  = round(disk.used  / 1e9, 1)
        out["disk_percent"]  = disk.percent
    except Exception:
        out.update({"disk_total_gb": 0, "disk_used_gb": 0, "disk_percent": 0})

    # Network I/O
    try:
        now_ts   = time.monotonic()
        nets     = psutil.net_io_counters(pernic=True)
        rx_total = sum(n.bytes_recv for iface, n in nets.items() if iface != "lo")
        tx_total = sum(n.bytes_sent for iface, n in nets.items() if iface != "lo")
        rx_kbps  = tx_kbps = 0.0
        with _state_lock:
            prev = _NET_SNAP.get("host")
            if prev:
                dt = now_ts - prev["ts"]
                if dt > 0:
                    rx_kbps = max(round((rx_total - prev["rx"]) / dt / 1024, 1), 0)
                    tx_kbps = max(round((tx_total - prev["tx"]) / dt / 1024, 1), 0)
            _NET_SNAP["host"] = {"ts": now_ts, "rx": rx_total, "tx": tx_total}
        out["net_rx_gb"]    = round(rx_total / 1e9, 3)
        out["net_tx_gb"]    = round(tx_total / 1e9, 3)
        out["net_rx_kbps"]  = rx_kbps
        out["net_tx_kbps"]  = tx_kbps
        out["net_interfaces"] = [
            {"name": iface, "rx_mb": round(n.bytes_recv / 1e6, 1), "tx_mb": round(n.bytes_sent / 1e6, 1)}
            for iface, n in sorted(nets.items()) if iface != "lo"
        ]
    except Exception:
        out.update({"net_rx_gb": 0, "net_tx_gb": 0,
                    "net_rx_kbps": 0, "net_tx_kbps": 0, "net_interfaces": []})

    return out


def get_gpu() -> list[dict]:
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,utilization.gpu,memory.used,memory.total,"
                         "temperature.gpu,power.draw,power.limit,clocks.current.sm",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return [{"available": False, "error": result.stderr.strip() or "nvidia-smi failed"}]
        gpus = []
        for line in result.stdout.strip().splitlines():
            p = [x.strip() for x in line.split(",")]
            if len(p) >= 6:
                gpus.append({
                    "available":     True,
                    "name":          p[0],
                    "util_pct":      _safe(lambda: float(p[1]), 0.0),
                    "vram_used_mb":  _safe(lambda: int(p[2]), 0),
                    "vram_total_mb": _safe(lambda: int(p[3]), 0),
                    "vram_percent":  _safe(lambda: round(int(p[2]) / int(p[3]) * 100, 1), 0),
                    "temp_c":        _safe(lambda: float(p[4]), 0.0),
                    "power_draw_w":  _safe(lambda: float(p[5]), 0.0),
                    "power_limit_w": _safe(lambda: float(p[6]), 0.0) if len(p) > 6 else None,
                    "clock_mhz":     _safe(lambda: int(p[7]), 0)     if len(p) > 7 else None,
                })
        return gpus if gpus else [{"available": False, "error": "No GPU output"}]
    except FileNotFoundError:
        return [{"available": False, "error": "nvidia-smi not found"}]
    except Exception as e:
        return [{"available": False, "error": str(e)}]


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/api/status")
async def status():
    import datetime
    global _STATUS_CACHE, _STATUS_CACHE_TS
    now = time.monotonic()
    if _STATUS_CACHE and (now - _STATUS_CACHE_TS) < STATUS_CACHE_TTL:
        return _STATUS_CACHE

    loop = asyncio.get_running_loop()

    # In the monolith, core-api routes are on the same server
    async def fetch_core(path):
        async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=30.0) as c:
            r = await c.get(path)
            return r.json()

    # Health checks — now all services are on the same hub
    async def check_hub_health() -> dict:
        """All services are in the same process — just return True."""
        return {
            "hub": True,
            "openclaw": True,  # checked via containers
        }

    service_health = (await check_hub_health())

    llm, sessions, containers, host, gpu, cstats = await asyncio.gather(
        fetch_core("/llm/status"),
        fetch_core("/sessions/overview"),
        fetch_core("/containers"),
        loop.run_in_executor(None, get_host_metrics),
        loop.run_in_executor(None, get_gpu),
        fetch_core("/containers/stats"),
    )

    openclaw_ok = any(c.get("running") for c in containers if c.get("name") == "openclaw")
    has_errors  = bool(sessions.get("recent_errors"))

    if openclaw_ok and not has_errors:
        overall = "healthy"
    elif openclaw_ok:
        overall = "degraded"
    else:
        overall = "down"

    response = {
        "timestamp":       datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "overall":         overall,
        "containers":      containers,
        "cstats":          cstats,
        "llm":             llm,
        "sessions":        sessions,
        "host":            host,
        "gpu":             gpu,
        "service_health":  service_health,
    }
    _STATUS_CACHE = response
    _STATUS_CACHE_TS = now
    return response


@router.get("/api/health", include_in_schema=False)
async def health():
    return {"status": "ok"}
