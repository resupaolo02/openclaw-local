# API Reference — All Services

All services use Docker DNS names internally. Base URLs:
- `http://core-api:8000`
- `http://llama:8080`
- `http://monitor:9091`
- `http://heartbeat:9092`
- `http://calendar:9093`
- `http://chat:9094`
- `http://landing:9095`
- `http://finance:9096`
- `http://nutrition:9097`

---

## Core API (:8000)

### Health & System
```bash
GET /health                    # Health check
GET /containers                # List containers (openclaw, llama-server) with status/uptime
GET /containers/stats          # Deep stats: CPU%, memory, I/O, network
GET /llm/status                # LLM health + model info from llama-server
```

### Sessions
```bash
GET /sessions/overview         # Summary: agent count, active/total, recent errors
GET /sessions/list             # All sessions with metadata, message count, preview
GET /sessions/{id}/messages    # Conversation history for a session
DELETE /sessions/{id}          # Delete session (moves to .deleted.timestamp)
```

### Skills & Prompt
```bash
GET /skills                    # List all available skills (from /custom-skills + workspace)
GET /system-prompt             # Assembled system prompt (cached 60s)
```

### Exec
```bash
POST /exec                     # Execute command in openclaw container
# Body: {"cmd": "openclaw system heartbeat last --json"}
```

---

## LLM / llama-server (:8080)

```bash
GET /health                    # Health check (used by Docker healthcheck)
GET /v1/models                 # List available models
POST /v1/chat/completions      # OpenAI-compatible chat completions
# Body: {"model": "qwen3.5-9b", "messages": [...], "stream": true}
```

---

## Monitor (:9091)

```bash
GET /                          # HTML dashboard
GET /api/status                # Full system status (10s cache):
#   host: {ram_gb, ram_pct, cpu_pct, load_avg, temp, disk, network}
#   gpu: {name, utilization_pct, vram_used_mb, vram_total_mb, temp, power_w, clock_mhz}
#   containers: [{name, status, uptime, cpu_pct, mem_mb}]
#   llm: {status, model, ctx_size}
#   sessions: {total, active, recent_errors}
#   overall: "healthy" | "degraded" | "down"
GET /api/health                # Health check
```

---

## Heartbeat (:9092)

```bash
GET /                          # HTML dashboard
GET /api/heartbeat             # Status: parsed tasks from HEARTBEAT.md, last event, running tasks
POST /api/heartbeat/trigger    # Manually trigger heartbeat
GET /api/health                # Health check
```

---

## Calendar (:9093)

### Events
```bash
GET /api/calendar/events               # List events (default: next 60 days)
GET /api/calendar/events?days=30       # Custom range

POST /api/calendar/events              # Create event
# Body: {
#   "title": "Meeting",
#   "date": "2026-04-10",          (YYYY-MM-DD, required)
#   "time": "14:00",               (HH:MM 24h, optional — omit for all-day)
#   "end_time": "15:00",           (optional)
#   "location": "Office",          (optional)
#   "description": "Notes",        (optional)
#   "reminder_minutes": 30         (optional, default: 30)
# }

DELETE /api/calendar/events/{event_id} # Delete event (Google event ID)
```

### Weekly Digest
```bash
GET /api/calendar/week                 # Remaining week digest
GET /api/calendar/week?mode=next       # Next week digest
POST /api/calendar/week/trigger        # Force regenerate digest
```

```bash
GET /                                  # HTML dashboard
GET /api/health                        # Health check
```

---

## Chat (:9094)

```bash
GET /                                  # HTML chat UI
POST /api/chat                         # Stream chat completion (SSE)
# Body: {
#   "message": "Hello",
#   "history": [{"role": "user", "content": "..."}],
#   "session_id": "optional"
# }

POST /api/chat/upload                  # Upload & extract text from file
# Body: multipart/form-data with file (PDF, DOCX, TXT, MD, CSV, JSON — 10MB limit)

GET /api/supported-files               # List supported file types
GET /api/skills                        # Proxy → core-api /skills
GET /api/sessions/list                 # Proxy → core-api /sessions/list
GET /api/sessions/{id}/messages        # Proxy → core-api /sessions/{id}/messages
DELETE /api/sessions/{id}              # Proxy → core-api /sessions/{id}
GET /api/health                        # Health check
```

---

## Finance (:9096)

### Transactions
```bash
GET /api/transactions                          # List (paginated)
# Params: month=YYYY-MM, account=, type=, category=, search=, page=, per_page=
GET /api/transactions/{id}                     # Single transaction
POST /api/transactions                         # Create
# Body: {date, time, account, category, subcategory, type, amount, currency,
#        expense_type, payment_status, description, note, personal_amount, non_personal_amount}
PUT /api/transactions/{id}                     # Update (partial)
DELETE /api/transactions/{id}                  # Delete
```

### Summary & Trends
```bash
GET /api/summary                               # Month-to-date: expenses, income, net
# Params: month=YYYY-MM
GET /api/monthly-trend                         # 12-month expense/income trend
GET /api/category-breakdown                    # Expenses by category (current month)
# Params: month=YYYY-MM
```

### Accounts
```bash
GET /api/accounts                              # All accounts with computed balances
GET /api/account-records                       # Balance history snapshots
POST /api/account-records                      # Record balance snapshot
# Body: {account_id, balance, recorded_at}
PUT /api/account-records/{account_id}          # Update snapshot
DELETE /api/account-records/{account_id}       # Delete snapshot
```

### Credit Cards
```bash
GET /api/credit-cards/summary                  # Outstanding, paid, pending totals
GET /api/credit-cards/transactions             # CC transactions with payment status
# Params: card=, status=paid|unpaid, month=YYYY-MM
GET /api/credit-cards/cards                    # List credit card accounts
GET /api/credit-cards/monthly-trend            # CC payment trends
POST /api/credit-cards/mark-paid               # Mark transactions as paid
# Body: {transaction_ids: [1, 2, 3]}
```

### Budgets
```bash
GET /api/budgets                               # All budgets
PUT /api/budgets                               # Upsert budget
# Body: {month, category, amount}
DELETE /api/budgets/{id}                       # Delete budget
```

### Other
```bash
GET /api/meta                                  # Metadata: categories, accounts, expense types
POST /api/installments                         # Create installment plan
# Body: {base transaction fields + installment_months, start_date}
GET /api/export/csv                            # CSV export
# Params: start_date=YYYY-MM-DD, end_date=YYYY-MM-DD
GET /                                          # HTML dashboard
GET /api/health                                # Health check
```

---

## Nutrition (:9097)

### Food Database
```bash
GET /api/foods/search?q=chicken&limit=50       # Search foods (local DB + external APIs)
GET /api/foods/barcode/{barcode}               # Barcode lookup (Open Food Facts)
GET /api/foods?source=seeded&limit=50          # List by source
# source: seeded | openfoodfacts | usda | custom
GET /api/foods/{id}                            # Food details
POST /api/foods                                # Create custom food
# Body: {food_name, brand, serving_size, serving_g, calories,
#        protein_g, carbs_g, fat_g, fiber_g, sugar_g, sodium_mg, tags}
PUT /api/foods/{id}                            # Update food
DELETE /api/foods/{id}                         # Delete food
```

### Food Log
```bash
POST /api/log/quick                            # Quick log from food database
# Body: {food_id, date, meal_type, servings OR grams}
GET /api/log?date=2026-04-06                   # Day's food log
GET /api/log/{id}                              # Single entry
POST /api/log                                  # Manual log entry
# Body: {date, time, meal_type, food_name, serving_size, calories,
#        protein_g, carbs_g, fat_g, fiber_g, sugar_g, sodium_mg, notes}
PUT /api/log/{id}                              # Update entry
DELETE /api/log/{id}                           # Delete entry
```

### Summary & Goals
```bash
GET /api/summary?date=2026-04-06               # Daily totals vs goals
GET /api/weekly-trend                          # Weekly average intake
GET /api/goals                                 # Current daily goals
PUT /api/goals                                 # Update goals
# Body: {calories, protein_g, carbs_g, fat_g, fiber_g}
```

### Other
```bash
GET /api/export/csv                            # CSV export
# Params: start_date=YYYY-MM-DD, end_date=YYYY-MM-DD
GET /                                          # HTML dashboard
GET /api/health                                # Health check
```

---

## Landing (:9095)

```bash
GET /                          # HTML landing page
GET /api/health                # Health check
```
