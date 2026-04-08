"""
OpenClaw Command Center — Backend
Monitoring, heartbeat management, file uploads, and Frostbite chat interface.
"""

import asyncio
import datetime
import io
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, AsyncGenerator, List, Optional

import docker
import httpx
import psutil
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from contextlib import asynccontextmanager

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://llama:8080")

@asynccontextmanager
async def lifespan(app):
    global _http_client
    _http_client = httpx.AsyncClient(base_url=LLM_BASE_URL, timeout=30.0)
    logger.info("HTTP client ready → %s", LLM_BASE_URL)
    yield
    await _http_client.aclose()

app = FastAPI(title="OpenClaw Command Center", lifespan=lifespan)
logger = logging.getLogger("command-center")

# Persistent HTTP client for LLM (set in lifespan)
_http_client: httpx.AsyncClient | None = None

OPENCLAW_DATA = Path("/openclaw-data")
SESSIONS_DIR  = OPENCLAW_DATA / "agents/main/sessions"
AGENTS_DIR    = OPENCLAW_DATA / "agents"
WORKSPACE     = OPENCLAW_DATA / "workspace"
CUSTOM_SKILLS = Path("/custom-skills")
HOST_PROC     = Path("/host/proc")

CORS_ORIGIN      = os.getenv("CORS_ORIGIN", "https://openclaw-frostbite.duckdns.org")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

_NET_SNAP:  dict[str, Any] = {}
_CNET_SNAP: dict[str, Any] = {}
_CPU_SNAP:  dict[str, Any] = {}

# Thread locks
import threading
_state_lock  = threading.Lock()
_prompt_lock = threading.Lock()

# Status endpoint cache (seconds)
STATUS_CACHE_TTL = 10
_STATUS_CACHE: dict | None = None
_STATUS_CACHE_TS: float = 0.0

# ── System prompt cache ───────────────────────────────────────────────────────
_SYS_PROMPT_CACHE: str | None = None
_SYS_PROMPT_TS: float = 0.0

def _safe(fn, fallback=None):
    try:
        return fn()
    except Exception:
        return fallback

# ── Singleton Docker client ──────────────────────────────────────────────────
_docker_client = None
_docker_lock = threading.Lock()

def _get_docker():
    """Return a singleton Docker client, reconnecting if needed."""
    global _docker_client
    with _docker_lock:
        if _docker_client is None:
            _docker_client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
        try:
            _docker_client.ping()
        except Exception:
            _docker_client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
        return _docker_client

def _uptime_str(started_at_iso: str | None) -> str:
    if not started_at_iso:
        return "—"
    try:
        ts = started_at_iso[:26].rstrip("Z").rstrip("0").rstrip(".")
        started = datetime.datetime.fromisoformat(ts)
        delta = datetime.datetime.utcnow() - started
        s = int(delta.total_seconds())
        if s < 0:
            return "just started"
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        if h:   return f"{h}h {m}m"
        if m:   return f"{m}m {sec}s"
        return f"{sec}s"
    except Exception:
        return "—"

# ── Containers (status / uptime) ──────────────────────────────────────────────

def get_containers() -> list[dict]:
    results = []
    try:
        client = _get_docker()
        for name in ["openclaw", "llama-server"]:
            try:
                c = client.containers.get(name)
                attrs = c.attrs
                state = attrs.get("State", {})
                out = {
                    "name":          name,
                    "status":        state.get("Status", "unknown"),
                    "running":       state.get("Running", False),
                    "started_at":    _uptime_str(state.get("StartedAt")),
                    "restart_count": attrs.get("RestartCount", 0),
                    "image":         attrs.get("Config", {}).get("Image", "—"),
                    "health":        state.get("Health", {}).get("Status", "none"),
                }
            except docker.errors.NotFound:
                out = {"name": name, "status": "not found", "running": False,
                       "started_at": "—", "restart_count": 0, "image": "—", "health": "none"}
            results.append(out)
    except Exception as e:
        logger.error("get_containers failed: %s", e)
        results.append({"error": str(e)})
    return results

# ── Per-container deep stats ──────────────────────────────────────────────────

def get_container_deep_stats() -> list[dict]:
    results = []
    now_ts = time.monotonic()
    try:
        client = _get_docker()
        for name in ["openclaw", "llama-server"]:
            try:
                c = client.containers.get(name)
                if c.status != "running":
                    results.append({"name": name, "running": False})
                    continue
                s = c.stats(stream=False)

                # CPU %
                cpu_pct = 0.0
                try:
                    cd = (s["cpu_stats"]["cpu_usage"]["total_usage"]
                          - s["precpu_stats"]["cpu_usage"]["total_usage"])
                    sd = (s["cpu_stats"]["system_cpu_usage"]
                          - s["precpu_stats"]["system_cpu_usage"])
                    nc = (s["cpu_stats"].get("online_cpus")
                          or len(s["cpu_stats"]["cpu_usage"].get("percpu_usage") or [1]))
                    if sd > 0:
                        cpu_pct = round(cd / sd * nc * 100.0, 1)
                except Exception:
                    pass

                # Memory (RSS, exclude page cache)
                mem_usage_mb = mem_limit_mb = mem_pct = 0.0
                try:
                    ms    = s["memory_stats"]
                    cache = ((ms.get("stats") or {}).get("inactive_file")
                             or (ms.get("stats") or {}).get("cache") or 0)
                    mem_usage_mb = round((ms["usage"] - cache) / 1024 / 1024, 1)
                    mem_limit_mb = round(ms["limit"] / 1024 / 1024, 0)
                    mem_pct      = round(mem_usage_mb / (mem_limit_mb or 1) * 100, 1)
                except Exception:
                    pass

                # Network I/O
                net_rx_b = net_tx_b = 0
                try:
                    for v in (s.get("networks") or {}).values():
                        net_rx_b += v.get("rx_bytes", 0)
                        net_tx_b += v.get("tx_bytes", 0)
                except Exception:
                    pass
                net_rx_kbps = net_tx_kbps = 0.0
                with _state_lock:
                    prev_net = _CNET_SNAP.get(name)
                    if prev_net:
                        dt = now_ts - prev_net["ts"]
                        if dt > 0:
                            net_rx_kbps = max(round((net_rx_b - prev_net["rx"]) / dt / 1024, 1), 0)
                            net_tx_kbps = max(round((net_tx_b - prev_net["tx"]) / dt / 1024, 1), 0)
                    _CNET_SNAP[name] = {"ts": now_ts, "rx": net_rx_b, "tx": net_tx_b}

                # Block I/O
                blk_read_mb = blk_write_mb = 0.0
                try:
                    for entry in (s["blkio_stats"].get("io_service_bytes_recursive") or []):
                        op = entry.get("op", "").lower()
                        if   op == "read":  blk_read_mb  += entry.get("value", 0)
                        elif op == "write": blk_write_mb += entry.get("value", 0)
                    blk_read_mb  = round(blk_read_mb  / 1024 / 1024, 1)
                    blk_write_mb = round(blk_write_mb / 1024 / 1024, 1)
                except Exception:
                    pass

                pids = _safe(lambda: s.get("pids_stats", {}).get("current", "—"), "—")

                results.append({
                    "name":         name,
                    "running":      True,
                    "cpu_pct":      cpu_pct,
                    "mem_usage_mb": mem_usage_mb,
                    "mem_limit_mb": int(mem_limit_mb),
                    "mem_pct":      mem_pct,
                    "net_rx_mb":    round(net_rx_b / 1024 / 1024, 2),
                    "net_tx_mb":    round(net_tx_b / 1024 / 1024, 2),
                    "net_rx_kbps":  net_rx_kbps,
                    "net_tx_kbps":  net_tx_kbps,
                    "blk_read_mb":  blk_read_mb,
                    "blk_write_mb": blk_write_mb,
                    "pids":         pids,
                })
            except docker.errors.NotFound:
                results.append({"name": name, "running": False})
    except Exception as e:
        logger.error("get_container_deep_stats failed: %s", e)
        results.append({"error": str(e)})
    return results

# ── LLM server ────────────────────────────────────────────────────────────────

async def get_llm_status() -> dict:
    """Query LLM server health, model info, and context props via persistent client."""
    base = str(_http_client.base_url) if _http_client else "—"
    result: dict[str, Any] = {"base_url": base, "healthy": False, "model": "—"}
    if not _http_client:
        result["error"] = "HTTP client not initialized"
        return result
    try:
        try:
            r = await _http_client.get("/health")
            result["healthy"] = r.status_code == 200
            body = r.json()
            result["health_status"]    = body.get("status", "—")
            result["slots_idle"]       = body.get("slots_idle", "—")
            result["slots_processing"] = body.get("slots_processing", "—")
        except Exception:
            result["health_status"] = "unreachable"
        try:
            r2 = await _http_client.get("/v1/models")
            models = r2.json().get("data", [])
            if models:
                result["model"] = models[0].get("id", "—")
        except Exception:
            pass
        try:
            r3 = await _http_client.get("/props")
            props = r3.json()
            result["n_ctx"]     = props.get("n_ctx", "—")
            result["n_predict"] = props.get("n_predict", "—")
        except Exception:
            pass
    except Exception as e:
        logger.error("get_llm_status failed: %s", e)
        result["error"] = str(e)
    return result


# ── CPU snapshot (non-blocking) ───────────────────────────────────────────────

def _take_cpu_snapshot() -> tuple[int, int]:
    """Read /proc/stat and return (total_ticks, idle_ticks). Stores in _CPU_SNAP."""
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


# ── Host system metrics ──────────────────────────────────────────────────────

def get_host_metrics() -> dict:
    out: dict[str, Any] = {}

    # RAM from /host/proc/meminfo (true host, not cgroup-limited)
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

    # CPU % using delta from previous snapshot (NON-BLOCKING — no sleep)
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

    # Disk (container root — approximates host when no /host mount)
    try:
        disk = psutil.disk_usage("/")
        out["disk_total_gb"] = round(disk.total / 1e9, 1)
        out["disk_used_gb"]  = round(disk.used  / 1e9, 1)
        out["disk_percent"]  = disk.percent
    except Exception:
        out.update({"disk_total_gb": 0, "disk_used_gb": 0, "disk_percent": 0})

    # Network I/O with per-poll KB/s rate
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

# ── GPU ───────────────────────────────────────────────────────────────────────

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

# ── Sessions ──────────────────────────────────────────────────────────────────

def get_sessions() -> dict:
    out: dict[str, Any] = {
        "agents": [], "active_sessions": 0, "total_sessions": 0,
        "recent_errors": [], "last_activity": "—",
    }
    if not SESSIONS_DIR.exists():
        return out
    try:
        for agent_dir in sorted(AGENTS_DIR.iterdir()):
            if agent_dir.name.startswith("_") or not agent_dir.is_dir():
                continue
            for p in [agent_dir / "agent.json", agent_dir / "agent" / "agent.json"]:
                if p.exists():
                    try:
                        out["agents"].append(json.loads(p.read_text()).get("name", agent_dir.name))
                    except Exception:
                        out["agents"].append(agent_dir.name)
                    break
            else:
                out["agents"].append(agent_dir.name)

        sessions_path = AGENTS_DIR / "main" / "sessions"
        if not sessions_path.exists():
            return out
        all_files = list(sessions_path.glob("*.jsonl"))
        active = [f for f in all_files
                  if not any(x in f.name for x in [".reset.", ".deleted."])]
        out["total_sessions"]  = len(all_files)
        out["active_sessions"] = len(active)

        recent_errors: list[dict] = []
        last_ts = 0.0
        for sf in sorted(active, key=lambda f: f.stat().st_mtime, reverse=True)[:5]:
            try:
                for raw in sf.read_text(errors="replace").strip().split("\n")[-30:]:
                    try:
                        entry = json.loads(raw)
                    except Exception:
                        continue
                    ts_str = entry.get("timestamp", "")
                    try:
                        ts_epoch = datetime.datetime.fromisoformat(ts_str.rstrip("Z")).timestamp()
                        if ts_epoch > last_ts:
                            last_ts = ts_epoch
                            out["last_activity"] = ts_str[:19].replace("T", " ") + " UTC"
                    except Exception:
                        pass
                    if entry.get("type") == "message":
                        err = entry.get("message", {}).get("errorMessage", "")
                        if err:
                            recent_errors.append({
                                "time":    ts_str[:19].replace("T", " "),
                                "session": sf.stem[:8],
                                "error":   err[:120],
                            })
            except Exception:
                continue
        seen: set[str] = set()
        out["recent_errors"] = [
            e for e in recent_errors
            if e["error"][:60] not in seen and not seen.add(e["error"][:60])  # type: ignore
        ][:5]
    except Exception as e:
        out["error"] = str(e)
    return out


# ── Session browser ───────────────────────────────────────────────────────────

def list_sessions_data() -> list[dict]:
    """Return metadata for all active sessions, newest-first."""
    if not SESSIONS_DIR.exists():
        return []
    results = []
    active_files = [
        f for f in SESSIONS_DIR.glob("*.jsonl")
        if not any(x in f.name for x in [".reset.", ".deleted."])
    ]
    for sf in sorted(active_files, key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            lines = sf.read_text(errors="replace").strip().splitlines()
            created = "—"
            preview = ""
            msg_count = 0
            for line in lines:
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("type") == "session":
                    ts = entry.get("timestamp", "")
                    if ts:
                        created = ts[:19].replace("T", " ") + " UTC"
                elif entry.get("type") == "message":
                    msg  = entry.get("message", {})
                    role = msg.get("role", "")
                    raw  = msg.get("content", "")
                    text = (
                        " ".join(c.get("text", "") for c in raw if c.get("type") == "text").strip()
                        if isinstance(raw, list) else str(raw).strip()
                    )
                    if role == "user" and text and not preview:
                        preview = text[:120]
                    if role in ("user", "assistant"):
                        msg_count += 1
            mtime  = sf.stat().st_mtime
            results.append({
                "id":            sf.stem,
                "created":       created,
                "modified":      datetime.datetime.utcfromtimestamp(mtime).strftime("%Y-%m-%d %H:%M UTC"),
                "modified_ts":   mtime,
                "message_count": msg_count,
                "preview":       preview or "(no user messages)",
            })
        except Exception:
            pass
    return results


def get_session_messages(session_id: str) -> list[dict]:
    """Return plain role/content pairs from a session file."""
    sf = SESSIONS_DIR / f"{session_id}.jsonl"
    if not sf.exists():
        return []
    messages = []
    for line in sf.read_text(errors="replace").strip().splitlines():
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if entry.get("type") != "message":
            continue
        msg  = entry.get("message", {})
        role = msg.get("role", "")
        if role not in ("user", "assistant"):
            continue
        raw  = msg.get("content", "")
        text = (
            "\n".join(c.get("text", "") for c in raw if c.get("type") == "text").strip()
            if isinstance(raw, list) else str(raw).strip()
        )
        if text:
            messages.append({"role": role, "content": text})
    return messages


# ── Skills ────────────────────────────────────────────────────────────────────

def _parse_skill_frontmatter(text: str) -> dict[str, str]:
    """Extract name, description, emoji from SKILL.md frontmatter."""
    meta: dict[str, str] = {"name": "", "description": "", "emoji": "🤖"}
    text = re.sub(r"^```[a-z]*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```\s*$", "", text, flags=re.MULTILINE)
    m = re.search(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return meta
    body = m.group(1)
    for line in body.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip().strip('"').strip("'")
        if key == "name":
            meta["name"] = val
        elif key == "description":
            meta["description"] = val[:200]
        elif key == "metadata":
            em = re.search(r'"emoji"\s*:\s*"([^"]+)"', line)
            if em:
                meta["emoji"] = em.group(1)
    return meta


def get_skills_data() -> list[dict]:
    skills = []
    search_dirs = []
    if CUSTOM_SKILLS.exists():
        search_dirs.append(CUSTOM_SKILLS)
    ws_skills = WORKSPACE / "custom-skills"
    if ws_skills.exists():
        search_dirs.append(ws_skills)
    seen: set[str] = set()
    for base in search_dirs:
        for skill_dir in sorted(base.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                meta = _parse_skill_frontmatter(skill_md.read_text())
                name = meta["name"] or skill_dir.name
                if name not in seen:
                    seen.add(name)
                    skills.append({
                        "name":        name,
                        "description": meta["description"],
                        "emoji":       meta["emoji"],
                        "dir":         skill_dir.name,
                    })
            except Exception:
                pass
    return skills


def build_system_prompt() -> str:
    global _SYS_PROMPT_CACHE, _SYS_PROMPT_TS
    with _prompt_lock:
        if _SYS_PROMPT_CACHE and (time.monotonic() - _SYS_PROMPT_TS) < 60:
            return _SYS_PROMPT_CACHE
        parts: list[str] = []
        for fname in ["IDENTITY.md", "SOUL.md", "USER.md"]:
            p = WORKSPACE / fname
            if p.exists():
                try:
                    parts.append(p.read_text())
                except Exception:
                    pass
        skills = get_skills_data()
        if skills:
            lines = ["## Your Available Skills\n"]
            for s in skills:
                lines.append(f"- **{s['emoji']} {s['name']}**: {s['description'][:120]}")
            parts.append("\n".join(lines))
        prompt = "\n\n---\n\n".join(parts)
        _SYS_PROMPT_CACHE = prompt
        _SYS_PROMPT_TS = time.monotonic()
        return prompt


# ── Heartbeat ─────────────────────────────────────────────────────────────────

def _exec_in_openclaw(cmd: str) -> dict[str, Any]:
    """Run a command inside the 'openclaw' container via Docker socket."""
    try:
        client = _get_docker()
        container = client.containers.get("openclaw")
        exit_code, output = container.exec_run(cmd, demux=False, stream=False)
        raw = output.decode("utf-8", errors="replace").strip() if output else ""
        return {"exit_code": exit_code, "output": raw}
    except Exception as e:
        logger.error("_exec_in_openclaw failed (%s): %s", cmd[:40], e)
        return {"exit_code": -1, "output": "", "error": str(e)}


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
    result = _exec_in_openclaw("openclaw system heartbeat last --json")
    if result.get("output") and result["output"].strip() not in ("null", ""):
        try:
            out["last_event"] = json.loads(result["output"])
        except Exception:
            out["last_event"] = {"raw": result["output"]}
    tasks_result = _exec_in_openclaw("openclaw tasks list --json --status running")
    if tasks_result.get("output"):
        try:
            parsed = json.loads(tasks_result["output"])
            items = parsed.get("tasks", parsed if isinstance(parsed, list) else [])
            out["running"] = len(items) > 0
            out["running_tasks"] = items[:5]
        except Exception:
            pass
    return out


# ── File extraction ───────────────────────────────────────────────────────────

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".csv",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".xml",
    ".sh", ".bash", ".zsh", ".fish", ".sql", ".toml", ".ini",
    ".cfg", ".conf", ".log", ".env", ".gitignore",
    ".rs", ".go", ".java", ".c", ".cpp", ".h", ".hpp", ".rb", ".php",
}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx"}


def extract_text_from_file(filename: str, content: bytes) -> tuple[str, str]:
    ext = Path(filename).suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return content.decode("utf-8", errors="replace"), "text"
    elif ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            pages = [p.extract_text() for p in reader.pages if p.extract_text()]
            if not pages:
                raise ValueError("PDF has no extractable text (may be scanned/image-only)")
            return "\n\n".join(pages), "PDF"
        except ImportError:
            raise ValueError("PDF extraction library not available")
        except Exception as e:
            raise ValueError(f"PDF extraction failed: {e}")
    elif ext == ".docx":
        try:
            from docx import Document
            doc = Document(io.BytesIO(content))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            if not paragraphs:
                raise ValueError("DOCX has no extractable text")
            return "\n\n".join(paragraphs), "DOCX"
        except ImportError:
            raise ValueError("DOCX extraction library not available")
        except Exception as e:
            raise ValueError(f"DOCX extraction failed: {e}")
    else:
        raise ValueError(
            f"Unsupported file type '{ext}'. Supported: plain-text files, .pdf, .docx"
        )


# ── Chat streaming ────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    file_text: Optional[str] = None
    file_name: Optional[str] = None


async def _stream_llm(messages: list[dict]) -> AsyncGenerator[str, None]:
    """Stream chat completions from the LLM via the persistent HTTP client."""
    if not _http_client:
        yield f"data: {json.dumps({'error': 'HTTP client not initialized'})}\n\n"
        return
    payload = {
        "model":       "local",
        "messages":    messages,
        "stream":      True,
        "max_tokens":  2048,
        "temperature": 0.7,
    }
    try:
        async with _http_client.stream(
            "POST",
            "/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                yield f"data: {json.dumps({'error': f'LLM error {resp.status_code}: {body.decode()[:200]}'})}\n\n"
                return
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = line[6:]
                if chunk.strip() == "[DONE]":
                    yield "data: [DONE]\n\n"
                    return
                yield f"data: {chunk}\n\n"
    except httpx.ConnectError:
        yield f"data: {json.dumps({'error': 'Cannot reach LLM — is llama-server running?'})}\n\n"
    except Exception as e:
        logger.error("LLM streaming error: %s", e)
        yield f"data: {json.dumps({'error': str(e)})}\n\n"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    html_path = Path("/app/index.html")
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>Dashboard not found</h1>")


@app.get("/api/status")
async def status():
    """Aggregated system status — cached for 10s, parallel data collection."""
    global _STATUS_CACHE, _STATUS_CACHE_TS
    now = time.monotonic()
    if _STATUS_CACHE and (now - _STATUS_CACHE_TS) < STATUS_CACHE_TTL:
        return _STATUS_CACHE

    loop = asyncio.get_event_loop()
    # Run all blocking functions in thread pool concurrently alongside async LLM check
    llm, sessions, containers, host, gpu, cstats = await asyncio.gather(
        get_llm_status(),
        loop.run_in_executor(None, get_sessions),
        loop.run_in_executor(None, get_containers),
        loop.run_in_executor(None, get_host_metrics),
        loop.run_in_executor(None, get_gpu),
        loop.run_in_executor(None, get_container_deep_stats),
    )

    openclaw_ok = any(c.get("running") for c in containers if c.get("name") == "openclaw")
    llama_ok    = any(c.get("running") for c in containers if c.get("name") == "llama-server")
    has_errors  = bool(sessions.get("recent_errors"))

    if openclaw_ok and llama_ok and not has_errors:
        overall = "healthy"
    elif openclaw_ok or llama_ok:
        overall = "degraded"
    else:
        overall = "down"

    response = {
        "timestamp":  datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "overall":    overall,
        "containers": containers,
        "cstats":     cstats,
        "llm":        llm,
        "sessions":   sessions,
        "host":       host,
        "gpu":        gpu,
    }
    _STATUS_CACHE = response
    _STATUS_CACHE_TS = now
    return response


@app.get("/api/heartbeat")
async def heartbeat_status():
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_heartbeat_data)


@app.post("/api/heartbeat/trigger")
async def heartbeat_trigger():
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _exec_in_openclaw,
        "openclaw system event --mode now --json "
        "--text 'Manual heartbeat triggered from Command Center'"
    )
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
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }


@app.get("/api/skills")
async def skills():
    return {"skills": get_skills_data()}


@app.post("/api/chat")
async def chat(request: ChatRequest):
    system_prompt = build_system_prompt()
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    msg_list = [m.model_dump() for m in request.messages]
    if request.file_text and msg_list:
        for i in range(len(msg_list) - 1, -1, -1):
            if msg_list[i]["role"] == "user":
                fname_label = f" ({request.file_name})" if request.file_name else ""
                msg_list[i]["content"] = (
                    f"[Attached file{fname_label}]\n```\n{request.file_text[:12000]}\n```\n\n"
                    + msg_list[i]["content"]
                )
                break
    messages.extend(msg_list)
    return StreamingResponse(
        _stream_llm(messages),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": CORS_ORIGIN,
        },
    )


@app.post("/api/chat/upload")
async def chat_upload(file: UploadFile = File(...)):
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content) // 1024 // 1024}MB). Max: {MAX_UPLOAD_BYTES // 1024 // 1024}MB",
        )
    filename = file.filename or "unknown"
    try:
        text, mime_label = extract_text_from_file(filename, content)
        truncated = len(text) > 15000
        return {
            "filename":   filename,
            "type":       mime_label,
            "text":       text[:15000],
            "truncated":  truncated,
            "char_count": len(text),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction error: {e}")


@app.get("/api/supported-files")
async def supported_files():
    return {
        "supported": {
            "text":     sorted(TEXT_EXTENSIONS),
            "document": [".pdf", ".docx"],
        },
        "not_supported": [".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mp3", ".zip", ".exe"],
        "note": (
            f"The local model ({os.environ.get('LLM_MODEL', 'Qwen3.5-9B')}) is text-only. "
            "Images, audio, and video are not supported. "
            "For PDF/DOCX, text is extracted and passed as context."
        ),
    }


@app.get("/api/sessions/list")
async def sessions_list_route():
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, list_sessions_data)
    return {"sessions": data}


@app.get("/api/sessions/{session_id}/messages")
async def session_messages_route(session_id: str):
    if not re.match(r"^[0-9a-f-]{36}$", session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID")
    return {"messages": get_session_messages(session_id)}


@app.delete("/api/sessions/{session_id}")
async def delete_session_route(session_id: str):
    if not re.match(r"^[0-9a-f-]{36}$", session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID")
    # Verify the file exists (via our read-only mount)
    sf = SESSIONS_DIR / f"{session_id}.jsonl"
    if not sf.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    # The openclaw-data volume is read-only here; perform the rename inside
    # the openclaw container (which has write access) via the Docker socket.
    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
    sessions_path = "/home/node/.openclaw/agents/main/sessions"
    src  = f"{sessions_path}/{session_id}.jsonl"
    dest = f"{sessions_path}/{session_id}.jsonl.deleted.{stamp}Z"
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _exec_in_openclaw, f"mv {src} {dest}")
    if result.get("exit_code", -1) != 0:
        detail = result.get("error") or result.get("output") or "mv failed"
        raise HTTPException(status_code=500, detail=detail)
    return {"deleted": True, "id": session_id}


@app.get("/api/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


# ── Calendar — Google Calendar API ────────────────────────────────────────────

try:
    from google.oauth2.credentials import Credentials as GCredentials
    from google.auth.transport.requests import Request as GRequest
    from googleapiclient.discovery import build as _gcal_build
    _GCAL_AVAILABLE = True
except ImportError:
    _GCAL_AVAILABLE = False
    logger.warning("Google Calendar libraries not installed — calendar features disabled")

_GCAL_SCOPES       = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_DATA_FILE = WORKSPACE / "calendar-data.json"
GCAL_TOKEN_FILE    = WORKSPACE / "google-token.json"
PH_TZ              = datetime.timezone(datetime.timedelta(hours=8))

_DAYS   = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
_MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]


def _read_cal_config() -> dict:
    """Read calendar config (calendar_id + weekly digest cache). Creates if missing."""
    default: dict[str, Any] = {
        "calendar_id":   "resupaolo@gmail.com",
        "weekly_digest": {"generated_at": None, "week_start": None,
                          "week_end": None, "content": ""},
    }
    if not CALENDAR_DATA_FILE.exists():
        CALENDAR_DATA_FILE.write_text(json.dumps(default, indent=2))
        return default
    try:
        return json.loads(CALENDAR_DATA_FILE.read_text())
    except Exception:
        return default


def _write_cal_config(data: dict) -> None:
    CALENDAR_DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _get_gcal_service():
    """Return an authenticated Google Calendar API service. Refreshes token if needed."""
    if not _GCAL_AVAILABLE:
        raise HTTPException(status_code=503,
            detail="Google Calendar libraries not installed in container")
    if not GCAL_TOKEN_FILE.exists():
        raise HTTPException(status_code=503,
            detail="Google Calendar not configured — token file missing")
    creds = GCredentials.from_authorized_user_file(str(GCAL_TOKEN_FILE), _GCAL_SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(GRequest())
                GCAL_TOKEN_FILE.write_text(creds.to_json())
            except Exception as exc:
                raise HTTPException(status_code=503,
                    detail=f"Token refresh failed: {exc}") from exc
        else:
            raise HTTPException(status_code=503,
                detail="Google Calendar token invalid — re-run the authorize script")
    return _gcal_build("calendar", "v3", credentials=creds, cache_discovery=False)


def _gcal_to_internal(ev: dict) -> dict:
    """Convert a Google Calendar event to our internal display format."""
    start = ev.get("start", {})
    end   = ev.get("end",   {})
    all_day      = "dateTime" not in start
    date_str     = (start.get("dateTime") or start.get("date") or "")[:10]
    time_str     = start.get("dateTime", "")[11:16]   # "HH:MM" or ""
    end_time_str = end.get("dateTime",   "")[11:16]
    return {
        "id":          ev.get("id", ""),
        "title":       ev.get("summary") or "(No title)",
        "date":        date_str,
        "time":        time_str,
        "end_time":    end_time_str,
        "all_day":     all_day,
        "description": ev.get("description", ""),
        "location":    ev.get("location",    ""),
        "html_link":   ev.get("htmlLink",    ""),
    }


def _fmt_day_header(d: datetime.date) -> str:
    return f"{_DAYS[d.weekday()].upper()}, {d.day:02d} {_MONTHS[d.month - 1].upper()}"


def _build_digest(events: list[dict], start: datetime.date, end: datetime.date,
                  label: str) -> str:
    """Format internal-format events into a readable weekly digest string."""
    by_date: dict[str, list[dict]] = {}
    for ev in events:
        try:
            ev_date = datetime.date.fromisoformat(ev["date"])
        except Exception:
            continue
        if start <= ev_date <= end:
            by_date.setdefault(ev["date"], []).append(ev)

    s = f"{start.day:02d} {_MONTHS[start.month-1]} {start.year}"
    e = f"{end.day:02d}   {_MONTHS[end.month-1]}   {end.year}"
    lines = [f"📅 {label} — {s} to {e}", "━" * 38, ""]
    total = 0
    cur = start
    while cur <= end:
        lines.append(_fmt_day_header(cur))
        day_evs = sorted(by_date.get(cur.isoformat(), []),
                         key=lambda ev: ev.get("time", ""))
        if day_evs:
            for ev in day_evs:
                t, et    = ev.get("time", ""), ev.get("end_time", "")
                time_str = ("All day" if ev.get("all_day")
                            else (f"{t}–{et}" if et else (t or "?")))
                loc      = f" @ {ev['location']}" if ev.get("location") else ""
                note     = f" ({ev['description']})" if ev.get("description") else ""
                lines.append(f"  • {time_str}  {ev['title']}{loc}{note}")
            total += len(day_evs)
        else:
            lines.append("  ✨ Nothing scheduled")
        lines.append("")
        cur += datetime.timedelta(days=1)
    lines += ["━" * 38, f"📊 {total} event{'s' if total != 1 else ''} total"]
    return "\n".join(lines)


def _compute_week_range(mode: str) -> tuple[datetime.date, datetime.date, str]:
    today = datetime.datetime.now(PH_TZ).date()
    if mode == "next":
        days = (7 - today.weekday()) % 7 or 7
        start = today + datetime.timedelta(days=days)
        end   = start + datetime.timedelta(days=6)
        return start, end, "WEEK AHEAD"
    days_sun = (6 - today.weekday()) % 7
    start    = today
    end      = today + datetime.timedelta(days=days_sun)
    label    = "REMAINING WEEK" if today.weekday() > 0 else "THIS WEEK"
    return start, end, label


def _fetch_gcal_events(service, cal_id: str,
                       start: datetime.date, end: datetime.date) -> list[dict]:
    """Fetch events from Google Calendar and convert to internal format."""
    time_min = datetime.datetime.combine(
        start, datetime.time.min, tzinfo=PH_TZ).isoformat()
    time_max = datetime.datetime.combine(
        end,   datetime.time.max, tzinfo=PH_TZ).isoformat()
    result = service.events().list(
        calendarId=cal_id, timeMin=time_min, timeMax=time_max,
        singleEvents=True, orderBy="startTime", maxResults=250,
    ).execute()
    return [_gcal_to_internal(e) for e in result.get("items", [])]


@app.get("/api/calendar/week")
async def calendar_week(mode: str = "remaining"):
    """
    Returns weekly schedule digest from Google Calendar.
    mode=remaining (default): today → Sunday of current week.
    mode=next: next Mon → Sun.
    """
    loop = asyncio.get_event_loop()

    def _compute():
        cfg    = _read_cal_config()
        cal_id = cfg.get("calendar_id", "resupaolo@gmail.com")
        start, end, label = _compute_week_range(mode)

        # Serve cached digest for 'next' mode if it covers the same range
        wd = cfg.get("weekly_digest", {})
        if (mode == "next"
                and wd.get("week_start") == start.isoformat()
                and wd.get("week_end")   == end.isoformat()
                and wd.get("content")):
            return {"mode": "next", "week_start": wd["week_start"],
                    "week_end": wd["week_end"], "generated_at": wd.get("generated_at"),
                    "digest": wd["content"], "events": []}

        service = _get_gcal_service()
        events  = _fetch_gcal_events(service, cal_id, start, end)
        digest  = _build_digest(events, start, end, label)
        now_iso = datetime.datetime.now(PH_TZ).isoformat()

        if mode == "next":
            cfg["weekly_digest"] = {"generated_at": now_iso,
                                    "week_start": start.isoformat(),
                                    "week_end": end.isoformat(), "content": digest}
            _write_cal_config(cfg)

        return {"mode": mode, "week_start": start.isoformat(),
                "week_end": end.isoformat(), "generated_at": now_iso,
                "digest": digest, "events": events}

    return await loop.run_in_executor(None, _compute)


@app.post("/api/calendar/week/trigger")
async def calendar_week_trigger():
    """Force-fetch and regenerate the remaining-week digest from Google Calendar."""
    loop = asyncio.get_event_loop()

    def _compute():
        cfg    = _read_cal_config()
        cal_id = cfg.get("calendar_id", "resupaolo@gmail.com")
        today  = datetime.datetime.now(PH_TZ).date()
        days_sun = (6 - today.weekday()) % 7
        start  = today
        end    = today + datetime.timedelta(days=days_sun)
        label  = "REMAINING WEEK" if today.weekday() > 0 else "THIS WEEK"
        service = _get_gcal_service()
        events  = _fetch_gcal_events(service, cal_id, start, end)
        digest  = _build_digest(events, start, end, label)
        now_iso = datetime.datetime.now(PH_TZ).isoformat()
        cfg["weekly_digest"] = {"generated_at": now_iso,
                                "week_start": start.isoformat(),
                                "week_end": end.isoformat(), "content": digest}
        _write_cal_config(cfg)
        return {"triggered": True, "week_start": start.isoformat(),
                "week_end": end.isoformat(), "generated_at": now_iso,
                "digest": digest, "events": events}

    return await loop.run_in_executor(None, _compute)


@app.get("/api/calendar/events")
async def calendar_events_list(days: int = 60):
    """Return upcoming events from Google Calendar (default: next 60 days)."""
    loop = asyncio.get_event_loop()

    def _get():
        cfg    = _read_cal_config()
        cal_id = cfg.get("calendar_id", "resupaolo@gmail.com")
        today  = datetime.datetime.now(PH_TZ).date()
        end    = today + datetime.timedelta(days=days)
        service = _get_gcal_service()
        events  = _fetch_gcal_events(service, cal_id, today, end)
        return {"events": events, "count": len(events), "calendar_id": cal_id}

    return await loop.run_in_executor(None, _get)


class CalendarEventCreate(BaseModel):
    title: str
    date: str               # YYYY-MM-DD
    time: str = "09:00"     # HH:MM 24h; empty string = all-day
    end_time: str = "10:00"
    description: str = ""
    location: str = ""
    reminder_minutes: int = 30
    all_day: bool = False


@app.post("/api/calendar/events", status_code=201)
async def calendar_events_create(ev: CalendarEventCreate):
    """Create a new event in Google Calendar."""
    loop = asyncio.get_event_loop()

    def _create():
        cfg    = _read_cal_config()
        cal_id = cfg.get("calendar_id", "resupaolo@gmail.com")
        service = _get_gcal_service()

        if ev.all_day or not ev.time:
            body: dict[str, Any] = {
                "summary":     ev.title,
                "start":       {"date": ev.date},
                "end":         {"date": ev.date},
                "description": ev.description,
                "location":    ev.location,
            }
        else:
            tz_offset = "+08:00"
            end_t = ev.end_time or ev.time
            body = {
                "summary":     ev.title,
                "start":       {"dateTime": f"{ev.date}T{ev.time}:00{tz_offset}",
                                "timeZone": "Asia/Manila"},
                "end":         {"dateTime": f"{ev.date}T{end_t}:00{tz_offset}",
                                "timeZone": "Asia/Manila"},
                "description": ev.description,
                "location":    ev.location,
                "reminders": {"useDefault": False, "overrides": [
                    {"method": "popup", "minutes": ev.reminder_minutes}]},
            }

        created = service.events().insert(calendarId=cal_id, body=body).execute()
        return _gcal_to_internal(created)

    return await loop.run_in_executor(None, _create)


@app.delete("/api/calendar/events/{event_id:path}")
async def calendar_events_delete(event_id: str):
    """Delete an event from Google Calendar by its Google event ID."""
    loop = asyncio.get_event_loop()

    def _delete():
        cfg    = _read_cal_config()
        cal_id = cfg.get("calendar_id", "resupaolo@gmail.com")
        service = _get_gcal_service()
        try:
            service.events().delete(calendarId=cal_id, eventId=event_id).execute()
        except Exception as exc:
            raise HTTPException(status_code=404,
                detail=f"Could not delete event: {exc}") from exc
        return {"deleted": True, "id": event_id}

    return await loop.run_in_executor(None, _delete)
