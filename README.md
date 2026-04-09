# 🐾 OpenClaw Local

![CI](https://github.com/resupaolo02/openclaw-local/actions/workflows/ci.yml/badge.svg)
![Docker Compose](https://img.shields.io/badge/docker--compose-✓-blue?logo=docker)
![Python](https://img.shields.io/badge/python-3.11-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

A **self-hosted AI assistant stack** with multi-model routing, personal finance tracking, nutrition logging, calendar integration, and extensible custom skills — all containerized with Docker Compose.

OpenClaw uses **external LLM providers** (Google Gemini + OpenRouter) via the OpenAI-compatible API format, so **no local GPU is required**.

---

## Table of Contents

- [Architecture](#architecture)
- [Multi-Model Routing](#multi-model-routing)
- [Hub Modules](#hub-modules)
- [Custom Skills](#custom-skills)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Common Commands](#common-commands)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [Documentation](#documentation)
- [License](#license)

---

## Architecture

**4-container monolith** — all application services consolidated into a single `hub` container for performance and simplicity.

```
Internet
  │
  ▼
┌──────────────────────────────────────────────────┐
│  Traefik  (HTTPS :443, Let's Encrypt via DuckDNS) │
│  Basic auth · security headers · gzip             │
└──┬───────────────────────────────────────────────┘
   │  path-based routing (full path forwarded)
   ▼
┌──────────────────────────────────────────────────┐
│  hub :8000  (single FastAPI monolith)             │
│                                                    │
│  /            → Landing page                       │
│  /chat/*      → LLM chat UI + streaming API        │
│  /finance/*   → Expense & income tracker           │
│  /nutrition/* → Calorie & macro tracker            │
│  /calendar/*  → Google Calendar integration        │
│  /monitor/*   → System/GPU dashboard               │
│  /heartbeat/* → Proactive agent poller             │
│  /health      → Core API (containers, exec, etc.)  │
└──────────────────────────────────────────────────┘
   ▲
   │
┌──────────────────────┐     ┌───────────────────────┐
│  openclaw :18789     │────▶│  LLM Provider(s)      │
│  (AI agent)          │     │  • Google AI Studio    │
│  ghcr.io/openclaw/   │     │  • OpenRouter          │
│  openclaw:latest     │     └───────────────────────┘
└──────────────────────┘
```

**Data flow:**
- External traffic → **Traefik** (HTTPS, basic auth) → **hub** (single process handles everything)
- **openclaw** agent calls `http://hub:8000/...` for all API operations (skills, exec, data)
- **hub** serves all web UIs, APIs, and manages SQLite databases in-process
- No inter-service HTTP calls — all modules share the same FastAPI instance

---

## Multi-Model Routing

The chat module includes a **keyword-based task classifier** that routes requests to the best model:

| Role | Model | Provider | Why |
|------|-------|----------|-----|
| **Default** (general chat) | Gemini 2.5 Flash Preview | Google AI Studio | Highest-intelligence free model |
| **Coding** (code gen, debug) | Qwen3-Coder | OpenRouter | SWE-bench SOTA — comparable to Claude Sonnet 4 |

Coding detection triggers on: code blocks, "write code", "debug", "refactor", "syntax error", etc.

---

## Hub Modules

All modules live in `services/hub/routers/` as FastAPI APIRouters:

| Module | Prefix | Description |
|--------|--------|-------------|
| **core** | `/` (root) | Docker ops, shell exec, session management, skill loading, system prompt |
| **chat** | `/chat` | LLM chat UI, streaming API, file upload, multi-model routing, tool calls |
| **finance** | `/finance` | Personal expense/income tracker, credit cards, budgets. SQLite-backed. |
| **nutrition** | `/nutrition` | Calorie/macro tracker, food database search (USDA + Open Food Facts). SQLite-backed. |
| **calendar** | `/calendar` | Google Calendar integration — event CRUD, weekly digest, token refresh |
| **monitor** | `/monitor` | System dashboard — CPU, RAM, disk, GPU stats, container health |
| **heartbeat** | `/heartbeat` | Proactive agent poller — reads HEARTBEAT.md, triggers events |

---

## Custom Skills

9 skills extending the OpenClaw agent:

| Skill | Emoji | Description |
|-------|-------|-------------|
| **self-admin** | 🛠️ | System health, restarts, troubleshooting, architecture knowledge |
| **calendar-assistant** | 📅 | Google Calendar management — events, digest, schedule |
| **finance-tracker** | 💰 | Live read/write to personal finance database |
| **nutrition-tracker** | 🥗 | Calorie/macro tracking, food search, daily goals |
| **ph-credit-card-maximizer** | 💳 | Philippine credit card rewards optimizer |
| **ph-investment-advisor** | 📈 | Philippine personal finance & investment advisor |
| **travel-advisor** | ✈️ | Travel planning for Filipino travelers |
| **media-downloader** | 📥 | Central router for media downloads |
| **epub-downloader** | 📚 | Free EPUB search and download |

### Creating a New Skill

1. `mkdir custom-skills/my-skill`
2. Create `SKILL.md` with YAML frontmatter (see [`custom-skills/SKILL_FORMAT.md`](custom-skills/SKILL_FORMAT.md))
3. `docker compose restart openclaw` (skills are **not** hot-reloaded)

---

## Quick Start

### Prerequisites

- **Docker** and **Docker Compose** (v2+)
- **Linux**, **macOS**, or **WSL2** on Windows
- A free API key from [Google AI Studio](https://aistudio.google.com/apikey)
- *(Optional)* An [OpenRouter](https://openrouter.ai/keys) API key for coding model

### Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/resupaolo02/openclaw-local.git
   cd openclaw-local
   ```

2. **Create your environment file:**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` with your API keys (see [Configuration](#configuration)).

3. **Create the Traefik auth file:**
   ```bash
   sudo apt install apache2-utils   # or brew install httpd on macOS
   htpasswd -c traefik/.htpasswd your-username
   ```

4. **Configure OpenClaw agent:**
   ```bash
   cp openclaw-data/openclaw.json.example openclaw-data/openclaw.json
   ```
   Edit `openclaw.json` with your provider API keys.

5. **Start the stack:**
   ```bash
   docker compose up -d
   ```

6. **Verify:**
   ```bash
   docker compose ps
   docker exec hub curl -sf http://localhost:8000/health
   ```

---

## Configuration

### Environment Variables (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | Yes | Google AI Studio API key |
| `OPENROUTER_API_KEY` | No | OpenRouter API key for coding model |
| `DUCKDNS_TOKEN` | Yes* | DuckDNS token for TLS cert renewal |
| `CORS_ORIGIN` | No | Allowed CORS origin (default: your DuckDNS domain) |
| `USDA_API_KEY` | No | USDA FoodData Central key (falls back to DEMO_KEY) |

### Traefik Routing

| Path | → Target |
|------|----------|
| `/chat` | hub:8000 |
| `/finance` | hub:8000 |
| `/nutrition` | hub:8000 |
| `/calendar` | hub:8000 |
| `/monitor` | hub:8000 |
| `/heartbeat` | hub:8000 |
| `/` (catch-all) | hub:8000 |

---

## Common Commands

```bash
# Start/stop the stack
docker compose up -d
docker compose down

# Rebuild hub after code changes
docker compose build hub && docker compose up -d --no-deps hub

# Reload skills
docker compose restart openclaw

# View logs
docker compose logs -f hub
docker compose logs --tail=50 hub

# Health check
docker exec hub curl -sf http://localhost:8000/health
```

---

## Testing

```bash
# Install test dependencies
pip install -r services/hub/requirements.txt pytest

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_finance.py -v
```

Tests cover:
- Router imports and route registration
- Finance DB operations and API endpoints
- Nutrition DB operations and API endpoints
- Static HTML file existence

CI/CD runs automatically on push/PR via GitHub Actions (`.github/workflows/ci.yml`).

---

## Project Structure

```
openclaw-local/
├── docker-compose.yml              # 4-container stack
├── .env                            # Secrets & config (not committed)
│
├── services/
│   └── hub/                        # Monolith service
│       ├── app.py                  #   Main FastAPI app
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── routers/                #   Module routers
│       │   ├── core.py             #     Docker ops, exec, sessions, skills
│       │   ├── chat.py             #     LLM chat + multi-model routing
│       │   ├── finance.py          #     Expense/income tracker
│       │   ├── nutrition.py        #     Calorie/macro tracker
│       │   ├── calendar.py         #     Google Calendar
│       │   ├── monitor.py          #     System metrics
│       │   └── heartbeat.py        #     Heartbeat poller
│       └── static/                 #   Web UI HTML files
│
├── custom-skills/                  # OpenClaw agent skills
├── openclaw-data/                  # Agent persistent data
│   ├── workspace/                  #   SQLite DBs, memory files
│   └── openclaw.json               #   Agent provider config
├── traefik/                        # Reverse proxy config
├── tests/                          # Test suite
├── docs/                           # Additional documentation
└── .github/workflows/ci.yml       # CI/CD pipeline
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Deployment guide for Linux, macOS, and WSL2 |
| [`docs/MAINTENANCE.md`](docs/MAINTENANCE.md) | Maintenance guide — backups, updates, troubleshooting |
| [`custom-skills/SKILL_FORMAT.md`](custom-skills/SKILL_FORMAT.md) | Skill authoring specification |

---

## License

This project is licensed under the [MIT License](LICENSE).
