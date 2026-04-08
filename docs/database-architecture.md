# OpenClaw Database Architecture

> Central database for all OpenClaw microservices with web-based query interface.

## Overview

All OpenClaw services share a single SQLite database (`openclaw.db`) located at:

```
Host:      ./openclaw-data/workspace/openclaw.db
Container: /workspace/openclaw.db
```

Services connect directly via `sqlite3.connect("/workspace/openclaw.db")`. A **Datasette** web UI is available at `/data` for ad-hoc SQL queries, browsing, and CSV export.

---

## Current Schema

### Finance Service Tables

#### `transactions` (primary data table)
| Column | Type | Default | Description |
|--------|------|---------|-------------|
| id | INTEGER | PK auto | Row ID |
| date | TEXT | required | Transaction date (YYYY-MM-DD) |
| time | TEXT | '00:00:00' | Transaction time |
| account | TEXT | '' | Account name (denormalized) |
| category | TEXT | '' | e.g. Food, Transportation, Utilities |
| note | TEXT | '' | Free text |
| type | TEXT | '' | 'Income', 'Income Balance', 'Exp.', 'Expense Balance', 'Transfer' |
| amount | REAL | 0.0 | Original currency amount |
| php | REAL | 0.0 | Amount in PHP (primary) |
| currency | TEXT | 'PHP' | ISO currency code |
| description | TEXT | '' | Short description |
| expense_type | TEXT | '' | 'Personal', 'Family', 'Friends' (denormalized) |
| payment_status | TEXT | '' | '' = unpaid, 'paid' = paid |
| personal_amount | REAL | 0.0 | Personal share (for split expenses) |
| non_personal_amount | REAL | 0.0 | Non-personal share |
| installment_num | INTEGER | 0 | Current installment number |
| installment_total | INTEGER | 0 | Total installments |
| account_id | INTEGER | NULL | FK → accounts.account_id |
| expense_type_id | INTEGER | NULL | FK → expense_types.expense_type_id |
| created_at | TEXT | datetime('now') | Row creation timestamp |
| updated_at | TEXT | datetime('now') | Last update timestamp |

**Indexes:** `date`, `account`, `type`, `category`, `expense_type`, `payment_status`, `account_id`, `expense_type_id`

#### `accounts`
| Column | Type | Default | Description |
|--------|------|---------|-------------|
| account_id | INTEGER | PK auto | Primary key |
| name | TEXT | required, UNIQUE | Account display name |
| group_name | TEXT | 'Other' | 'Card', 'Cash', 'E-Wallet', 'Savings', 'Investment', 'Loan', 'Other' |
| icon | TEXT | '💳' | Emoji icon |
| sort_order | INTEGER | 0 | Display ordering |
| created_at | TEXT | datetime('now') | Row creation timestamp |

#### `expense_types`
| Column | Type | Default | Description |
|--------|------|---------|-------------|
| expense_type_id | INTEGER | PK auto | Primary key |
| name | TEXT | required, UNIQUE | Type name (Personal, Family, Friends, Business, Work, Reimbursement) |
| created_at | TEXT | datetime('now') | Row creation timestamp |

---

### Nutrition Service Tables

#### `food_log`
| Column | Type | Default | Description |
|--------|------|---------|-------------|
| id | INTEGER | PK auto | Row ID |
| date | TEXT | required | Log date (YYYY-MM-DD) |
| time | TEXT | '00:00:00' | Meal time |
| meal_type | TEXT | 'snack' | 'breakfast', 'lunch', 'dinner', 'snack' |
| food_name | TEXT | required | What was eaten |
| serving_size | TEXT | '1 serving' | Portion description |
| calories | REAL | 0.0 | Kilocalories |
| protein_g | REAL | 0.0 | Grams of protein |
| carbs_g | REAL | 0.0 | Grams of carbs |
| fat_g | REAL | 0.0 | Grams of fat |
| fiber_g | REAL | 0.0 | Grams of fiber |
| sugar_g | REAL | 0.0 | Grams of sugar |
| sodium_mg | REAL | 0.0 | Milligrams of sodium |
| notes | TEXT | '' | Free text |
| created_at | TEXT | datetime('now') | Row creation timestamp |
| updated_at | TEXT | datetime('now') | Last update timestamp |

**Indexes:** `date`, `meal_type`

#### `food_database`
| Column | Type | Default | Description |
|--------|------|---------|-------------|
| id | INTEGER | PK auto | Row ID |
| external_id | TEXT | '' | USDA/OpenFoodFacts ID |
| source | TEXT | 'custom' | 'seeded', 'custom', 'usda_legacy', 'openfoodfacts' |
| food_name | TEXT | required | Food display name |
| brand | TEXT | '' | Brand name |
| serving_size | TEXT | '100g' | Serving description |
| serving_g | REAL | 100.0 | Serving weight in grams |
| calories | REAL | 0.0 | kcal per serving |
| protein_g | REAL | 0.0 | Protein per serving |
| carbs_g | REAL | 0.0 | Carbs per serving |
| fat_g | REAL | 0.0 | Fat per serving |
| fiber_g | REAL | 0.0 | Fiber per serving |
| sugar_g | REAL | 0.0 | Sugar per serving |
| sodium_mg | REAL | 0.0 | Sodium per serving |
| tags | TEXT | '' | Comma-separated tags |
| created_at | TEXT | datetime('now') | Row creation timestamp |
| updated_at | TEXT | datetime('now') | Last update timestamp |

**Indexes:** `food_name`, `(external_id, source)`, `source`

#### `daily_goals`
| Column | Type | Default | Description |
|--------|------|---------|-------------|
| id | INTEGER | PK auto | Row ID |
| calories | REAL | 2000.0 | Daily calorie target |
| protein_g | REAL | 150.0 | Daily protein target |
| carbs_g | REAL | 200.0 | Daily carbs target |
| fat_g | REAL | 65.0 | Daily fat target |
| fiber_g | REAL | 25.0 | Daily fiber target |
| created_at | TEXT | datetime('now') | Row creation timestamp |

---

## Web Query Interface (Datasette)

### Access
- **External:** `https://openclaw-frostbite.duckdns.org/data/`
- **Internal (Docker):** `http://datasette:8001/data/`
- Protected by the same HTTP basic auth as all other services
- Unlike other services, Datasette uses `base_url=/data/` instead of strip-prefix (no Traefik path stripping)

### Features
- **SQL Editor:** Write arbitrary SELECT queries against all tables
- **Table Browser:** Click any table to browse/filter/facet data
- **Canned Queries:** Pre-built useful queries (monthly summary, top categories, etc.)
- **Export:** CSV, JSON, and notebook formats
- **Read-only:** Cannot modify data through the web UI (safe)

### Example Queries

```sql
-- Monthly spending by category (last 6 months)
SELECT
  strftime('%Y-%m', date) AS month,
  category,
  ROUND(SUM(php), 2) AS total
FROM transactions
WHERE type IN ('Exp.', 'Expense Balance')
  AND date >= date('now', '-6 months')
GROUP BY month, category
ORDER BY month DESC, total DESC;

-- Cross-service: days where both finance and nutrition data exist
SELECT DISTINCT t.date, COUNT(DISTINCT t.id) AS txn_count, COUNT(DISTINCT f.id) AS meals
FROM transactions t
LEFT JOIN food_log f ON t.date = f.date
GROUP BY t.date
ORDER BY t.date DESC
LIMIT 30;
```

---

## Conventions for New Services

### 1. Use the Central Database

All new services should use `openclaw.db`:

```python
from pathlib import Path
WORKSPACE = Path("/workspace")
DB_PATH = WORKSPACE / "openclaw.db"
```

### 2. Table Naming

Since all tables share one database, use a **service prefix** for new tables to avoid collisions:

| Service | Prefix | Example Tables |
|---------|--------|----------------|
| Finance | (none — legacy) | transactions, accounts, budgets |
| Nutrition | (none — legacy) | food_log, food_database, daily_goals |
| Calendar | `cal_` | cal_events, cal_reminders |
| Fitness | `fit_` | fit_workouts, fit_exercises |
| Notes | `notes_` | notes_entries, notes_tags |

> Existing finance/nutrition tables keep their current names (no prefix) since they're already unique and widely referenced.

### 3. Connection Pattern

Every service must use WAL mode and busy_timeout for safe concurrent access:

```python
import sqlite3
from contextlib import contextmanager

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")  # wait up to 5s on lock
    return conn

@contextmanager
def _db():
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

### 4. Schema Initialization

Use `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` so services can start in any order:

```python
def _init_db():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cal_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                start_dt TEXT NOT NULL,
                end_dt TEXT,
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cal_events_start ON cal_events(start_dt)")
```

### 5. Docker Volume Mount

Add to `docker-compose.yml`:

```yaml
your-service:
  build: ./services/your-service
  volumes:
    - ./openclaw-data/workspace:/workspace  # RW access to central DB
```

### 6. Traefik Route

Add to `traefik/dynamic.yml`:
- Router with `PathPrefix(`/your-service`)` and strip middleware
- Service pointing to your container's port

---

## Concurrency Notes

SQLite with WAL mode supports:
- **Multiple simultaneous readers** ✅
- **One writer at a time** (others wait up to `busy_timeout` ms)

For a single-user local setup, this is more than sufficient. The `busy_timeout=5000` pragma means a write that would conflict will automatically retry for up to 5 seconds before failing.

If you ever need truly concurrent writes from many services, consider migrating to PostgreSQL. But for the current scale (~5K rows total), SQLite is optimal.

---

## File Locations

```
openclaw-data/workspace/
├── openclaw.db           ← Central database (all services)
├── finance.db.bak        ← Backup of original finance DB
├── nutrition.db.bak      ← Backup of original nutrition DB
├── finance-seed.json     ← Initial finance data (import on empty DB)
└── ...

services/datasette/
├── Dockerfile            ← Datasette container definition
├── metadata.yml          ← DB/table descriptions & canned queries
└── settings.json         ← Datasette configuration

scripts/
└── migrate_to_central_db.py  ← Migration tool (safe to re-run)
```

---

## Backup & Recovery

The original per-service databases are preserved as `.bak` files. To restore:

```bash
# Stop services
docker compose stop finance nutrition

# Restore originals
cp openclaw-data/workspace/finance.db.bak openclaw-data/workspace/finance.db
cp openclaw-data/workspace/nutrition.db.bak openclaw-data/workspace/nutrition.db

# Revert DB_PATH in services (change "openclaw.db" back to "finance.db" / "nutrition.db")
# Then rebuild and restart
```

To create a fresh backup of the central DB:

```bash
sqlite3 openclaw-data/workspace/openclaw.db ".backup openclaw-data/workspace/openclaw-$(date +%Y%m%d).db.bak"
```
