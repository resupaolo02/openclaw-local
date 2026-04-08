# 🐾 OpenClaw Local

<!-- Badges (update URLs when CI/CD is configured) -->
![Docker Compose](https://img.shields.io/badge/docker--compose-✓-blue?logo=docker)
![Python](https://img.shields.io/badge/python-3.11-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?logo=fastapi&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

A **self-hosted AI assistant stack** with multi-model routing, personal finance tracking, nutrition logging, calendar integration, and extensible custom skills — all containerized with Docker Compose.

OpenClaw uses **external LLM providers** (Google Gemini + OpenRouter) via the OpenAI-compatible API format, so **no local GPU is required**. Optionally, a local llama.cpp server can run alongside for offline/private use.

---

## Table of Contents

- [Architecture](#architecture)
- [Multi-Model Routing](#multi-model-routing)
- [Services](#services)
- [Custom Skills](#custom-skills)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Common Commands](#common-commands)
- [Project Structure](#project-structure)
- [Documentation](#documentation)
- [License](#license)

---

## Architecture

```
Internet
  │
  ▼
┌──────────────────────────────────────────────────────────────────┐
│  Traefik  (HTTPS :443, Let's Encrypt via DuckDNS)               │
│  Basic auth · security headers · rate limiting · gzip            │
└──┬───────┬────────┬────────┬────────┬────────┬────────┬─────────┘
   │       │        │        │        │        │        │
   ▼       ▼        ▼        ▼        ▼        ▼        ▼
 /chat   /monitor /heartbeat /calendar /finance /nutrition /data    /
   │       │        │         │         │         │        │        │
   ▼       ▼        ▼         ▼         ▼         ▼        ▼        ▼
 chat    monitor  heartbeat calendar finance  nutrition datasette landing
 :9094   :9091    :9092     :9093    :9096    :9097     :8001    :9095
   │       │        │
   ▼       ▼        ▼
┌─────────────────────────┐     ┌──────────────────────────────┐
│  core-api :8000         │     │  openclaw :18789             │
│  (internal hub:         │◄────│  (AI agent container)        │
│   Docker ops, skills,   │     │  ghcr.io/openclaw/openclaw   │
│   sessions, exec)       │     └──────────┬───────────────────┘
└─────────────────────────┘                │
                                           ▼
                               ┌───────────────────────┐
                               │  LLM Provider(s)      │
                               │  • Google AI Studio    │
                               │  • OpenRouter          │
                               │  • Local llama-server  │
                               │    (optional, :8080)   │
                               └───────────────────────┘
```

**Data flow:**
- External traffic → **Traefik** (HTTPS termination, basic auth) → path-based routing to services
- **chat** service streams LLM completions from configured provider(s) and delegates skill/session state to **core-api**
- **openclaw** agent talks to **core-api** for skills, sessions, and system context
- **finance**, **nutrition**, and **calendar** persist data in SQLite at `/workspace` (host: `./openclaw-data/workspace/`)
- **datasette** provides a web UI to browse all SQLite databases

---

## Multi-Model Routing

The chat service includes a **keyword-based task classifier** that automatically routes requests to the best model for the job:

| Role | Model | Provider | Why |
|------|-------|----------|-----|
| **Default** (general chat, reasoning) | Gemini 3 Flash Preview | Google AI Studio | Highest-intelligence free model (AA score 46) |
| **Coding** (code gen, debug, refactor) | Qwen3-Coder | OpenRouter | SWE-bench SOTA — comparable to Claude Sonnet 4 |

### How It Works

The last user message is analyzed for coding signals:

- **Code blocks** — any message containing triple backticks
- **Coding actions** — "write code", "create a function", "build a script"
- **Debugging** — "debug this", "fix the bug", "resolve this error"
- **Refactoring** — "refactor this code", "optimize the function"
- **Error patterns** — "syntax error", "runtime error", "traceback"
- **Code review** — "code review", "review this code", "pull request"

If no coding signal is detected, the request goes to the **default model**. Each response includes a metadata SSE event indicating which model answered:

```
data: {"type": "model_info", "model": "gemini-3-flash-preview", "category": "default"}
```

Multi-model routing can be disabled by removing the `LLM_CODING_*` environment variables.

> See [`docs/external-llm-providers.md`](docs/external-llm-providers.md) for full provider setup, free tier limits, and model rankings.

---

## Services

All microservices are **Python 3.11 + FastAPI + Uvicorn**, built locally from `./services/<name>/`. Each is a single `app.py` file with an identical Dockerfile structure.

| Service | Port | Description |
|---------|------|-------------|
| **openclaw** | 18789 | AI agent container (`ghcr.io/openclaw/openclaw:latest`). Executes skills, manages conversations, and maintains persistent memory. |
| **chat** | 9094 | LLM chat UI and API. Streams completions from external providers, supports file uploads (PDF, DOCX, text), multi-model routing, exec tool calls, and context-window management. |
| **core-api** | 8000 | Internal hub — not exposed externally. Docker operations, skill loading, session management, system prompt assembly, and shell command execution. |
| **monitor** | 9091 | System dashboard displaying host metrics (CPU, RAM, disk), GPU stats (via NVIDIA runtime), and container health. Delegates Docker data to core-api. |
| **heartbeat** | 9092 | Proactive agent poller. Views and triggers OpenClaw heartbeat tasks, reads `HEARTBEAT.md` checklist, and delegates Docker exec to core-api. |
| **calendar** | 9093 | Google Calendar integration. Event CRUD, weekly digest generation, token refresh. Stores config in `/workspace/calendar-data.json`. |
| **landing** | 9095 | Dashboard landing page linking to all services. Serves a static `index.html`. |
| **finance** | 9096 | Personal expense & income tracker with credit card tracking. SQLite-backed with 3,700+ transactions. Imports from seed data on first run. Web UI + full REST API. |
| **nutrition** | 9097 | Calorie & macro tracker. SQLite-backed with ~130 seeded Philippine dishes. Searches Open Food Facts and USDA FoodData Central for food data. Web UI + REST API. |
| **datasette** | 8001 | [Datasette](https://datasette.io/) instance providing a web-based SQL browser for all SQLite databases in `/workspace`. |
| **llama** | 8080 | *(Optional)* Local LLM server via [llama.cpp](https://github.com/ggml-org/llama.cpp). Serves Qwen3.5-9B with full GPU offload. Can be stopped to free VRAM when using external providers. |
| **traefik** | 80/443 | Reverse proxy. HTTPS via Let's Encrypt (DuckDNS DNS challenge), path-based routing, basic auth, security headers, rate limiting, gzip compression. |

### Service Conventions

- Every service exposes `GET /health` or `GET /api/health` (some expose both)
- Inter-service communication uses Docker DNS (`http://core-api:8000`, `http://calendar:9093`, etc.)
- SQLite databases are stored in `/workspace` (host: `./openclaw-data/workspace/openclaw.db`)
- HTTP clients use `httpx.AsyncClient` initialized in FastAPI `lifespan` context
- Thread-safety: `core-api` uses `threading.Lock()` for shared state

---

## Custom Skills

Skills extend the OpenClaw agent's capabilities. Each lives in `custom-skills/<name>/` with a `SKILL.md` file.

| Skill | Emoji | Description |
|-------|-------|-------------|
| **self-admin** | 🛠️ | System health checks, service restarts, troubleshooting, architecture knowledge, and self-maintenance. |
| **calendar-assistant** | 📅 | Google Calendar management — event CRUD, weekly digest, schedule viewing, and reminders. |
| **finance-tracker** | 💰 | Live read/write access to personal finance database — expenses, income, balances, net worth, and transaction logging. |
| **nutrition-tracker** | 🥗 | Calorie & macro tracking — food logging, nutrition search (USDA + Open Food Facts), daily goals, and meal summaries. |
| **ph-credit-card-maximizer** | 💳 | Philippine credit card advisor — recommends the best card for any purchase based on a 10-card portfolio (rewards, miles, cashback). |
| **ph-investment-advisor** | 📈 | Philippine personal finance & investment advisor — MP2, REITs, digital banks, time deposits, PSEi stocks, US ETFs, and RTBs. |
| **travel-advisor** | ✈️ | Travel planning for Filipino travelers — flights, hotels, itineraries, visas, budget optimization, and travel hacks. |
| **media-downloader** | 📥 | Central router for digital media downloads — dispatches to the appropriate sub-skill based on media type. |
| **epub-downloader** | 📚 | Search and download free EPUB books from Project Gutenberg, Internet Archive, and Open Library. |

### Creating a New Skill

1. Create the directory: `mkdir custom-skills/my-skill`
2. Create `SKILL.md` wrapped in `` ```skill ``` `` fences with YAML frontmatter:
   - `name`: kebab-case, must match the folder name
   - `description`: starts with "Use when…", ends with `Triggers on: "keyword1", "keyword2"`
   - `version`: semver string
   - `metadata`: `{ "openclaw": { "emoji": "🔧" } }`
3. Restart the agent: `docker compose restart openclaw` (skills are **not** hot-reloaded)

> See [`custom-skills/SKILL_FORMAT.md`](custom-skills/SKILL_FORMAT.md) for the full format specification.

---

## Quick Start

### Prerequisites

- **Docker** and **Docker Compose** (v2+)
- **Linux**, **macOS**, or **WSL2** on Windows
- A free API key from [Google AI Studio](https://aistudio.google.com/apikey) (for the default LLM)
- *(Optional)* An [OpenRouter](https://openrouter.ai/keys) API key for the coding model
- *(Optional)* NVIDIA GPU + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/) for the local llama-server

### Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-org/openclaw-local.git
   cd openclaw-local
   ```

2. **Create your environment file:**
   ```bash
   cp .env.example .env   # or create .env manually
   ```

   Edit `.env` with your API keys:
   ```env
   # Default LLM Provider (Google AI Studio — free)
   LLM_API_BASE=https://generativelanguage.googleapis.com/v1beta/openai
   LLM_API_KEY=your-google-ai-studio-key
   LLM_MODEL=gemini-3-flash-preview
   LLM_CTX_WINDOW=1048576

   # Coding Provider (OpenRouter — optional)
   LLM_CODING_URL=https://openrouter.ai/api/v1/chat/completions
   LLM_CODING_KEY=your-openrouter-key
   LLM_CODING_MODEL=qwen/qwen3-coder:free

   # Traefik (required for HTTPS)
   DUCKDNS_TOKEN=your-duckdns-token

   # Optional
   CORS_ORIGIN=https://your-domain.duckdns.org
   USDA_API_KEY=your-usda-key   # or leave blank for DEMO_KEY
   ```

3. **Create the Traefik auth file:**
   ```bash
   sudo apt install apache2-utils   # or brew install httpd on macOS
   htpasswd -c traefik/.htpasswd your-username
   ```

4. **Start the stack:**
   ```bash
   docker compose up -d
   ```

5. **Verify:**
   ```bash
   # Check openclaw agent
   curl -sf http://localhost:18789/health

   # Check all containers
   docker compose ps
   ```

> **Note:** If you don't have an NVIDIA GPU, remove or comment out the `llama` service and the `runtime: nvidia` line from `docker-compose.yml` before starting. The stack works fully with external LLM providers.

---

## Configuration

### Environment Variables (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `LLM_API_BASE` | Yes | OpenAI-compatible base URL for the default LLM provider |
| `LLM_API_KEY` | Yes | API key for the default LLM provider |
| `LLM_MODEL` | Yes | Model name for the default provider (e.g., `gemini-3-flash-preview`) |
| `LLM_CTX_WINDOW` | No | Context window size for history trimming (default: `32768`) |
| `LLM_CODING_URL` | No | Full completions URL for the coding provider |
| `LLM_CODING_KEY` | No | API key for the coding provider |
| `LLM_CODING_MODEL` | No | Coding model name (e.g., `qwen/qwen3-coder:free`) |
| `DUCKDNS_TOKEN` | Yes* | DuckDNS token for Let's Encrypt TLS cert renewal (*required for HTTPS) |
| `CORS_ORIGIN` | No | Allowed CORS origin for the chat service |
| `USDA_API_KEY` | No | USDA FoodData Central key; falls back to `DEMO_KEY` (rate-limited) |

### Traefik Routing

All external traffic is routed by path prefix over HTTPS with basic auth:

| Path Prefix | Service | Internal URL |
|-------------|---------|-------------|
| `/chat` | Chat UI & API | `http://chat:9094` |
| `/monitor` | System Dashboard | `http://monitor:9091` |
| `/heartbeat` | Heartbeat Poller | `http://heartbeat:9092` |
| `/calendar` | Calendar | `http://calendar:9093` |
| `/finance` | Finance Tracker | `http://finance:9096` |
| `/nutrition` | Nutrition Tracker | `http://nutrition:9097` |
| `/data` | Datasette (DB browser) | `http://datasette:8001` |
| `/` (catch-all) | Landing Page | `http://landing:9095` |

To add a new routed service, update both `traefik/dynamic.yml` (router + stripPrefix middleware + service) and `docker-compose.yml`.

---

## Common Commands

```bash
# ── Lifecycle ────────────────────────────────────────────────────
docker compose up -d                          # Start the full stack
docker compose down                           # Stop all services
docker compose restart openclaw               # Reload skills (after editing custom-skills/)

# ── Rebuild a single service (after code changes) ───────────────
docker compose build chat && docker compose up -d --no-deps chat

# ── Logs ─────────────────────────────────────────────────────────
docker compose logs -f chat                   # Follow a specific service
docker compose logs --tail=50 core-api        # Last 50 lines

# ── Health Checks ────────────────────────────────────────────────
curl -sf http://localhost:18789/health        # openclaw agent
docker compose ps                             # All container statuses

# ── Local LLM (optional) ────────────────────────────────────────
docker compose stop llama                     # Free GPU VRAM when using external providers
docker compose start llama                    # Restart the local LLM
nvidia-smi                                    # Check GPU usage
```

---

## Project Structure

```
openclaw-local/
├── docker-compose.yml              # Full stack orchestration
├── .env                            # Secrets & LLM provider config (not committed)
│
├── services/                       # All microservices (Python FastAPI)
│   ├── chat/                       #   LLM chat UI + multi-model routing
│   ├── core-api/                   #   Internal hub (Docker ops, skills, sessions)
│   ├── monitor/                    #   System/GPU dashboard
│   ├── heartbeat/                  #   Proactive agent poller
│   ├── calendar/                   #   Google Calendar integration
│   ├── landing/                    #   Dashboard landing page
│   ├── finance/                    #   Expense & income tracker
│   ├── nutrition/                  #   Calorie & macro tracker
│   └── datasette/                  #   SQLite web browser
│
├── custom-skills/                  # OpenClaw agent skills
│   ├── SKILL_FORMAT.md             #   Skill authoring guide
│   ├── self-admin/                 #   🛠️  System maintenance
│   ├── calendar-assistant/         #   📅  Calendar management
│   ├── finance-tracker/            #   💰  Finance operations
│   ├── nutrition-tracker/          #   🥗  Nutrition tracking
│   ├── ph-credit-card-maximizer/   #   💳  Credit card optimizer
│   ├── ph-investment-advisor/      #   📈  Investment advice
│   ├── travel-advisor/             #   ✈️   Travel planning
│   ├── media-downloader/           #   📥  Media download router
│   └── epub-downloader/            #   📚  Ebook downloader
│
├── traefik/                        # Reverse proxy config
│   ├── traefik.yml                 #   Static config (entrypoints, cert resolver)
│   ├── dynamic.yml                 #   Dynamic config (routers, middlewares, services)
│   ├── .htpasswd                   #   Basic auth credentials (not committed)
│   └── acme/                       #   Let's Encrypt certificates
│
├── openclaw-data/                  # Agent persistent data (mounted as /home/node/.openclaw)
│   └── workspace/                  #   SQLite databases, memory files, configs
│
├── models/                         # Local LLM model files (GGUF)
├── scripts/                        # Migration and setup scripts
├── docs/                           # Additional documentation
│   ├── external-llm-providers.md   #   LLM provider setup & model rankings
│   └── database-architecture.md    #   SQLite schema documentation
└── tests/                          # Test suite
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [`docs/external-llm-providers.md`](docs/external-llm-providers.md) | Comprehensive guide to external LLM providers — Google AI Studio, Groq, OpenRouter — with free tier limits, model rankings, and multi-model routing details. |
| [`docs/database-architecture.md`](docs/database-architecture.md) | SQLite database schema documentation for finance, nutrition, and other services. |
| [`custom-skills/SKILL_FORMAT.md`](custom-skills/SKILL_FORMAT.md) | Specification for creating custom OpenClaw skills. |

---

## License

This project is licensed under the [MIT License](LICENSE).
