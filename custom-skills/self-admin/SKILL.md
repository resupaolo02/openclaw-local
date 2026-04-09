```skill
---
name: self-admin
description: Use when you need to check system health, restart or rebuild services, troubleshoot errors, create new skills or services, understand the architecture, edit service files, or perform any self-maintenance task. Triggers on: "health", "restart", "rebuild", "status", "broken", "fix", "troubleshoot", "add service", "create skill", "architecture", "system", "docker", "container", "logs", "not working", "down", "error", "how does my system work", "self-maintenance", "admin", "edit", "change", "update", "modify", "add feature", "self-modify"
version: 1.2.0
metadata: { "openclaw": { "emoji": "🔧" } }
---

# Self-Admin — System Knowledge & Maintenance

You ARE this system. This skill teaches you how to inspect, maintain, troubleshoot, and extend yourself.

## Quick Architecture

```
traefik (HTTPS :443) → path-based routing → microservices
llama-server (:8080) → Qwen3.5-9B via llama.cpp (GPU)
openclaw (:18789) → you (Frostbite), the AI agent
core-api (:8000) → internal hub: Docker ops, skills, sessions, system prompt
monitor (:9091) → system/GPU dashboard + ALL microservice health
heartbeat (:9092) → proactive agent poller
calendar (:9093) → Google Calendar integration
chat (:9094) → LLM chat UI, streams from llama-server, auto-saves sessions
landing (:9095) → dashboard home page
finance (:9096) → SQLite expense/income tracker
nutrition (:9097) → SQLite calorie/macro tracker
```

All services except openclaw and llama-server are built from `./services/<name>/app.py`.

**Full architecture details:** `/app/custom-skills/self-admin/references/ARCHITECTURE.md`
**Full API reference:** `/app/custom-skills/self-admin/references/API-REFERENCE.md`

## Health Check Workflow

1. **Quick check all services (single hub):**
   ```bash
   curl -sf http://hub:8000/health && echo "hub OK"
   curl -sf http://hub:8000/monitor/api/health && echo "monitor OK"
   curl -sf http://hub:8000/heartbeat/api/health && echo "heartbeat OK"
   curl -sf http://hub:8000/calendar/api/health && echo "calendar OK"
   curl -sf http://hub:8000/chat/api/health && echo "chat OK"
   curl -sf http://hub:8000/finance/api/health && echo "finance OK"
   curl -sf http://hub:8000/nutrition/api/health && echo "nutrition OK"
   ```

2. **Detailed system status (GPU, RAM, ALL containers + microservice health pings):**
   ```bash
   curl -s http://hub:8000/monitor/api/status
   ```
   Returns: `containers` (all 11), `service_health` (HTTP health per microservice), `gpu`, `host`, `sessions`, `cstats` (deep stats for openclaw, llama-server, core-api, chat).

3. **LLM model status:**
   ```bash
   curl -s http://hub:8000/llm/status
   ```

4. **Container status (all services):**
   ```bash
   curl -s http://hub:8000/containers
   ```
   Returns running status, uptime, restart count for: openclaw, llama-server, core-api, traefik, monitor, heartbeat, calendar, chat, finance, nutrition, landing.

## Sessions — Chat UI Persistence

The chat web UI at `/chat` now persists conversations:
- **localStorage**: Chat history survives navigation away and back (per browser)
- **Backend sessions**: After each complete exchange, the conversation is auto-saved to core-api as a `.jsonl` session file — it will appear in the Sessions drawer

**Session API endpoints (via chat service proxy):**
```bash
# List saved sessions
curl -s http://hub:8000/chat/api/sessions/list

# Load messages from a session
curl -s http://hub:8000/chat/api/sessions/<session-id>/messages

# Save a session manually
curl -s -X POST http://hub:8000/chat/api/sessions/save \
  -H "Content-Type: application/json" \
  -d '{"session_id":"UUID","messages":[{"role":"user","content":"hi"},{"role":"assistant","content":"hello"}]}'

# Delete a session
curl -s -X DELETE http://hub:8000/chat/api/sessions/<session-id>
```

Sessions are stored as `.jsonl` files in `/home/node/.openclaw/agents/main/sessions/` (host: `./openclaw-data/agents/main/sessions/`).

## Self-Modification API

You can edit your own source files and run system commands directly via `core-api`. All paths are relative to the project root (`/home/resupaolo/openclaw-local` on the host, mounted at `/project` inside core-api).

### Run a shell command
```bash
curl -s -X POST http://hub:8000/maintenance/exec \
  -H "Content-Type: application/json" \
  -d '{"cmd":"<shell command>","timeout":60}'
```
Returns: `{"exit_code":0,"stdout":"...","stderr":"..."}`

The container has `docker` and `docker compose` available. Commands run with the project root as cwd by default.

### Read a file
```bash
curl -s "http://hub:8000/maintenance/file?path=services/chat/app.py"
```
Returns: `{"path":"...","content":"...","size":1234}`

### Write / create a file
```bash
curl -s -X POST http://hub:8000/maintenance/file \
  -H "Content-Type: application/json" \
  -d '{"path":"services/chat/app.py","content":"<full file content>"}'
```

### Patch (find & replace) a file
```bash
curl -s -X PATCH http://hub:8000/maintenance/file \
  -H "Content-Type: application/json" \
  -d '{"path":"services/chat/app.py","old_str":"old text","new_str":"new text"}'
```
`old_str` must appear exactly once in the file.

### Delete a file
```bash
curl -s -X DELETE "http://hub:8000/maintenance/file?path=path/to/file"
```

### List a directory
```bash
curl -s "http://hub:8000/maintenance/ls?path=services/chat"
```
Returns entries with name, type (file/dir), and size.

### Typical self-modification workflow
1. **Read** the file you want to change
2. **Patch** (preferred for small edits) or **Write** (for large rewrites)
3. **Rebuild** the affected service
4. **Verify** with a health check or log tail

Example — add a new endpoint to the chat service:
```bash
# 1. Read current source
curl -s "http://hub:8000/maintenance/file?path=services/chat/app.py"

# 2. Patch it
curl -s -X PATCH http://hub:8000/maintenance/file \
  -H "Content-Type: application/json" \
  -d '{"path":"services/chat/app.py","old_str":"# end of routes","new_str":"@app.get(\"/api/ping\")\nasync def ping(): return {\"pong\": True}\n# end of routes"}'

# 3. Rebuild
curl -s -X POST http://hub:8000/maintenance/exec \
  -H "Content-Type: application/json" \
  -d '{"cmd":"docker compose build chat && docker compose up -d --no-deps chat","timeout":120}'

# 4. Verify
curl -s http://hub:8000/chat/api/health
```

## Restart / Rebuild Workflow

**Restart a service** (no code change — just restart the container):
```bash
curl -s -X POST http://hub:8000/maintenance/exec \
  -H "Content-Type: application/json" \
  -d '{"cmd":"docker compose -p openclaw-local --project-directory /project restart <service-name>"}'
```

**Rebuild a service** (after editing its source files):
```bash
curl -s -X POST http://hub:8000/maintenance/exec \
  -H "Content-Type: application/json" \
  -d '{"cmd":"docker compose -p openclaw-local --project-directory /project build <service-name> && docker compose -p openclaw-local --project-directory /project up -d --no-deps <service-name>","timeout":120}'
```

**Restart yourself** (after skill changes):
```bash
curl -s -X POST http://hub:8000/maintenance/exec \
  -H "Content-Type: application/json" \
  -d '{"cmd":"docker compose -p openclaw-local --project-directory /project restart openclaw"}'
```

**Full stack restart:**
```bash
curl -s -X POST http://hub:8000/maintenance/exec \
  -H "Content-Type: application/json" \
  -d '{"cmd":"docker compose -p openclaw-local --project-directory /project down && docker compose -p openclaw-local --project-directory /project up -d","timeout":120}'
```

## Troubleshooting Workflow

1. **Check which services are down:**
   ```bash
   curl -s http://hub:8000/containers
   ```
   If core-api itself is down, use: `docker compose ps` on the host.

2. **Read service logs** (last 50 lines):
   ```bash
   curl -s -X POST http://hub:8000/maintenance/exec \
     -H "Content-Type: application/json" \
     -d '{"cmd":"docker compose logs --tail=50 <service-name>"}'
   ```

3. **Common issues and fixes:**
   - **LLM not responding** → restart llama, check GPU memory (`nvidia-smi`)
   - **Skill not triggering** → check `description` field triggers in SKILL.md, then `docker compose restart openclaw`
   - **Service 502/unhealthy** → `docker compose logs <service>`, then rebuild if code issue
   - **Calendar auth failed** → token may need refresh, check `/workspace/google-token.json`
   - **Finance/Nutrition DB locked** → restart the service (SQLite lock timeout)
   - **Chat disappears on navigation** → should self-restore from localStorage; if broken, check browser console for JS errors
   - **Chat sessions not showing** → check `curl -s http://hub:8000/sessions/list`; ensure sessions dir is writable

## docker-compose.yml Volume Notes

core-api mounts:
- `.:/project` — full project root (read-write) — used by maintenance endpoints
- `./openclaw-data:/openclaw-data:ro` — read-only general data
- `./openclaw-data/workspace:/openclaw-data/workspace` — writable workspace
- `./openclaw-data/agents/main/sessions:/openclaw-data/agents/main/sessions` — writable sessions
- `/var/run/docker.sock` — docker socket (rw, needed for `docker compose` commands)

## Creating a New Skill

1. Create folder and SKILL.md via maintenance API:
   ```bash
   # Create the directory
   curl -s -X POST http://hub:8000/maintenance/exec \
     -H "Content-Type: application/json" \
     -d '{"cmd":"mkdir -p custom-skills/<skill-name>"}'

   # Write the SKILL.md
   curl -s -X POST http://hub:8000/maintenance/file \
     -H "Content-Type: application/json" \
     -d '{"path":"custom-skills/<skill-name>/SKILL.md","content":"<skill content>"}'
   ```
2. Key rules:
   - Wrap in ` ```skill ``` ` fences
   - `name` must match folder name (kebab-case)
   - `description` starts with "Use when...", ends with `Triggers on: "kw1", "kw2"`
   - Keep under 300 lines; use `references/` for large data
3. Restart yourself to load it:
   ```bash
   curl -s -X POST http://hub:8000/maintenance/exec \
     -H "Content-Type: application/json" \
     -d '{"cmd":"docker compose -p openclaw-local --project-directory /project restart openclaw"}'
   ```
4. Test by sending a message that should trigger it

## Adding a New Service

1. Create `services/<name>/app.py` (FastAPI + uvicorn, expose `/api/health`)
2. Create `services/<name>/Dockerfile` (copy pattern from any existing service)
3. Create `services/<name>/requirements.txt`
4. Add to `docker-compose.yml` via maintenance API:
   ```bash
   curl -s -X PATCH http://hub:8000/maintenance/file \
     -H "Content-Type: application/json" \
     -d '{"path":"docker-compose.yml","old_str":"<anchor text>","new_str":"<new content>"}'
   ```
5. Add to `traefik/dynamic.yml` (router + stripPrefix middleware + service + basicAuth)
6. Add container name to `ALL_CONTAINERS` list in `services/core-api/app.py`
7. Add health URL to `MICROSERVICE_HEALTH_ENDPOINTS` in `services/monitor/app.py`
8. Rebuild:
   ```bash
   curl -s -X POST http://hub:8000/maintenance/exec \
     -H "Content-Type: application/json" \
     -d '{"cmd":"docker compose build core-api monitor <name> && docker compose up -d --no-deps core-api monitor <name>","timeout":180}'
   ```
9. Verify: `curl -sf http://<name>:<port>/api/health`

## Self-Maintenance Checklist

During heartbeats or when idle, you can:
- Run health checks on all services
- Review and clean up memory files
- Update MEMORY.md with recent learnings
- Check for stale data in skill references
- Verify calendar credentials are valid
- Review logs for recurring errors

## Notes

- Use `curl http://hub:8000/maintenance/exec` for host-level commands (docker compose, etc.)
- Use `curl http://hub:8000/maintenance/file` to read/write/patch project source files
- Services are NOT hot-reloaded — always restart/rebuild after changes
- SQLite databases: `finance.db` and `nutrition.db` in `/workspace/`
- Never expose internal ports directly; Traefik handles external access
- Skills are loaded at startup; changes require `docker compose restart openclaw`
- core-api uses `/health` (not `/api/health`) — all other services use `/api/health`
- All paths in maintenance API are relative to project root (`/home/resupaolo/openclaw-local`)
```
