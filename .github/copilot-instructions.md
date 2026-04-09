# OpenClaw Local — Copilot Instructions

## Architecture Overview

Self-hosted AI assistant stack on a single Linux machine with an NVIDIA GPU. **Monolith architecture** — all services consolidated into a single `hub` container.

```
traefik (HTTPS, DuckDNS)  → reverse proxy with basic auth
hub                        → single FastAPI monolith serving all APIs + web UIs (:8000)
openclaw                   → AI agent container (ghcr.io/openclaw/openclaw:latest)
datasette                  → optional SQLite browser (:8001)
```

**Hub modules:** core (Docker/exec/sessions/skills), chat (LLM streaming), finance (expense tracker), nutrition (food/macro tracker), calendar (Google Calendar), monitor (system/GPU metrics), heartbeat (proactive poller), landing (dashboard).

**LLM:** Multi-model via external providers — Gemini Flash (default) + OpenRouter Qwen3-Coder (coding). No local LLM running.

**Data flow:** `openclaw` agent calls `http://hub:8000/...` for all operations. Chat UI streams from Gemini/OpenRouter via hub. Finance/nutrition use SQLite at `/workspace/openclaw.db`.

## Common Commands

```bash
# Start the full stack
docker compose up -d

# Rebuild hub after code changes
docker compose build hub && docker compose up -d --no-deps hub

# Reload skills (required after any change to custom-skills/)
docker compose restart openclaw

# View logs
docker compose logs -f hub
docker compose logs --tail=50 hub

# Check health
curl -sf http://localhost:18789/health  # openclaw
# Hub health (via docker exec since port not exposed to host):
docker exec hub curl -sf http://localhost:8000/health
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

## Hub Service Architecture

- Single FastAPI app (`services/hub/app.py`) mounts 7 router modules
- Each module in `services/hub/routers/` is a self-contained APIRouter
- Core routes at root (`/health`, `/containers`, `/exec`, `/sessions/*`, `/skills`, `/maintenance/*`)
- All other modules prefixed: `/chat/*`, `/finance/*`, `/nutrition/*`, `/calendar/*`, `/monitor/*`, `/heartbeat/*`
- SQLite databases stored at `/workspace/openclaw.db` (mounted from `./openclaw-data/workspace/`)
- HTTP clients use `httpx.AsyncClient` initialized in FastAPI `lifespan` context
- GPU monitoring via nvidia-smi (CUDA base image)

## Traefik Routing

All external traffic hits `openclaw-frostbite.duckdns.org` over HTTPS (Let's Encrypt via DuckDNS challenge). Full paths forwarded to hub (no prefix stripping). All routes require HTTP basic auth (`traefik/.htpasswd`).

| Path prefix | Target |
|---|---|
| `/chat` | hub:8000 |
| `/finance` | hub:8000 |
| `/nutrition` | hub:8000 |
| `/calendar` | hub:8000 |
| `/monitor` | hub:8000 |
| `/heartbeat` | hub:8000 |
| `/datasette` | datasette:8001 (stripPrefix) |
| `/` (catch-all) | hub:8000 |

## Environment & Secrets

Secrets are in `.env` (never commit). Key variables:
- `DUCKDNS_TOKEN` — required for TLS cert renewal
- `CORS_ORIGIN` — used by the chat module
- `GEMINI_API_KEY` — Google AI Studio API key for Gemini Flash
- `OPENROUTER_API_KEY` — OpenRouter API key for Qwen3-Coder
- `USDA_API_KEY` — optional; nutrition module falls back to `DEMO_KEY` (rate-limited)

LLM is configured in `openclaw-data/openclaw.json` (providers: gemini + openrouter).

## OpenClaw Agent Workspace

The agent's persistent workspace is `./openclaw-data/` (mounted as `/home/node/.openclaw` in the container). Key files:
- `workspace/AGENTS.md` — agent behavioral rules and memory conventions
- `workspace/SOUL.md`, `workspace/USER.md` — agent identity/user context
- `workspace/memory/YYYY-MM-DD.md` — daily session logs
- `workspace/MEMORY.md` — curated long-term memory (main session only)
- `workspace/HEARTBEAT.md` — optional checklist for heartbeat polls
- `workspace/openclaw.db` — shared SQLite database (finance + nutrition data)
