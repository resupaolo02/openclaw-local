"""
Core router — Docker operations, session management, skills, and LLM health.
Migrated from the standalone core-api microservice.
"""

import asyncio
import datetime
import json
import logging
import os
import re
import subprocess
import time
import threading
from pathlib import Path
from typing import Any

import docker
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger("core-api")

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://llama:8080")

_http_client: httpx.AsyncClient | None = None

OPENCLAW_DATA = Path("/openclaw-data")
SESSIONS_DIR  = OPENCLAW_DATA / "agents/main/sessions"
AGENTS_DIR    = OPENCLAW_DATA / "agents"
WORKSPACE     = OPENCLAW_DATA / "workspace"
CUSTOM_SKILLS = Path("/custom-skills")
PROJECT_ROOT  = Path(os.getenv("PROJECT_ROOT", "/project"))

_state_lock  = threading.Lock()
_prompt_lock = threading.Lock()

_SYS_PROMPT_CACHE: str | None = None
_SYS_PROMPT_TS: float = 0.0

# Container network rate tracking
_CNET_SNAP: dict[str, Any] = {}

# ── Singleton Docker client ──────────────────────────────────────────────────
_docker_client = None
_docker_lock = threading.Lock()


# ── Lifecycle helpers (called by the main app lifespan) ──────────────────────

async def init_clients():
    global _http_client
    _http_client = httpx.AsyncClient(base_url=LLM_BASE_URL, timeout=30.0)
    logger.info("HTTP client ready → %s", LLM_BASE_URL)


async def close_clients():
    global _http_client
    if _http_client:
        await _http_client.aclose()
        _http_client = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _safe(fn, fallback=None):
    try:
        return fn()
    except Exception:
        return fallback


def _get_docker():
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
        delta = datetime.datetime.now(datetime.timezone.utc) - started
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


# ── Containers ───────────────────────────────────────────────────────────────

ALL_CONTAINERS = [
    "openclaw", "core-api", "traefik",
    "monitor", "heartbeat", "calendar", "chat",
    "finance", "nutrition", "landing",
]

# Only collect expensive docker stats for the main heavy containers
DEEP_STATS_CONTAINERS = ["openclaw", "core-api", "chat"]

def get_containers() -> list[dict]:
    results = []
    try:
        client = _get_docker()
        for name in ALL_CONTAINERS:
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


# ── Per-container deep stats ─────────────────────────────────────────────────

def get_container_deep_stats() -> list[dict]:
    results = []
    now_ts = time.monotonic()
    try:
        client = _get_docker()
        for name in DEEP_STATS_CONTAINERS:
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

                # Memory
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


# ── Docker exec ──────────────────────────────────────────────────────────────

def _exec_in_openclaw(cmd: str) -> dict[str, Any]:
    try:
        client = _get_docker()
        container = client.containers.get("openclaw")
        exit_code, output = container.exec_run(cmd, demux=False, stream=False)
        raw = output.decode("utf-8", errors="replace").strip() if output else ""
        return {"exit_code": exit_code, "output": raw}
    except Exception as e:
        logger.error("_exec_in_openclaw failed (%s): %s", cmd[:40], e)
        return {"exit_code": -1, "output": "", "error": str(e)}


# ── LLM status ──────────────────────────────────────────────────────────────

async def get_llm_status() -> dict:
    base = str(_http_client.base_url) if _http_client else "—"
    model_name = os.getenv("LLM_MODEL", "—")
    result: dict[str, Any] = {"base_url": base, "healthy": False, "model": model_name}
    if not _http_client:
        result["error"] = "HTTP client not initialized"
        return result

    # Determine if this is a local (llama-server) or external provider
    is_local = "llama" in base or "localhost" in base or "127.0.0.1" in base

    try:
        if is_local:
            # Local llama-server: check health, models, props
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
        else:
            # External provider (Gemini, OpenRouter, etc.)
            result["healthy"] = True
            result["health_status"] = "external"
            result["provider"] = "external"
    except Exception as e:
        logger.error("get_llm_status failed: %s", e)
        result["error"] = str(e)
    return result


# ── Sessions ─────────────────────────────────────────────────────────────────

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
            if e["error"][:60] not in seen and not seen.add(e["error"][:60])
        ][:5]
    except Exception as e:
        out["error"] = str(e)
    return out


def list_sessions_data() -> list[dict]:
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


# ── Skills ───────────────────────────────────────────────────────────────────

def _parse_skill_frontmatter(text: str) -> dict[str, str]:
    meta: dict[str, str] = {"name": "", "description": "", "emoji": "🤖"}
    text = re.sub(r"^`{3,}[a-z]*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"^`{3,}\s*$", "", text, flags=re.MULTILINE)
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


# ── System prompt ────────────────────────────────────────────────────────────

def build_system_prompt() -> str:
    """Build a compact system prompt with skill summaries only (not full content).

    Full SKILL.md content is loaded on-demand via /skill/<name>/content.
    This keeps the system prompt small enough to leave room for conversation.
    """
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
            lines = [
                "## Your Available Skills\n",
                "You have specialized skills. Match the user's request to the best skill and follow its approach.\n",
                "When you need a skill's full instructions, call: exec(`curl -s http://core-api:8000/skill/<skill-name>/content`)\n",
            ]
            for s in skills:
                lines.append(f"- **{s['emoji']} {s['name']}**: {s['description']}")
            parts.append("\n".join(lines))
        prompt = "\n\n---\n\n".join(parts)
        _SYS_PROMPT_CACHE = prompt
        _SYS_PROMPT_TS = time.monotonic()
        return prompt


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/containers")
async def containers_route():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_containers)


@router.get("/containers/stats")
async def containers_stats_route():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_container_deep_stats)


class ExecRequest(BaseModel):
    cmd: str


@router.post("/exec")
async def exec_route(request: ExecRequest):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _exec_in_openclaw, request.cmd)


@router.get("/llm/status")
async def llm_status_route():
    return await get_llm_status()


@router.get("/sessions/overview")
async def sessions_overview_route():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_sessions)


@router.get("/sessions/list")
async def sessions_list_route():
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, list_sessions_data)
    return {"sessions": data}


@router.get("/sessions/{session_id}/messages")
async def session_messages_route(session_id: str):
    if not re.match(r"^[0-9a-f-]{36}$", session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID")
    return {"messages": get_session_messages(session_id)}


@router.delete("/sessions/{session_id}")
async def delete_session_route(session_id: str):
    if not re.match(r"^[0-9a-f-]{36}$", session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID")
    sf = SESSIONS_DIR / f"{session_id}.jsonl"
    if not sf.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    sessions_path = "/home/node/.openclaw/agents/main/sessions"
    src  = f"{sessions_path}/{session_id}.jsonl"
    dest = f"{sessions_path}/{session_id}.jsonl.deleted.{stamp}Z"
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _exec_in_openclaw, f"mv {src} {dest}")
    if result.get("exit_code", -1) != 0:
        detail = result.get("error") or result.get("output") or "mv failed"
        raise HTTPException(status_code=500, detail=detail)
    return {"deleted": True, "id": session_id}


class SaveSessionRequest(BaseModel):
    session_id: str
    messages: list[dict]
    title: str = ""


@router.post("/sessions/save")
async def save_session_route(req: SaveSessionRequest):
    if not re.match(r"^[0-9a-f-]{36}$", req.session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID")
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    sf = SESSIONS_DIR / f"{req.session_id}.jsonl"
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    lines = [json.dumps({"type": "session", "id": req.session_id, "timestamp": now_iso})]
    for msg in req.messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role not in ("user", "assistant") or not content:
            continue
        lines.append(json.dumps({
            "type": "message",
            "message": {"role": role, "content": content},
        }))
    sf.write_text("\n".join(lines) + "\n")
    return {"saved": True, "id": req.session_id, "message_count": len(req.messages)}


@router.get("/skills")
async def skills_route():
    return {"skills": get_skills_data()}


@router.get("/system-prompt")
async def system_prompt_route():
    return {"prompt": build_system_prompt()}


@router.get("/skill/{skill_name}/content")
async def skill_content_route(skill_name: str):
    """Return the full SKILL.md content for a specific skill (on-demand loading)."""
    for base in [CUSTOM_SKILLS, WORKSPACE / "custom-skills"]:
        skill_md = base / skill_name / "SKILL.md"
        if skill_md.exists():
            try:
                return {"skill": skill_name, "content": skill_md.read_text()}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")


# ── Maintenance — host file & shell access ───────────────────────────────────

def _safe_path(raw: str) -> Path:
    """Resolve path relative to PROJECT_ROOT; reject traversal outside it."""
    # Strip leading slash so Path joining works correctly
    rel = raw.lstrip("/")
    resolved = (PROJECT_ROOT / rel).resolve()
    if not str(resolved).startswith(str(PROJECT_ROOT.resolve())):
        raise HTTPException(status_code=400, detail="Path outside project root")
    return resolved


class HostExecRequest(BaseModel):
    cmd: str
    cwd: str = ""      # relative to PROJECT_ROOT; empty = PROJECT_ROOT
    timeout: int = 60


class FileWriteRequest(BaseModel):
    path: str          # relative to PROJECT_ROOT
    content: str


class FilePatchRequest(BaseModel):
    path: str          # relative to PROJECT_ROOT
    old_str: str
    new_str: str


def _run_host_cmd(req: HostExecRequest) -> dict[str, Any]:
    cwd = PROJECT_ROOT
    if req.cwd:
        cwd = _safe_path(req.cwd)
    try:
        result = subprocess.run(
            req.cmd,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=req.timeout,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"Command timed out after {req.timeout}s"}
    except Exception as e:
        return {"exit_code": -1, "stdout": "", "stderr": str(e)}


@router.post("/maintenance/exec")
async def maintenance_exec(req: HostExecRequest):
    """Run a shell command on the host (inside core-api container which has docker socket + project mount)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run_host_cmd, req)


@router.get("/maintenance/file")
async def maintenance_file_read(path: str):
    """Read a file from the project directory."""
    p = _safe_path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not p.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    try:
        content = p.read_text(errors="replace")
        return {"path": path, "content": content, "size": p.stat().st_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/maintenance/file")
async def maintenance_file_write(req: FileWriteRequest):
    """Write (create or overwrite) a file in the project directory."""
    p = _safe_path(req.path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(req.content)
        return {"written": True, "path": req.path, "size": len(req.content)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/maintenance/file")
async def maintenance_file_patch(req: FilePatchRequest):
    """Find-and-replace exactly one occurrence of old_str with new_str in a file."""
    p = _safe_path(req.path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = p.read_text(errors="replace")
        count = content.count(req.old_str)
        if count == 0:
            raise HTTPException(status_code=422, detail="old_str not found in file")
        if count > 1:
            raise HTTPException(status_code=422, detail=f"old_str found {count} times — must be unique")
        new_content = content.replace(req.old_str, req.new_str, 1)
        p.write_text(new_content)
        return {"patched": True, "path": req.path}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/maintenance/file")
async def maintenance_file_delete(path: str):
    """Delete a file from the project directory."""
    p = _safe_path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not p.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    try:
        p.unlink()
        return {"deleted": True, "path": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/maintenance/ls")
async def maintenance_ls(path: str = ""):
    """List directory contents in the project directory."""
    p = _safe_path(path) if path else PROJECT_ROOT
    if not p.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")
    try:
        entries = []
        for item in sorted(p.iterdir()):
            entries.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            })
        return {"path": path or ".", "entries": entries}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
