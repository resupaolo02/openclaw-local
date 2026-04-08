# Architecture Reference

## System Overview

**Host:** Single Linux machine with NVIDIA GPU
**Domain:** `openclaw-frostbite.duckdns.org` (HTTPS via Let's Encrypt + DuckDNS)
**Base directory:** `/home/resupaolo/openclaw-local/`
**LLM:** Qwen3.5-9B-Q4_K_M (llama.cpp, CUDA, 32K context, Q4_K_M quantization)

## Service Map

```
                    Internet
                       │
                   ┌───▼───┐
                   │Traefik │  :80 (→ :443 redirect)
                   │  :443  │  HTTPS + basic auth
                   └───┬────┘
          ┌────────────┼────────────────────────────┐
          │ path-based routing (prefix stripped)      │
          │                                          │
    /chat → chat:9094          /monitor → monitor:9091
    /finance → finance:9096    /heartbeat → heartbeat:9092
    /nutrition → nutrition:9097  /calendar → calendar:9093
    / (catch-all) → landing:9095
          │
          │  Internal-only (no Traefik route)
          ├──── core-api:8000
          ├──── llama:8080
          └──── openclaw:18789, :18791
```

## Service Details

| Service | Port | Image/Build | Database | Depends On | Memory Limit |
|---------|------|-------------|----------|------------|--------------|
| llama | 8080 | `ghcr.io/ggml-org/llama.cpp:server-cuda` | — | GPU | 16G |
| openclaw | 18789 | `ghcr.io/openclaw/openclaw:latest` | JSONL sessions | llama | 4G |
| core-api | 8000 | `./services/core-api` | — (reads sessions) | llama, openclaw | 512M |
| monitor | 9091 | `./services/monitor` (nvidia/cuda base) | — | core-api | 512M |
| heartbeat | 9092 | `./services/heartbeat` | JSON files | core-api | 256M |
| calendar | 9093 | `./services/calendar` | JSON files | — | 256M |
| chat | 9094 | `./services/chat` | — | llama, core-api | 512M |
| landing | 9095 | `./services/landing` | — | — | 128M |
| finance | 9096 | `./services/finance` | SQLite (`finance.db`) | — | 256M |
| nutrition | 9097 | `./services/nutrition` | SQLite (`nutrition.db`) | — | 256M |

## Data Flow

```
User (Telegram/Web) → openclaw → llama-server (LLM inference)
                         │
                         ├→ core-api (skills, sessions, Docker ops)
                         └→ exec curl commands to any internal service

Web UI (chat page) → chat service → llama-server (streaming)
                         └→ core-api (skills, sessions, system prompt)

monitor → core-api → Docker socket + llama health
heartbeat → core-api → openclaw exec (heartbeat commands)
```

## Inter-Service Communication

All services communicate over Docker DNS names (e.g., `http://core-api:8000`).

| Caller | Calls | Purpose |
|--------|-------|---------|
| openclaw | llama:8080 | LLM inference (OpenAI-compatible API) |
| core-api | llama:8080 | LLM health check |
| core-api | Docker socket | Container stats, exec |
| chat | llama:8080 | Streaming completions |
| chat | core-api:8000 | Skills, sessions, system prompt, exec |
| monitor | core-api:8000 | Container stats, LLM status, sessions |
| heartbeat | core-api:8000 | Exec OpenClaw CLI commands |

**Standalone services** (no inter-service calls): calendar, finance, nutrition, landing

## File System Layout

```
/home/resupaolo/openclaw-local/
├── .env                          # Secrets (DUCKDNS_TOKEN, CORS_ORIGIN, TRAEFIK_BASIC_AUTH)
├── docker-compose.yml            # All service definitions
├── models/
│   └── Qwen3.5-9B-Q4_K_M.gguf   # LLM model weights
├── custom-skills/                # Mounted as /app/custom-skills/ in openclaw
│   ├── SKILL_FORMAT.md           # Skill creation guide
│   ├── self-admin/               # This skill
│   ├── calendar-assistant/
│   ├── epub-downloader/
│   ├── finance-tracker/
│   ├── media-downloader/
│   ├── nutrition-tracker/
│   ├── ph-credit-card-maximizer/
│   ├── ph-investment-advisor/
│   └── travel-advisor/
├── services/                     # Microservice source code
│   ├── core-api/app.py
│   ├── monitor/app.py
│   ├── heartbeat/app.py
│   ├── calendar/app.py
│   ├── chat/app.py
│   ├── landing/app.py
│   ├── finance/app.py
│   └── nutrition/app.py
├── openclaw-data/                # Mounted as /home/node/.openclaw in openclaw
│   ├── workspace/                # Agent workspace (also mounted standalone)
│   │   ├── AGENTS.md             # Behavioral rules
│   │   ├── SOUL.md               # Agent identity/personality
│   │   ├── USER.md               # User context
│   │   ├── IDENTITY.md           # Name, timezone, owner
│   │   ├── TOOLS.md              # Environment-specific notes
│   │   ├── MEMORY.md             # Curated long-term memory
│   │   ├── HEARTBEAT.md          # Heartbeat task checklist
│   │   ├── BOOTSTRAP.md          # First-run setup (processed)
│   │   ├── memory/               # Daily session logs (YYYY-MM-DD.md)
│   │   ├── finance.db            # Finance SQLite database
│   │   ├── nutrition.db          # Nutrition SQLite database
│   │   ├── calendar-data.json    # Calendar config + weekly digest cache
│   │   ├── google-credentials.json
│   │   └── google-token.json
│   ├── agents/                   # OpenClaw agent configs
│   ├── skills/                   # OpenClaw internal skills
│   ├── logs/                     # OpenClaw logs
│   └── openclaw.json             # Main OpenClaw config
├── traefik/
│   ├── traefik.yml               # Traefik entrypoints, TLS config
│   ├── dynamic.yml               # Route rules, middlewares, services
│   ├── .htpasswd                 # Basic auth credentials
│   └── acme/                     # Let's Encrypt certificates
└── scripts/
    ├── approve-latest-browser-pairing.sh
    └── setup-windows-access.ps1
```

## Docker Container Paths (Inside openclaw)

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `./openclaw-data` | `/home/node/.openclaw` | Agent data, configs, sessions |
| `./workspace` | `/home/node/workspace` | Shared workspace |
| `./custom-skills` | `/app/custom-skills` | All custom skills |
| `./models` | `/models` (llama only) | LLM model weights |

## Traefik Routing

All external traffic → `openclaw-frostbite.duckdns.org` (HTTPS)
- TLS: Let's Encrypt via DuckDNS DNS challenge
- Auth: HTTP basic auth on all routes (`.htpasswd`)
- Path prefix is stripped before forwarding to services

| Path | → Service | Middleware |
|------|-----------|-----------|
| `/chat` | `http://chat:9094` | basicAuth + stripPrefix |
| `/monitor` | `http://monitor:9091` | basicAuth + stripPrefix |
| `/heartbeat` | `http://heartbeat:9092` | basicAuth + stripPrefix |
| `/calendar` | `http://calendar:9093` | basicAuth + stripPrefix |
| `/finance` | `http://finance:9096` | basicAuth + stripPrefix |
| `/nutrition` | `http://nutrition:9097` | basicAuth + stripPrefix |
| `/` (catch-all) | `http://landing:9095` | basicAuth |

## LLM Configuration

- **Model:** Qwen3.5-9B-Q4_K_M.gguf
- **Context size:** 32768 tokens
- **GPU layers:** 99 (fully offloaded)
- **Threads:** 6
- **Parallel slots:** 1
- **Reasoning budget:** 1024
- **Flash attention:** on
- **Jinja templates:** enabled
- **API:** OpenAI-compatible (`/v1/chat/completions`, `/v1/models`, `/health`)

## Environment Variables

| Variable | Used By | Purpose |
|----------|---------|---------|
| `DUCKDNS_TOKEN` | traefik | TLS cert renewal via DuckDNS |
| `CORS_ORIGIN` | chat | Allowed CORS origin |
| `TRAEFIK_BASIC_AUTH` | .env | Basic auth config reference |
| `OPENAI_API_BASE` | openclaw | LLM endpoint (`http://llama:8080/v1`) |
| `OPENAI_API_KEY` | openclaw | Set to `not-needed` (local LLM) |
| `LLM_PROVIDER` | openclaw | `openai` |
| `LLM_MODEL` | openclaw | `qwen3.5-9b` |
| `LLM_BASE_URL` | core-api, chat | `http://llama:8080` |
| `CORE_API_URL` | monitor, heartbeat, chat | `http://core-api:8000` |
| `USDA_API_KEY` | nutrition | USDA FoodData Central key (default: `DEMO_KEY`) |

## Skill Routing

Skills are loaded at openclaw startup from `/app/custom-skills/`. Each skill's `description` field is used for intent routing — the LLM reads all descriptions to decide which skill to invoke.

**Current skills (8):**

| Skill | Emoji | Triggers On |
|-------|-------|-------------|
| `self-admin` | 🔧 | system health, restart, rebuild, troubleshoot, architecture |
| `calendar-assistant` | 📅 | calendar, schedule, events, reminders |
| `epub-downloader` | 📚 | download book, find ebook, epub |
| `finance-tracker` | 💰 | expenses, income, spending, accounts, net worth |
| `media-downloader` | 📥 | download media (routes to epub-downloader) |
| `nutrition-tracker` | 🥗 | calories, macros, food log, nutrition |
| `ph-credit-card-maximizer` | 💳 | credit cards, rewards, cashback, promos |
| `ph-investment-advisor` | 📈 | investments, savings, digital banks, MP2, REITs |
| `travel-advisor` | ✈️ | travel, flights, itineraries, visas |

## Database Schemas

### Finance (`/workspace/finance.db`)

```sql
transactions (id, date, time, account, category, subcategory, type, amount, php,
              currency, expense_type, payment_status, personal_amount,
              non_personal_amount, description, note, created_at, updated_at)
-- type: Exp. | Income | Transfer-In | Transfer-Out
-- payment_status: Paid | Unpaid (for credit card tracking)

budgets (id, month, category, amount, UNIQUE(month, category))

accounts (id, name, group_name, icon, sort_order, created_at)
```

### Nutrition (`/workspace/nutrition.db`)

```sql
food_log (id, date, time, meal_type, food_name, serving_size, calories,
          protein_g, carbs_g, fat_g, fiber_g, sugar_g, sodium_mg,
          notes, created_at, updated_at)
-- meal_type: breakfast | lunch | dinner | snack

daily_goals (calories=2000, protein_g=150, carbs_g=200, fat_g=65, fiber_g=25)

food_database (id, external_id, source, food_name, brand, serving_size,
               serving_g, calories, protein_g, carbs_g, fat_g, fiber_g,
               sugar_g, sodium_mg, tags, created_at, updated_at)
-- source: seeded (~130 PH dishes) | openfoodfacts | usda | custom
```
