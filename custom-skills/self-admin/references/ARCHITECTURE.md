# Architecture Reference

## System Overview

**Host:** Single Linux machine with NVIDIA GPU
**Domain:** `openclaw-frostbite.duckdns.org` (HTTPS via Let's Encrypt + DuckDNS)
**Base directory:** `/home/resupaolo/openclaw-local/`
**LLM:** Multi-model (Gemini Flash primary, OpenRouter Qwen3-Coder for coding)

## Architecture: Monolith Hub (4 containers)

```
                    Internet
                       │
                   ┌───▼───┐
                   │Traefik │  :80 (→ :443 redirect)
                   │  :443  │  HTTPS + basic auth
                   └───┬────┘
          ┌────────────┼──────────────┐
          │  path-based routing       │
          │  (full path forwarded)    │
          │                           │
          ▼                           ▼
      hub:8000                   datasette:8001
   (single FastAPI)              (optional)
          │
          ├── / (landing)
          ├── /chat/*
          ├── /finance/*
          ├── /nutrition/*
          ├── /calendar/*
          ├── /monitor/*
          ├── /heartbeat/*
          └── /health, /containers, /exec, /sessions/*, /skills, /maintenance/*
          │
          │  Internal-only
          └──── openclaw:18789, :18791
```

## Container Details

| Container | Port | Image/Build | Purpose | Memory |
|-----------|------|-------------|---------|--------|
| hub | 8000 | `./services/hub` (CUDA base) | All APIs + web UIs | 1G |
| openclaw | 18789 | `ghcr.io/openclaw/openclaw:latest` | AI agent | 4G |
| traefik | 80,443 | `traefik:v3.4` | HTTPS reverse proxy | 256M |
| datasette | 8001 | `datasetteproject/datasette` | SQLite browser (optional) | 256M |

## Data Flow

```
User (Telegram/Web) → openclaw → Gemini/OpenRouter (external LLM)
                         │
                         └→ exec curl http://hub:8000/... (all APIs in one place)

Web UI → Traefik → hub:8000/{service}/... (no prefix stripping needed)

All internal: hub handles everything in-process (no inter-service HTTP)
```

## Hub Router Modules

All modules live in `services/hub/routers/`. Each is a FastAPI APIRouter:

| Module | Prefix | Source | Database |
|--------|--------|--------|----------|
| core | / (root) | Docker ops, exec, sessions, skills | — |
| chat | /chat | LLM chat UI + streaming API | — |
| finance | /finance | Transaction CRUD, summaries | SQLite (openclaw.db) |
| nutrition | /nutrition | Food log, search, goals | SQLite (openclaw.db) |
| calendar | /calendar | Google Calendar integration | JSON files |
| monitor | /monitor | System/GPU metrics dashboard | — (reads /proc) |
| heartbeat | /heartbeat | Heartbeat poller | — |

## File System Layout

```
/home/resupaolo/openclaw-local/
├── .env                          # Secrets (API keys, auth, DuckDNS)
├── docker-compose.yml            # 4-container stack
├── services/
│   └── hub/                      # Monolith service
│       ├── app.py                # Main FastAPI app (mounts all routers)
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── routers/
│       │   ├── core.py           # Docker ops, exec, sessions, skills
│       │   ├── chat.py           # LLM chat with multi-model routing
│       │   ├── finance.py        # Finance tracker
│       │   ├── nutrition.py      # Nutrition tracker
│       │   ├── calendar.py       # Google Calendar
│       │   ├── monitor.py        # System metrics
│       │   └── heartbeat.py      # Heartbeat poller
│       └── static/               # Web UI HTML files
│           ├── landing/index.html
│           ├── chat/index.html
│           ├── finance/index.html
│           ├── nutrition/index.html
│           ├── calendar/index.html
│           ├── monitor/index.html
│           └── heartbeat/index.html
├── custom-skills/                # Mounted in openclaw
├── openclaw-data/                # Agent data + workspace
│   ├── workspace/
│   │   ├── openclaw.db           # Shared SQLite (finance + nutrition)
│   │   ├── calendar-data.json
│   │   ├── google-credentials.json
│   │   ├── google-token.json
│   │   ├── AGENTS.md, SOUL.md, USER.md, TOOLS.md, MEMORY.md
│   │   └── memory/              # Daily session logs
│   └── openclaw.json            # Agent config (providers, models)
├── traefik/
│   ├── traefik.yml, dynamic.yml, .htpasswd
│   └── acme/                    # TLS certificates
└── docs/
    ├── DEPLOYMENT.md
    └── MAINTENANCE.md
```

## Docker Container Paths

| Host Path | Hub Container Path | OpenClaw Container Path |
|-----------|-------------------|------------------------|
| `./openclaw-data/workspace/` | `/workspace/` | `/home/node/.openclaw/workspace/` |
| `./custom-skills/` | — | `/app/custom-skills/` |
| `./openclaw-data/` | — | `/home/node/.openclaw/` |
| `/var/run/docker.sock` | `/var/run/docker.sock` | — |
| `/proc` | `/host/proc` (read-only) | — |

## Traefik Routing

All external traffic → `openclaw-frostbite.duckdns.org` (HTTPS)
- TLS: Let's Encrypt via DuckDNS DNS challenge
- Auth: HTTP basic auth on all routes (`.htpasswd`)
- **Full path forwarded** (no prefix stripping) — hub handles routing internally

| Path | → Target | Middleware |
|------|----------|-----------|
| `/chat` | `hub:8000` | basicAuth |
| `/finance` | `hub:8000` | basicAuth |
| `/nutrition` | `hub:8000` | basicAuth |
| `/calendar` | `hub:8000` | basicAuth |
| `/monitor` | `hub:8000` | basicAuth |
| `/heartbeat` | `hub:8000` | basicAuth |
| `/datasette` | `datasette:8001` | basicAuth + stripPrefix |
| `/` (catch-all) | `hub:8000` | basicAuth |

## LLM Configuration

Multi-model routing via external providers (configured in `openclaw.json`):

| Provider | Model | Use Case | Free Tier |
|----------|-------|----------|-----------|
| Gemini (Google AI Studio) | gemini-2.5-flash-preview-05-20 | Default — general tasks | 15 RPM / 1500 RPD |
| OpenRouter | qwen/qwen3-coder | Coding & technical tasks | 20 RPM / 50 RPD |

Chat service auto-routes coding tasks to Qwen3-Coder based on keyword detection.

## Environment Variables

| Variable | Used By | Purpose |
|----------|---------|---------|
| `DUCKDNS_TOKEN` | traefik | TLS cert renewal |
| `CORS_ORIGIN` | hub | Allowed CORS origin |
| `GEMINI_API_KEY` | hub (chat) | Google AI Studio API key |
| `OPENROUTER_API_KEY` | hub (chat) | OpenRouter API key |
| `USDA_API_KEY` | hub (nutrition) | USDA FoodData Central key |
| `GOOGLE_CREDENTIALS` | hub (calendar) | Google Calendar OAuth |
| `WORKSPACE_DIR` | hub | Path to workspace volume |

## Skills

9 custom skills loaded from `/app/custom-skills/`:

| Skill | Emoji | Triggers On |
|-------|-------|-------------|
| `self-admin` | 🔧 | system health, restart, rebuild, troubleshoot |
| `calendar-assistant` | 📅 | calendar, schedule, events, reminders |
| `epub-downloader` | 📚 | download book, find ebook, epub |
| `finance-tracker` | 💰 | expenses, income, spending, accounts |
| `media-downloader` | 📥 | download media |
| `nutrition-tracker` | 🥗 | calories, macros, food log, nutrition |
| `ph-credit-card-maximizer` | 💳 | credit cards, rewards, cashback |
| `ph-investment-advisor` | 📈 | investments, savings, digital banks |
| `travel-advisor` | ✈️ | travel, flights, itineraries |

## Database Schemas

All in `/workspace/openclaw.db`:

### Finance Tables
```sql
transactions (id, date, time, account, category, subcategory, type, amount, php,
              currency, expense_type, payment_status, personal_amount,
              non_personal_amount, description, note, created_at, updated_at)
budgets (id, month, category, amount, UNIQUE(month, category))
accounts (id, name, group_name, icon, sort_order, created_at)
```

### Nutrition Tables
```sql
food_log (id, date, time, meal_type, food_name, serving_size, calories,
          protein_g, carbs_g, fat_g, fiber_g, sugar_g, sodium_mg,
          notes, created_at, updated_at)
daily_goals (calories=2000, protein_g=150, carbs_g=200, fat_g=65, fiber_g=25)
food_database (id, external_id, source, food_name, brand, serving_size,
               serving_g, calories, protein_g, carbs_g, fat_g, fiber_g,
               sugar_g, sodium_mg, tags, created_at, updated_at)
```
