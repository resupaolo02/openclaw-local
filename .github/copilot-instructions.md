# OpenClaw Local — Copilot Instructions

## Architecture Overview

This is a self-hosted AI assistant stack running on a single Linux machine with an NVIDIA GPU.

```
traefik (HTTPS, DuckDNS) → all public-facing services
llama-server             → llama.cpp serving Qwen3.5-9B via OpenAI-compatible API (:8080)
openclaw                 → AI agent container (ghcr.io/openclaw/openclaw:latest)
core-api                 → internal FastAPI hub: Docker ops, skill loading, session mgmt (:8000)
monitor                  → system/GPU stats dashboard (:9091)
heartbeat                → proactive agent poller (:9092)
calendar                 → calendar service (:9093)
chat                     → LLM chat UI and API, streams from llama-server (:9094)
landing                  → dashboard/landing page (:9095)
finance                  → SQLite-backed expense tracker (:9096)
nutrition                → SQLite-backed food/macro tracker (:9097)
```

All services except `openclaw` and `llama-server` are **built locally** from `./services/<name>/`. Each is a single-file Python FastAPI app (`app.py`).

**Data flow:** `openclaw` talks to `core-api` for skills/sessions. `chat` service streams completions directly from `llama-server` and delegates skill/session state to `core-api`. Services like `finance`, `nutrition`, and `calendar` store SQLite databases at `/workspace` (host: `./openclaw-data/workspace/`).

## Common Commands

```bash
# Start the full stack
docker compose up -d

# Rebuild and restart a single service (e.g. after editing services/chat/app.py)
docker compose build chat && docker compose up -d --no-deps chat

# Reload skills (required after any change to custom-skills/)
docker compose restart openclaw

# View logs
docker compose logs -f chat          # follow a specific service
docker compose logs --tail=50 core-api

# Check health
curl -sf http://localhost:18789/health  # openclaw
curl -sf http://localhost:8080/health   # llama-server (from host, if port is exposed)
```

## Custom Skills

Skills extend the OpenClaw agent. Each skill lives in `custom-skills/<skill-name>/` with a required `SKILL.md`.

**Creating a skill:**
1. `mkdir custom-skills/my-skill`
2. Create `SKILL.md` — must be wrapped in ` ```skill ``` ` fences with YAML frontmatter
3. `docker compose restart openclaw` — skills are **not** hot-reloaded

**SKILL.md frontmatter fields:**
- `name`: kebab-case, must match folder name exactly
- `description`: starts with "Use when...", ends with `Triggers on: "kw1", "kw2"` — this drives routing
- `version`: semver string
- `metadata`: `{ "openclaw": { "emoji": "🔧" } }`

Full format spec: `custom-skills/SKILL_FORMAT.md`

**Large datasets** (card portfolios, reference tables) go in `references/` or `assets/` subfolders — never inline in `SKILL.md`. Point to them with a file path comment.

## Service Conventions

- All microservices: Python 3.11, FastAPI + uvicorn, single `app.py`
- Dockerfiles are identical in structure: slim Python base → install curl → pip install → copy app.py
- Every service exposes `GET /health` and `GET /api/health` (check the specific service; some use one, some both)
- Inter-service communication uses Docker DNS names (e.g., `http://core-api:8000`, `http://llama:8080`)
- Services that need SQLite store DBs in `/workspace` (mounted from `./openclaw-data/workspace/`)
- Thread-safety: `core-api` uses `threading.Lock()` for shared state (`_state_lock`, `_prompt_lock`, `_docker_lock`)
- HTTP clients in services use `httpx.AsyncClient` initialized in FastAPI `lifespan` context

## Traefik Routing

All external traffic hits `openclaw-frostbite.duckdns.org` over HTTPS (Let's Encrypt via DuckDNS challenge). Services are routed by path prefix; the prefix is stripped before forwarding. All routes require HTTP basic auth (`traefik/.htpasswd`).

| Path prefix | Service |
|---|---|
| `/chat` | chat:9094 |
| `/monitor` | monitor:9091 |
| `/heartbeat` | heartbeat:9092 |
| `/calendar` | calendar:9093 |
| `/finance` | finance:9096 |
| `/nutrition` | nutrition:9097 |
| `/` (catch-all) | landing:9095 |

To add a new routed service: add entries to both `traefik/dynamic.yml` (router + stripPrefix middleware + service) and `docker-compose.yml`.

## Environment & Secrets

Secrets are in `.env` (never commit). Key variables:
- `DUCKDNS_TOKEN` — required for TLS cert renewal
- `CORS_ORIGIN` — used by the chat service
- `USDA_API_KEY` — optional; nutrition service falls back to `DEMO_KEY` (rate-limited)

The LLM endpoint is configured via `OPENAI_API_BASE=http://llama:8080/v1` in the `openclaw` container and `LLM_BASE_URL=http://llama:8080` in microservices.

## OpenClaw Agent Workspace

The agent's persistent workspace is `./openclaw-data/` (mounted as `/home/node/.openclaw` in the container). Key files the agent uses:
- `workspace/AGENTS.md` — agent behavioral rules and memory conventions
- `workspace/SOUL.md`, `workspace/USER.md` — agent identity/user context
- `workspace/memory/YYYY-MM-DD.md` — daily session logs
- `workspace/MEMORY.md` — curated long-term memory (main session only)
- `workspace/HEARTBEAT.md` — optional checklist for heartbeat polls
