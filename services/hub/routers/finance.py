"""
Finance Tracker Router — personal expense & income tracker with credit card tracking.
Stores data in SQLite. Imports from finance-seed.json on first run.
"""

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any, Optional, List

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger("finance")

DB_PATH = os.getenv("WORKSPACE_DIR", "/workspace") + "/openclaw.db"
WORKSPACE  = Path(os.getenv("WORKSPACE_DIR", "/workspace"))
SEED_FILE  = WORKSPACE / "finance-seed.json"

PH_TZ_OFFSET = 8  # UTC+8

# Accounts to exclude from all views (credit cards + transactions)
EXCLUDED_ACCOUNTS = ["BDO Corporate AMEX"]

# ── Database ─────────────────────────────────────────────────────────────────

DDL_TABLES = """
CREATE TABLE IF NOT EXISTS transactions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    date                    TEXT    NOT NULL,
    time                    TEXT    NOT NULL DEFAULT '00:00:00',
    note                    TEXT    NOT NULL DEFAULT '',
    amount                  REAL    NOT NULL DEFAULT 0.0,
    php                     REAL    NOT NULL DEFAULT 0.0,
    currency                TEXT    NOT NULL DEFAULT 'PHP',
    description             TEXT    NOT NULL DEFAULT '',
    payment_status          TEXT    NOT NULL DEFAULT '',
    personal_amount         REAL    NOT NULL DEFAULT 0.0,
    non_personal_amount     REAL    NOT NULL DEFAULT 0.0,
    installment_num         INTEGER NOT NULL DEFAULT 0,
    installment_total       INTEGER NOT NULL DEFAULT 0,
    account_id              INTEGER REFERENCES accounts(account_id),
    category_id             INTEGER REFERENCES categories(category_id),
    transaction_type_id     INTEGER REFERENCES transaction_types(transaction_type_id),
    expense_type_id_primary   INTEGER REFERENCES expense_types(expense_type_id),
    expense_type_id_secondary INTEGER REFERENCES expense_types(expense_type_id),
    created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,
    group_name      TEXT    NOT NULL DEFAULT 'Other',
    icon            TEXT    NOT NULL DEFAULT '💳',
    sort_order      INTEGER NOT NULL DEFAULT 0,
    cutoff_day      INTEGER NOT NULL DEFAULT 15,
    balance_offset  REAL    NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS expense_types (
    expense_type_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    category_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transaction_types (
    transaction_type_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL UNIQUE,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

DDL_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_date             ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_payment_status   ON transactions(payment_status);
CREATE INDEX IF NOT EXISTS idx_txn_account_id   ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_txn_category_id  ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_txn_type_id      ON transactions(transaction_type_id);
CREATE INDEX IF NOT EXISTS idx_txn_et_primary   ON transactions(expense_type_id_primary);
CREATE INDEX IF NOT EXISTS idx_txn_et_secondary ON transactions(expense_type_id_secondary);
"""

# Columns to add if they don't exist (for migration of existing databases)
MIGRATION_COLUMNS = [
    ("transactions", "payment_status",          "TEXT NOT NULL DEFAULT ''"),
    ("transactions", "personal_amount",         "REAL NOT NULL DEFAULT 0.0"),
    ("transactions", "non_personal_amount",     "REAL NOT NULL DEFAULT 0.0"),
    ("transactions", "installment_num",         "INTEGER NOT NULL DEFAULT 0"),
    ("transactions", "installment_total",       "INTEGER NOT NULL DEFAULT 0"),
    ("transactions", "account_id",              "INTEGER REFERENCES accounts(account_id)"),
    ("transactions", "category_id",             "INTEGER REFERENCES categories(category_id)"),
    ("transactions", "transaction_type_id",     "INTEGER REFERENCES transaction_types(transaction_type_id)"),
    ("transactions", "expense_type_id_primary",   "INTEGER REFERENCES expense_types(expense_type_id)"),
    ("transactions", "expense_type_id_secondary", "INTEGER REFERENCES expense_types(expense_type_id)"),
    ("accounts",     "cutoff_day",              "INTEGER NOT NULL DEFAULT 15"),
    ("accounts",     "balance_offset",          "REAL NOT NULL DEFAULT 0"),
]

# ── Transaction Type ID constants (must match seeded data) ────────────────────
# Loaded at startup from DB; these are fallback defaults
_TT_IDS: dict[str, int] = {}

def _tt_id(name: str) -> int:
    """Get transaction_type_id for a given type name."""
    return _TT_IDS.get(name, 0)

# Convenience constants populated at startup
EXPENSE_TT_IDS: tuple = ()      # (Exp., Expense Balance)
INCOME_TT_IDS: tuple = ()       # (Income, Income Balance)
CREDIT_TT_IDS: tuple = ()       # (Income, Transfer-In, Income Balance)
DEBIT_TT_IDS: tuple = ()        # (Exp., Transfer-Out, Expense Balance)

def _init_tt_ids(conn):
    """Load transaction type IDs from DB into module-level constants."""
    global _TT_IDS, EXPENSE_TT_IDS, INCOME_TT_IDS, CREDIT_TT_IDS, DEBIT_TT_IDS
    rows = conn.execute("SELECT transaction_type_id, name FROM transaction_types").fetchall()
    _TT_IDS = {r["name"]: r["transaction_type_id"] for r in rows}
    EXPENSE_TT_IDS = tuple(v for k, v in _TT_IDS.items() if k in ("Exp.", "Expense Balance"))
    INCOME_TT_IDS = tuple(v for k, v in _TT_IDS.items() if k in ("Income", "Income Balance"))
    CREDIT_TT_IDS = tuple(v for k, v in _TT_IDS.items() if k in ("Income", "Transfer-In", "Income Balance"))
    DEBIT_TT_IDS = tuple(v for k, v in _TT_IDS.items() if k in ("Exp.", "Transfer-Out", "Expense Balance"))

# Personal expense type ID (loaded at startup)
PERSONAL_ET_ID: int = 4


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
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


def _seed_accounts():
    """No-op: accounts are managed via the accounts table directly."""
    pass


def _seed_transaction_types():
    """Ensure default transaction types exist in the DB."""
    defaults = ["Exp.", "Income", "Transfer-In", "Transfer-Out", "Income Balance", "Expense Balance"]
    with _db() as conn:
        for name in defaults:
            conn.execute("INSERT OR IGNORE INTO transaction_types (name) VALUES (?)", (name,))


def _seed_expense_types():
    """Ensure default expense types exist in the DB."""
    defaults = ["Business", "Family", "Friends", "Personal", "Reimbursement", "Work"]
    with _db() as conn:
        for name in defaults:
            conn.execute("INSERT OR IGNORE INTO expense_types (name) VALUES (?)", (name,))


def _load_constants():
    """Load FK IDs into module-level constants after DB is initialized."""
    global PERSONAL_ET_ID
    with _db() as conn:
        _init_tt_ids(conn)
        row = conn.execute("SELECT expense_type_id FROM expense_types WHERE name = 'Personal'").fetchone()
        if row:
            PERSONAL_ET_ID = row[0]


def _init_db():
    with _db() as conn:
        conn.executescript(DDL_TABLES)
    _migrate_columns()
    with _db() as conn:
        conn.executescript(DDL_INDEXES)
    _seed_transaction_types()
    _seed_expense_types()
    _maybe_import_seed()
    _load_constants()


def _migrate_columns():
    """Add new columns to existing tables if they don't exist."""
    with _db() as conn:
        for table, col_name, col_def in MIGRATION_COLUMNS:
            existing = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if col_name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
                logger.info("Migrated: added %s.%s", table, col_name)

    # Backfill personal_amount / non_personal_amount for existing rows
    with _db() as conn:
        needs_backfill = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE personal_amount = 0.0 AND non_personal_amount = 0.0 AND php > 0"
        ).fetchone()[0]
        if needs_backfill > 0:
            # Personal or no expense type → all personal
            conn.execute(
                """UPDATE transactions SET personal_amount = php, non_personal_amount = 0.0
                   WHERE (personal_amount = 0.0 AND non_personal_amount = 0.0 AND php > 0)
                     AND (expense_type_id_secondary IS NULL)"""
            )
            # Has secondary expense type → non-personal portion
            conn.execute(
                """UPDATE transactions SET personal_amount = 0.0, non_personal_amount = php
                   WHERE (personal_amount = 0.0 AND non_personal_amount = 0.0 AND php > 0)
                     AND expense_type_id_secondary IS NOT NULL AND expense_type_id_primary IS NOT NULL"""
            )
            logger.info("Backfilled personal/non_personal amounts for %d rows", needs_backfill)

    # Backfill installment_num / installment_total from note field for existing rows
    with _db() as conn:
        import re
        # Handle [N/M] format at start of note
        rows = conn.execute(
            "SELECT id, note FROM transactions WHERE installment_num = 0 AND note LIKE '[%/%]%'"
        ).fetchall()
        for row in rows:
            m = re.match(r'^\[(\d+)/(\d+)\]', row[1])
            if m:
                clean_note = re.sub(r'^\[\d+/\d+\]\s*', '', row[1]).strip()
                conn.execute(
                    "UPDATE transactions SET installment_num = ?, installment_total = ?, note = ? WHERE id = ?",
                    (int(m.group(1)), int(m.group(2)), clean_note, row[0])
                )

        # Handle (N/M) format anywhere in note
        paren_rows = conn.execute(
            "SELECT id, note FROM transactions WHERE installment_num = 0"
        ).fetchall()
        for row in paren_rows:
            m = re.search(r'\((\d+)/(\d+)\)', row[1] or '')
            if m:
                clean_note = re.sub(r'\s*\(\d+/\d+\)\s*', '', row[1]).strip()
                conn.execute(
                    "UPDATE transactions SET installment_num = ?, installment_total = ?, note = ? WHERE id = ?",
                    (int(m.group(1)), int(m.group(2)), clean_note, row[0])
                )


def _maybe_import_seed():
    with _db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    if count > 0:
        logger.info("Database already has %d transactions — skipping seed import", count)
        return
    if not SEED_FILE.exists():
        logger.warning("No seed file found at %s — starting with empty database", SEED_FILE)
        return
    logger.info("Importing seed data from %s …", SEED_FILE)
    data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    rows = data.get("transactions", [])
    with _db() as conn:
        # Pre-create accounts and categories
        for r in rows:
            acct_name = r.get("account", "")
            if acct_name:
                conn.execute("INSERT OR IGNORE INTO accounts (name, group_name, icon) VALUES (?, 'Other', '💳')", (acct_name,))
            cat_name = r.get("category", "")
            if cat_name:
                conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (cat_name,))

        for r in rows:
            acct_id = None
            acct_name = r.get("account", "")
            if acct_name:
                row_a = conn.execute("SELECT account_id FROM accounts WHERE name = ?", (acct_name,)).fetchone()
                if row_a:
                    acct_id = row_a[0]
            cat_id = None
            cat_name = r.get("category", "")
            if cat_name:
                row_c = conn.execute("SELECT category_id FROM categories WHERE name = ?", (cat_name,)).fetchone()
                if row_c:
                    cat_id = row_c[0]
            tt_id = None
            type_name = r.get("type", "")
            if type_name:
                row_t = conn.execute("SELECT transaction_type_id FROM transaction_types WHERE name = ?", (type_name,)).fetchone()
                if row_t:
                    tt_id = row_t[0]
            conn.execute(
                """INSERT INTO transactions
                   (date, time, note, amount, php, currency, description,
                    account_id, category_id, transaction_type_id, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
                (r["date"], r["time"], r.get("note", ""),
                 r["amount"], r["php"], r.get("currency", "PHP"), r.get("description", ""),
                 acct_id, cat_id, tt_id),
            )
    logger.info("Imported %d transactions", len(rows))


def _migrate_expense_types():
    """Legacy — no longer needed. Combined expense types already migrated in Phase 5."""
    pass


async def init_db():
    """Initialize the finance database tables, run migrations, and seed data.
    Called by the main app at startup."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    _init_db()


# ── Models ────────────────────────────────────────────────────────────────────


class TransactionCreate(BaseModel):
    date: str
    time: str = "00:00:00"
    account: str = ""
    category: str = ""
    note: str = ""
    type: str = ""
    amount: float = 0.0
    php: float = 0.0
    currency: str = "PHP"
    description: str = ""
    expense_type_primary: str = ""
    expense_type_secondary: str = ""
    payment_status: str = ""
    personal_amount: float = 0.0
    non_personal_amount: float = 0.0
    installment_num: int = 0
    installment_total: int = 0


class AccountCreate(BaseModel):
    name: str
    group_name: str = "Other"
    icon: str = "💳"
    sort_order: int = 0
    cutoff_day: int = 15


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    group_name: Optional[str] = None
    icon: Optional[str] = None
    sort_order: Optional[int] = None
    cutoff_day: Optional[int] = None


class InstallmentCreate(BaseModel):
    date: str
    account: str = ""
    category: str = ""
    note: str = ""
    type: str = "Exp."
    total_amount: float = 0.0
    installments: int = 1
    currency: str = "PHP"
    description: str = ""
    expense_type_primary: str = ""
    expense_type_secondary: str = ""
    payment_status: str = ""


class TransactionUpdate(BaseModel):
    date: Optional[str] = None
    time: Optional[str] = None
    account: Optional[str] = None
    category: Optional[str] = None
    note: Optional[str] = None
    type: Optional[str] = None
    amount: Optional[float] = None
    php: Optional[float] = None
    currency: Optional[str] = None
    description: Optional[str] = None
    expense_type_primary: Optional[str] = None
    expense_type_secondary: Optional[str] = None
    payment_status: Optional[str] = None
    personal_amount: Optional[float] = None
    non_personal_amount: Optional[float] = None
    installment_num: Optional[int] = None
    installment_total: Optional[int] = None


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    # Ensure frontend-compatible field names
    # "type" comes from tt.name alias; "category" from c.name alias
    # "expense_type_primary" from ep.name; "expense_type_secondary" from es.name
    return d


def _resolve_account_id(conn, name: str) -> Optional[int]:
    """Resolve account name to account_id, creating if needed."""
    if not name:
        return None
    row = conn.execute("SELECT account_id FROM accounts WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    conn.execute("INSERT INTO accounts (name) VALUES (?)", (name,))
    return conn.execute("SELECT account_id FROM accounts WHERE name = ?", (name,)).fetchone()[0]


def _resolve_category_id(conn, name: str) -> Optional[int]:
    """Resolve category name to category_id, creating if needed."""
    if not name:
        return None
    row = conn.execute("SELECT category_id FROM categories WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
    return conn.execute("SELECT category_id FROM categories WHERE name = ?", (name,)).fetchone()[0]


def _resolve_tt_id(conn, name: str) -> Optional[int]:
    """Resolve transaction type name to transaction_type_id."""
    if not name:
        return None
    row = conn.execute("SELECT transaction_type_id FROM transaction_types WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def _resolve_et_id(conn, name: str) -> Optional[int]:
    """Resolve expense type name to expense_type_id."""
    if not name:
        return None
    row = conn.execute("SELECT expense_type_id FROM expense_types WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


# 6-table JOIN: transactions + accounts + categories + transaction_types + expense_types (primary) + expense_types (secondary)
_TXN_JOIN = """FROM transactions t
  LEFT JOIN accounts a ON t.account_id = a.account_id
  LEFT JOIN categories c ON t.category_id = c.category_id
  LEFT JOIN transaction_types tt ON t.transaction_type_id = tt.transaction_type_id
  LEFT JOIN expense_types ep ON t.expense_type_id_primary = ep.expense_type_id
  LEFT JOIN expense_types es ON t.expense_type_id_secondary = es.expense_type_id"""

_TXN_SELECT = f"""SELECT t.*,
  a.name AS account, c.name AS category, tt.name AS type,
  ep.name AS expense_type_primary, es.name AS expense_type_secondary
  {_TXN_JOIN}"""

_TXN_FROM = _TXN_JOIN


# ── Routes ────────────────────────────────────────────────────────────────────


# ── Transactions CRUD ─────────────────────────────────────────────────────────

@router.get("/api/transactions")
async def list_transactions(
    page:     int   = Query(1,    ge=1),
    per_page: int   = Query(50,   ge=1, le=500),
    account:  str   = Query("",   description="Filter by account name"),
    category: str   = Query("",   description="Filter by category"),
    ttype:    str   = Query("",   alias="type", description="Filter by type"),
    search:   str   = Query("",   description="Search in note/description"),
    date_from:str   = Query("",   description="Start date YYYY-MM-DD"),
    date_to:  str   = Query("",   description="End date YYYY-MM-DD"),
    sort:     str   = Query("date_desc", description="Sort: date_desc|date_asc|amount_desc|amount_asc"),
    expense_type: str = Query("", description="Filter by expense type"),
    payment_status: str = Query("", description="Filter by payment status"),
):
    where_clauses = []
    params: list[Any] = []

    # Exclude hidden accounts
    if EXCLUDED_ACCOUNTS:
        ea_ph = ",".join("?" * len(EXCLUDED_ACCOUNTS))
        where_clauses.append(f"a.name NOT IN ({ea_ph})")
        params.extend(EXCLUDED_ACCOUNTS)

    if account:
        where_clauses.append("a.name = ?")
        params.append(account)
    if category:
        where_clauses.append("c.name = ?")
        params.append(category)
    if ttype:
        where_clauses.append("tt.name = ?")
        params.append(ttype)
    if search:
        where_clauses.append("(t.note LIKE ? OR t.description LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    if date_from:
        where_clauses.append("t.date >= ?")
        params.append(date_from)
    if date_to:
        where_clauses.append("t.date <= ?")
        params.append(date_to)
    if expense_type:
        where_clauses.append("(ep.name = ? OR es.name = ?)")
        params += [expense_type, expense_type]
    if payment_status:
        where_clauses.append("t.payment_status = ?")
        params.append(payment_status)

    # Exclude fully non-personal card transactions from the main list
    with _db() as conn:
        card_ids = _get_card_account_ids(conn)
    if card_ids:
        ca_ph = ",".join("?" * len(card_ids))
        where_clauses.append(
            f"NOT (t.account_id IN ({ca_ph}) AND t.personal_amount = 0.0 AND t.non_personal_amount > 0.0)"
        )
        params.extend(card_ids)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    sort_map = {
        "date_desc":   "t.date DESC, t.time DESC",
        "date_asc":    "t.date ASC,  t.time ASC",
        "amount_desc": "t.amount DESC",
        "amount_asc":  "t.amount ASC",
    }
    order_sql = sort_map.get(sort, "t.date DESC, t.time DESC")

    offset = (page - 1) * per_page

    with _db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) {_TXN_FROM} {where_sql}", params
        ).fetchone()[0]

        rows = conn.execute(
            f"{_TXN_SELECT} {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

    return {
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "pages":    (total + per_page - 1) // per_page,
        "items":    [_row_to_dict(r) for r in rows],
    }


@router.get("/api/transactions/{txn_id}")
async def get_transaction(txn_id: int):
    with _db() as conn:
        row = conn.execute(f"{_TXN_SELECT} WHERE t.id = ?", (txn_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return _row_to_dict(row)


@router.post("/api/transactions", status_code=201)
async def create_transaction(body: TransactionCreate):
    if not body.type.strip():
        raise HTTPException(status_code=422, detail="Type is required")
    if body.amount < 0:
        raise HTTPException(status_code=422, detail="Amount cannot be negative")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    php_amount = body.php if body.php else body.amount
    p_amt = body.personal_amount
    np_amt = body.non_personal_amount

    # Auto-compute split if not explicitly provided
    if p_amt == 0.0 and np_amt == 0.0 and php_amount > 0:
        if not body.expense_type_secondary:
            p_amt = php_amount
        else:
            # Has secondary (shared) expense type → default 50/50
            p_amt = round(php_amount * 0.5, 2)
            np_amt = round(php_amount * 0.5, 2)

    # Validate expense type guardrails
    if body.expense_type_primary and body.expense_type_primary != "Personal":
        raise HTTPException(status_code=422, detail="Primary expense type must be 'Personal'")

    with _db() as conn:
        acct_id = _resolve_account_id(conn, body.account)
        cat_id = _resolve_category_id(conn, body.category)
        tt_id = _resolve_tt_id(conn, body.type)
        et_primary = _resolve_et_id(conn, body.expense_type_primary) if body.expense_type_primary else None
        et_secondary = _resolve_et_id(conn, body.expense_type_secondary) if body.expense_type_secondary else None

        # If type is an expense type and no primary set, default to Personal
        if tt_id and tt_id in EXPENSE_TT_IDS and not et_primary:
            et_primary = PERSONAL_ET_ID

        cur = conn.execute(
            """INSERT INTO transactions
               (date, time, note, amount, php, currency, description,
                payment_status, personal_amount, non_personal_amount,
                installment_num, installment_total,
                account_id, category_id, transaction_type_id,
                expense_type_id_primary, expense_type_id_secondary,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (body.date, body.time, body.note, body.amount, php_amount,
             body.currency, body.description, body.payment_status,
             p_amt, np_amt, body.installment_num, body.installment_total,
             acct_id, cat_id, tt_id, et_primary, et_secondary, now, now),
        )
        new_id = cur.lastrowid
        row = conn.execute(f"{_TXN_SELECT} WHERE t.id = ?", (new_id,)).fetchone()
    return _row_to_dict(row)


@router.put("/api/transactions/{txn_id}")
async def update_transaction(txn_id: int, body: TransactionUpdate):
    with _db() as conn:
        existing = conn.execute(f"{_TXN_SELECT} WHERE t.id = ?", (txn_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Transaction not found")

        updates = body.dict(exclude_none=True)
        if not updates:
            return _row_to_dict(existing)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Build actual DB column updates
        db_updates = {"updated_at": now}

        # Resolve FK columns when text values change
        if "account" in updates:
            db_updates["account_id"] = _resolve_account_id(conn, updates["account"])
        if "category" in updates:
            db_updates["category_id"] = _resolve_category_id(conn, updates["category"])
        if "type" in updates:
            db_updates["transaction_type_id"] = _resolve_tt_id(conn, updates["type"])
        if "expense_type_primary" in updates:
            if updates["expense_type_primary"] and updates["expense_type_primary"] != "Personal":
                raise HTTPException(status_code=422, detail="Primary expense type must be 'Personal'")
            db_updates["expense_type_id_primary"] = _resolve_et_id(conn, updates["expense_type_primary"])
        if "expense_type_secondary" in updates:
            db_updates["expense_type_id_secondary"] = _resolve_et_id(conn, updates["expense_type_secondary"])

        # Pass through direct DB columns
        direct_cols = ["date", "time", "note", "amount", "php", "currency",
                       "description", "payment_status", "personal_amount",
                       "non_personal_amount", "installment_num", "installment_total"]
        for col in direct_cols:
            if col in updates:
                db_updates[col] = updates[col]

        set_clause = ", ".join(f"{k} = ?" for k in db_updates)
        params = list(db_updates.values()) + [txn_id]
        conn.execute(f"UPDATE transactions SET {set_clause} WHERE id = ?", params)
        row = conn.execute(f"{_TXN_SELECT} WHERE t.id = ?", (txn_id,)).fetchone()
    return _row_to_dict(row)


@router.delete("/api/transactions/{txn_id}")
async def delete_transaction(txn_id: int):
    with _db() as conn:
        existing = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Transaction not found")
        conn.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))
    return {"deleted": True, "id": txn_id}


# ── Summary / Analytics ───────────────────────────────────────────────────────

def _exclusion_clause(prefix="AND"):
    """Build SQL clause to exclude hidden accounts (requires JOIN alias 'a' for accounts)."""
    if not EXCLUDED_ACCOUNTS:
        return "", []
    ea_ph = ",".join("?" * len(EXCLUDED_ACCOUNTS))
    return f"{prefix} a.name NOT IN ({ea_ph})", list(EXCLUDED_ACCOUNTS)


@router.get("/api/summary")
async def summary(month: str = Query("", description="YYYY-MM for monthly summary")):
    """Overall and monthly summary of income, expenses, and net."""
    excl_sql, excl_params = _exclusion_clause("AND")
    with _db() as conn:
        month_pattern = f"{month}%" if month else f"{date.today().strftime('%Y-%m')}%"
        month_filter = "AND t.date LIKE ?"

        # Income
        income_row = conn.execute(
            f"""SELECT COALESCE(SUM(t.php), 0) {_TXN_FROM}
                WHERE t.transaction_type_id IN ({','.join('?' * len(INCOME_TT_IDS))}) {month_filter} {excl_sql}""",
            list(INCOME_TT_IDS) + [month_pattern] + excl_params,
        ).fetchone()
        income = round(income_row[0], 2)

        # Expenses (exclude non-personal card expenses)
        card_ids = _get_card_account_ids(conn)
        exp_ids = list(EXPENSE_TT_IDS)
        exp_ph = ','.join('?' * len(exp_ids))
        if card_ids:
            ca_ph = ",".join("?" * len(card_ids))
            expense_row = conn.execute(
                f"""SELECT COALESCE(SUM(
                        CASE WHEN t.account_id IN ({ca_ph}) THEN
                            CASE WHEN t.personal_amount = 0.0 AND t.non_personal_amount = 0.0 THEN t.php ELSE t.personal_amount END
                        ELSE t.php END
                    ), 0) {_TXN_FROM}
                    WHERE t.transaction_type_id IN ({exp_ph}) {month_filter}
                      AND NOT (t.account_id IN ({ca_ph}) AND t.personal_amount = 0.0 AND t.non_personal_amount > 0.0)
                      {excl_sql}""",
                card_ids + exp_ids + [month_pattern] + card_ids + excl_params,
            ).fetchone()
        else:
            expense_row = conn.execute(
                f"""SELECT COALESCE(SUM(t.php), 0) {_TXN_FROM}
                    WHERE t.transaction_type_id IN ({exp_ph}) {month_filter} {excl_sql}""",
                exp_ids + [month_pattern] + excl_params,
            ).fetchone()
        expenses = round(expense_row[0], 2)

        net = round(income - expenses, 2)

        total_txns = conn.execute(
            f"SELECT COUNT(*) {_TXN_FROM} WHERE 1=1 {month_filter} {excl_sql}",
            [month_pattern] + excl_params,
        ).fetchone()[0]

        # All-time totals
        inc_ph = ','.join('?' * len(INCOME_TT_IDS))
        all_income = conn.execute(
            f"SELECT COALESCE(SUM(t.php),0) {_TXN_FROM} WHERE t.transaction_type_id IN ({inc_ph}) {excl_sql}",
            list(INCOME_TT_IDS) + excl_params,
        ).fetchone()[0]
        if card_ids:
            ca_ph2 = ",".join("?" * len(card_ids))
            all_expense = conn.execute(
                f"""SELECT COALESCE(SUM(
                        CASE WHEN t.account_id IN ({ca_ph2}) THEN
                            CASE WHEN t.personal_amount = 0.0 AND t.non_personal_amount = 0.0 THEN t.php ELSE t.personal_amount END
                        ELSE t.php END
                    ), 0) {_TXN_FROM}
                    WHERE t.transaction_type_id IN ({exp_ph})
                      AND NOT (t.account_id IN ({ca_ph2}) AND t.personal_amount = 0.0 AND t.non_personal_amount > 0.0)
                      {excl_sql}""",
                card_ids + exp_ids + card_ids + excl_params,
            ).fetchone()[0]
        else:
            all_expense = conn.execute(
                f"SELECT COALESCE(SUM(t.php),0) {_TXN_FROM} WHERE t.transaction_type_id IN ({exp_ph}) {excl_sql}",
                exp_ids + excl_params,
            ).fetchone()[0]

    return {
        "period":          month or date.today().strftime("%Y-%m"),
        "income":          income,
        "expenses":        expenses,
        "net":             net,
        "transaction_count": total_txns,
        "all_time_income":  round(all_income, 2),
        "all_time_expenses": round(all_expense, 2),
        "all_time_net":     round(all_income - all_expense, 2),
    }


@router.get("/api/monthly-trend")
async def monthly_trend(months: int = Query(12, ge=1, le=60)):
    """Return monthly income/expense totals for the last N months (excludes non-personal card expenses)."""
    excl_sql, excl_params = _exclusion_clause("AND")
    inc_ph = ','.join('?' * len(INCOME_TT_IDS))
    exp_ph = ','.join('?' * len(EXPENSE_TT_IDS))
    with _db() as conn:
        card_ids = _get_card_account_ids(conn)
        if card_ids:
            ca_ph = ",".join("?" * len(card_ids))
            rows = conn.execute(
                f"""SELECT
                       substr(t.date,1,7) AS month,
                       COALESCE(SUM(CASE WHEN t.transaction_type_id IN ({inc_ph}) THEN t.php ELSE 0 END),0) AS income,
                       COALESCE(SUM(CASE WHEN t.transaction_type_id IN ({exp_ph})
                           THEN CASE WHEN t.account_id IN ({ca_ph}) THEN
                               CASE WHEN t.personal_amount = 0.0 AND t.non_personal_amount = 0.0 THEN t.php ELSE t.personal_amount END
                           ELSE t.php END
                           ELSE 0 END),0) AS expenses
                   {_TXN_FROM}
                   WHERE NOT (t.account_id IN ({ca_ph}) AND t.personal_amount = 0.0 AND t.non_personal_amount > 0.0)
                     {excl_sql}
                   GROUP BY month
                   ORDER BY month DESC
                   LIMIT ?""",
                list(INCOME_TT_IDS) + list(EXPENSE_TT_IDS) + card_ids + card_ids + excl_params + [months],
            ).fetchall()
        else:
            rows = conn.execute(
                f"""SELECT
                       substr(t.date,1,7) AS month,
                       COALESCE(SUM(CASE WHEN t.transaction_type_id IN ({inc_ph}) THEN t.php ELSE 0 END),0) AS income,
                       COALESCE(SUM(CASE WHEN t.transaction_type_id IN ({exp_ph}) THEN t.php ELSE 0 END),0) AS expenses
                   {_TXN_FROM}
                   WHERE 1=1 {excl_sql}
                   GROUP BY month
                   ORDER BY month DESC
                   LIMIT ?""",
                list(INCOME_TT_IDS) + list(EXPENSE_TT_IDS) + excl_params + [months],
            ).fetchall()
    result = [{"month": r["month"], "income": round(r["income"], 2), "expenses": round(r["expenses"], 2)} for r in rows]
    result.reverse()
    return {"data": result}


@router.get("/api/category-breakdown")
async def category_breakdown(
    month:    str = Query("", description="YYYY-MM"),
    ttype:    str = Query("expense", description="'expense' or 'income'"),
):
    """Return spending/income by category for a given month."""
    if ttype == "income":
        tt_ids = list(INCOME_TT_IDS)
    else:
        tt_ids = list(EXPENSE_TT_IDS)
    tt_ph = ','.join('?' * len(tt_ids))

    month_pattern = f"{month}%" if month else f"{date.today().strftime('%Y-%m')}%"
    date_filter = "AND t.date LIKE ?"

    with _db() as conn:
        card_ids = _get_card_account_ids(conn)
        excl_sql, excl_params = _exclusion_clause("AND")
        if card_ids and ttype != "income":
            ca_ph = ",".join("?" * len(card_ids))
            rows = conn.execute(
                f"""SELECT c.name AS category, COALESCE(SUM(
                        CASE WHEN t.account_id IN ({ca_ph}) THEN
                            CASE WHEN t.personal_amount = 0.0 AND t.non_personal_amount = 0.0 THEN t.php ELSE t.personal_amount END
                        ELSE t.php END
                    ),0) AS total
                    {_TXN_FROM}
                    WHERE t.transaction_type_id IN ({tt_ph}) {date_filter}
                      AND NOT (t.account_id IN ({ca_ph}) AND t.personal_amount = 0.0 AND t.non_personal_amount > 0.0)
                      {excl_sql}
                    GROUP BY c.name
                    ORDER BY total DESC""",
                card_ids + tt_ids + [month_pattern] + card_ids + excl_params,
            ).fetchall()
        else:
            rows = conn.execute(
                f"""SELECT c.name AS category, COALESCE(SUM(t.php),0) AS total
                    {_TXN_FROM}
                    WHERE t.transaction_type_id IN ({tt_ph}) {date_filter} {excl_sql}
                    GROUP BY c.name
                    ORDER BY total DESC""",
                tt_ids + [month_pattern] + excl_params,
            ).fetchall()

    return {"data": [{"category": r["category"] or "Uncategorized", "total": round(r["total"], 2)} for r in rows]}


@router.get("/api/accounts")
async def accounts_summary():
    """Current balance per account based on all transactions, with group info."""
    with _db() as conn:
        credit_ph = ','.join('?' * len(CREDIT_TT_IDS))
        debit_ph = ','.join('?' * len(DEBIT_TT_IDS))
        rows = conn.execute(
            f"""SELECT
                   a.name AS account,
                   COALESCE(a.group_name, 'Other') AS group_name,
                   COALESCE(a.icon, '💳') AS icon,
                   a.account_id,
                   COALESCE(a.sort_order, 999) AS sort_order,
                   a.cutoff_day,
                   COALESCE(a.balance_offset, 0) AS balance_offset,
                   COALESCE(SUM(CASE WHEN t.transaction_type_id IN ({credit_ph}) THEN t.php ELSE 0 END),0) AS credits,
                   COALESCE(SUM(CASE WHEN t.transaction_type_id IN ({debit_ph}) THEN t.php ELSE 0 END),0) AS debits,
                   COUNT(t.id) AS txn_count
               FROM accounts a
               LEFT JOIN transactions t ON t.account_id = a.account_id
               GROUP BY a.account_id
               ORDER BY COALESCE(a.sort_order, 999), COALESCE(a.group_name, 'Other'), a.name""",
            list(CREDIT_TT_IDS) + list(DEBIT_TT_IDS),
        ).fetchall()

    result = []
    for r in rows:
        txn_balance = round(r["credits"] - r["debits"], 2)
        offset = round(r["balance_offset"], 2)
        balance = round(txn_balance + offset, 2)
        result.append({
            "account":    r["account"],
            "group_name": r["group_name"],
            "icon":       r["icon"],
            "account_id": r["account_id"],
            "cutoff_day": r["cutoff_day"] or 15,
            "balance_offset": offset,
            "credits":    round(r["credits"], 2),
            "debits":     round(r["debits"], 2),
            "balance":    balance,
            "txn_count":  r["txn_count"],
        })

    total_balance = round(sum(a["balance"] for a in result), 2)
    return {"accounts": result, "total_balance": total_balance}


@router.get("/api/meta")
async def meta():
    """Return lists of unique accounts, categories, types, and expense types for dropdown menus."""
    with _db() as conn:
        account_rows = conn.execute(
            "SELECT account_id, name, group_name, icon, cutoff_day FROM accounts ORDER BY sort_order, group_name, name"
        ).fetchall()
        categories = [r[0] for r in conn.execute(
            "SELECT name FROM categories ORDER BY name"
        ).fetchall()]
        types = [r[0] for r in conn.execute(
            "SELECT name FROM transaction_types ORDER BY transaction_type_id"
        ).fetchall()]
        expense_types_rows = conn.execute(
            "SELECT name FROM expense_types ORDER BY expense_type_id"
        ).fetchall()

    accounts_list = [r["name"] for r in account_rows if r["name"] not in EXCLUDED_ACCOUNTS]
    accounts_grouped: dict[str, list] = {}
    for r in account_rows:
        if r["name"] in EXCLUDED_ACCOUNTS:
            continue
        grp = r["group_name"]
        if grp not in accounts_grouped:
            accounts_grouped[grp] = []
        accounts_grouped[grp].append({"name": r["name"], "icon": r["icon"], "cutoff_day": r["cutoff_day"]})

    card_accounts = [r["name"] for r in account_rows
                     if r["group_name"] == "Card" and r["name"] not in EXCLUDED_ACCOUNTS]

    return {
        "accounts": accounts_list,
        "accounts_grouped": accounts_grouped,
        "categories": categories,
        "types": types,
        "card_accounts": card_accounts,
        "expense_types": [r[0] for r in expense_types_rows],
        "payment_statuses": PAYMENT_STATUSES,
    }


@router.get("/api/account-records")
async def list_account_records():
    """List all accounts with computed balances."""
    with _db() as conn:
        credit_ph = ','.join('?' * len(CREDIT_TT_IDS))
        debit_ph = ','.join('?' * len(DEBIT_TT_IDS))
        rows = conn.execute(
            f"""SELECT a.account_id, a.name, a.group_name, a.icon, a.sort_order, a.cutoff_day,
                   COALESCE(a.balance_offset, 0) AS balance_offset,
                   COALESCE(SUM(CASE WHEN t.transaction_type_id IN ({credit_ph}) THEN t.php ELSE 0 END),0) AS credits,
                   COALESCE(SUM(CASE WHEN t.transaction_type_id IN ({debit_ph}) THEN t.php ELSE 0 END),0) AS debits,
                   COUNT(t.id) AS txn_count
               FROM accounts a
               LEFT JOIN transactions t ON t.account_id = a.account_id
               GROUP BY a.account_id
               ORDER BY a.sort_order, a.group_name, a.name""",
            list(CREDIT_TT_IDS) + list(DEBIT_TT_IDS),
        ).fetchall()
    result = []
    for r in rows:
        txn_balance = round(r["credits"] - r["debits"], 2)
        offset = round(r["balance_offset"], 2)
        balance = round(txn_balance + offset, 2)
        result.append({
            "account_id": r["account_id"], "name": r["name"], "group_name": r["group_name"],
            "icon": r["icon"], "sort_order": r["sort_order"], "cutoff_day": r["cutoff_day"],
            "balance_offset": offset,
            "credits": round(r["credits"], 2), "debits": round(r["debits"], 2),
            "balance": balance, "txn_count": r["txn_count"],
        })
    return {"accounts": result}


@router.post("/api/account-records", status_code=201)
async def create_account_record(body: AccountCreate):
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="Account name is required")
    try:
        with _db() as conn:
            cur = conn.execute(
                "INSERT INTO accounts (name, group_name, icon, sort_order, cutoff_day) VALUES (?,?,?,?,?)",
                (body.name.strip(), body.group_name, body.icon, body.sort_order, body.cutoff_day),
            )
            row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (cur.lastrowid,)).fetchone()
        return _row_to_dict(row)
    except Exception:
        raise HTTPException(status_code=409, detail="Account name already exists")


@router.put("/api/account-records/{account_id}")
async def update_account_record(account_id: int, body: AccountUpdate):
    with _db() as conn:
        existing = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Account not found")
        updates = {k: v for k, v in body.dict().items() if v is not None}
        if not updates:
            return _row_to_dict(existing)
        # With account_id FK, renaming an account doesn't require updating transactions
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE accounts SET {set_clause} WHERE account_id=?", list(updates.values()) + [account_id])
        row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
    return _row_to_dict(row)


class BalanceOverride(BaseModel):
    desired_balance: float


@router.put("/api/account-records/{account_id}/balance")
async def set_account_balance(account_id: int, body: BalanceOverride):
    """Set an account balance without recording a transaction.

    Computes the required balance_offset so that
    (credits - debits + balance_offset) == desired_balance.
    """
    with _db() as conn:
        existing = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Account not found")
        credit_ph = ','.join('?' * len(CREDIT_TT_IDS))
        debit_ph = ','.join('?' * len(DEBIT_TT_IDS))
        row = conn.execute(
            f"""SELECT
                   COALESCE(SUM(CASE WHEN t.transaction_type_id IN ({credit_ph}) THEN t.php ELSE 0 END),0) AS credits,
                   COALESCE(SUM(CASE WHEN t.transaction_type_id IN ({debit_ph}) THEN t.php ELSE 0 END),0) AS debits
               FROM transactions t WHERE t.account_id=?""",
            list(CREDIT_TT_IDS) + list(DEBIT_TT_IDS) + [account_id],
        ).fetchone()
        txn_balance = round(row["credits"] - row["debits"], 2)
        new_offset = round(body.desired_balance - txn_balance, 2)
        conn.execute("UPDATE accounts SET balance_offset=? WHERE account_id=?", (new_offset, account_id))
    return {"account_id": account_id, "balance": body.desired_balance, "balance_offset": new_offset}


@router.delete("/api/account-records/{account_id}")
async def delete_account_record(account_id: int):
    with _db() as conn:
        existing = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Account not found")
        txn_count = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE account_id=?", (account_id,)
        ).fetchone()[0]
        if txn_count > 0:
            raise HTTPException(status_code=409, detail=f"Cannot delete: account has {txn_count} transactions. Remove them first.")
        conn.execute("DELETE FROM accounts WHERE account_id=?", (account_id,))
    return {"deleted": True, "account_id": account_id}


@router.post("/api/installments", status_code=201)
async def create_installments(body: InstallmentCreate):
    if not 2 <= body.installments <= 120:
        raise HTTPException(status_code=422, detail="Installments must be between 2 and 120")
    if body.total_amount <= 0:
        raise HTTPException(status_code=422, detail="Amount must be positive")

    from datetime import date as date_cls
    monthly = round(body.total_amount / body.installments, 2)
    last_amount = round(body.total_amount - monthly * (body.installments - 1), 2)
    start = date_cls.fromisoformat(body.date)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    txns = []
    with _db() as conn:
        acct_id = _resolve_account_id(conn, body.account)
        cat_id = _resolve_category_id(conn, body.category)
        tt_id = _resolve_tt_id(conn, body.type)
        et_primary = _resolve_et_id(conn, body.expense_type_primary) if body.expense_type_primary else None
        et_secondary = _resolve_et_id(conn, body.expense_type_secondary) if body.expense_type_secondary else None

        # Default primary to Personal for expenses
        if tt_id and tt_id in EXPENSE_TT_IDS and not et_primary:
            et_primary = PERSONAL_ET_ID

        for i in range(body.installments):
            m = start.month + i
            yr = start.year + (m - 1) // 12
            mo = ((m - 1) % 12) + 1
            import calendar
            last_day = calendar.monthrange(yr, mo)[1]
            day = min(start.day, last_day)
            txn_date = str(date_cls(yr, mo, day))
            amt = monthly if i < body.installments - 1 else last_amount
            txns.append((txn_date, "00:00:00", body.note, amt, amt,
                         body.currency, body.description, body.payment_status,
                         0.0, 0.0, i + 1, body.installments,
                         acct_id, cat_id, tt_id, et_primary, et_secondary, now, now))

        conn.executemany(
            """INSERT INTO transactions
               (date, time, note, amount, php, currency, description, payment_status,
                personal_amount, non_personal_amount, installment_num, installment_total,
                account_id, category_id, transaction_type_id,
                expense_type_id_primary, expense_type_id_secondary,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            txns,
        )
    return {"created": body.installments, "monthly_amount": monthly, "last_amount": last_amount}


@router.get("/api/export/csv")
async def export_csv():
    """Export all transactions as CSV."""
    with _db() as conn:
        rows = conn.execute(
            f"""{_TXN_SELECT}
                ORDER BY t.date DESC, t.time DESC"""
        ).fetchall()

    headers = ["id", "date", "time", "account", "category", "note", "type",
               "amount", "php", "currency", "description",
               "expense_type_primary", "expense_type_secondary",
               "payment_status", "personal_amount", "non_personal_amount",
               "installment_num", "installment_total",
               "account_id", "category_id", "transaction_type_id",
               "expense_type_id_primary", "expense_type_id_secondary"]

    def _generate():
        yield ",".join(headers) + "\n"
        for row in rows:
            d = dict(row)
            vals = [str(d.get(h, "")).replace('"', '""') for h in headers]
            yield ",".join(f'"{v}"' for v in vals) + "\n"

    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=finance-export.csv"},
    )


# ── Credit Card Endpoints ─────────────────────────────────────────────────────

# Canonical expense type categories for CC breakdown:
# Primary is always Personal; secondary distinguishes Personal / Family / Friends
CC_EXPENSE_CATEGORIES = ["Personal", "Family", "Friends"]

PAYMENT_STATUSES = ["Unpaid", "Paid"]


def _get_card_account_ids(conn) -> list[int]:
    """Get card account IDs, excluding hidden accounts."""
    if EXCLUDED_ACCOUNTS:
        ea_ph = ",".join("?" * len(EXCLUDED_ACCOUNTS))
        return [r[0] for r in conn.execute(
            f"SELECT account_id FROM accounts WHERE group_name = 'Card' AND name NOT IN ({ea_ph}) ORDER BY sort_order, name",
            EXCLUDED_ACCOUNTS,
        ).fetchall()]
    return [r[0] for r in conn.execute(
        "SELECT account_id FROM accounts WHERE group_name = 'Card' ORDER BY sort_order, name"
    ).fetchall()]


def _get_card_account_names(conn) -> list[str]:
    """Get card account names, excluding hidden accounts."""
    if EXCLUDED_ACCOUNTS:
        ea_ph = ",".join("?" * len(EXCLUDED_ACCOUNTS))
        return [r[0] for r in conn.execute(
            f"SELECT name FROM accounts WHERE group_name = 'Card' AND name NOT IN ({ea_ph}) ORDER BY sort_order, name",
            EXCLUDED_ACCOUNTS,
        ).fetchall()]
    return [r[0] for r in conn.execute(
        "SELECT name FROM accounts WHERE group_name = 'Card' ORDER BY sort_order, name"
    ).fetchall()]


@router.get("/api/credit-cards/summary")
async def credit_card_summary(
    month: str = Query("", description="YYYY-MM (legacy)"),
    date_from: str = Query("", description="Start date YYYY-MM-DD"),
    date_to: str = Query("", description="End date YYYY-MM-DD"),
    card: str = Query("", description="Card account name for cutoff calculation"),
):
    """Per-card summary: total charged, paid/unpaid, personal vs non-personal.
    
    Supports both legacy month=YYYY-MM and new date_from/date_to (cutoff-based).
    """
    with _db() as conn:
        card_ids = _get_card_account_ids(conn)
        if not card_ids:
            return {"month": month, "cards": [], "totals": {}, "expense_breakdown": []}

        placeholders = ",".join("?" * len(card_ids))

        # Determine date range
        if date_from and date_to:
            date_filter = "AND t.date >= ? AND t.date <= ?"
            date_params = [date_from, date_to]
        elif month:
            date_filter = "AND t.date LIKE ?"
            date_params = [f"{month}%"]
        else:
            current_month = date.today().strftime("%Y-%m")
            date_filter = "AND t.date LIKE ?"
            date_params = [f"{current_month}%"]

        exp_ph = ','.join('?' * len(EXPENSE_TT_IDS))
        exp_ids = list(EXPENSE_TT_IDS)

        # Per-card breakdown (WHERE already filters to expense types, so no need for CASE on type)
        rows = conn.execute(
            f"""SELECT
                    a.name AS account,
                    COALESCE(SUM(t.php), 0) AS total_charged,
                    COALESCE(SUM(CASE WHEN t.payment_status = 'Paid' THEN t.php ELSE 0 END), 0) AS total_paid,
                    COALESCE(SUM(CASE WHEN t.payment_status = 'Unpaid' OR t.payment_status = '' THEN t.php ELSE 0 END), 0) AS total_unpaid,
                    COALESCE(SUM(t.personal_amount), 0) AS personal_total,
                    COALESCE(SUM(t.non_personal_amount), 0) AS non_personal_total,
                    COUNT(*) AS txn_count
                FROM transactions t
                JOIN accounts a ON t.account_id = a.account_id
                WHERE t.account_id IN ({placeholders})
                  {date_filter}
                  AND t.transaction_type_id IN ({exp_ph})
                GROUP BY a.name
                ORDER BY a.name""",
            card_ids + date_params + exp_ids,
        ).fetchall()

        cards = []
        for r in rows:
            cards.append({
                "account": r["account"],
                "total_charged": round(r["total_charged"], 2),
                "total_paid": round(r["total_paid"], 2),
                "total_unpaid": round(r["total_unpaid"], 2),
                "personal_total": round(r["personal_total"], 2),
                "non_personal_total": round(r["non_personal_total"], 2),
                "txn_count": r["txn_count"],
            })

        # Expense type breakdown using primary/secondary FK
        raw_breakdown = conn.execute(
            f"""SELECT
                    COALESCE(es.name, 'Personal') AS expense_category,
                    COALESCE(SUM(t.php), 0) AS total,
                    COUNT(*) AS count
                FROM transactions t
                LEFT JOIN expense_types es ON t.expense_type_id_secondary = es.expense_type_id
                WHERE t.account_id IN ({placeholders})
                  {date_filter}
                  AND t.transaction_type_id IN ({exp_ph})
                GROUP BY expense_category
                ORDER BY total DESC""",
            card_ids + date_params + list(EXPENSE_TT_IDS),
        ).fetchall()

        # Map to canonical categories (Personal, Family, Friends)
        merged: dict[str, dict] = {}
        for r in raw_breakdown:
            cat = r["expense_category"]
            if cat not in CC_EXPENSE_CATEGORIES:
                cat = "Personal"
            if cat not in merged:
                merged[cat] = {"total": 0.0, "count": 0}
            merged[cat]["total"] += r["total"]
            merged[cat]["count"] += r["count"]
        expense_breakdown = [
            {"type": k, "total": round(v["total"], 2), "count": v["count"]}
            for k, v in sorted(merged.items(), key=lambda x: -x[1]["total"])
        ]

        totals = {
            "total_charged": round(sum(c["total_charged"] for c in cards), 2),
            "total_paid": round(sum(c["total_paid"] for c in cards), 2),
            "total_unpaid": round(sum(c["total_unpaid"] for c in cards), 2),
            "personal_total": round(sum(c["personal_total"] for c in cards), 2),
            "non_personal_total": round(sum(c["non_personal_total"] for c in cards), 2),
            "txn_count": sum(c["txn_count"] for c in cards),
        }

    return {
        "month": month or (date_from[:7] if date_from else date.today().strftime("%Y-%m")),
        "date_from": date_from,
        "date_to": date_to,
        "cards": cards,
        "totals": totals,
        "expense_breakdown": expense_breakdown,
    }


@router.get("/api/credit-cards/transactions")
async def credit_card_transactions(
    page:     int = Query(1,  ge=1),
    per_page: int = Query(50, ge=1, le=500),
    account:  str = Query("", description="Filter by card account name"),
    category: str = Query("", description="Filter by category"),
    ttype:    str = Query("", alias="type", description="Filter by type"),
    search:   str = Query("", description="Search in note/description"),
    date_from:str = Query("", description="Start date YYYY-MM-DD"),
    date_to:  str = Query("", description="End date YYYY-MM-DD"),
    expense_type: str = Query("", description="Filter by expense type"),
    payment_status: str = Query("", description="Filter by payment status"),
    sort:     str = Query("date_desc", description="Sort: date_desc|date_asc|amount_desc|amount_asc"),
):
    """List credit card transactions with filters."""
    where_clauses: list[str] = []
    params: list[Any] = []

    with _db() as conn:
        card_ids = _get_card_account_ids(conn)

    if not card_ids:
        return {"total": 0, "page": 1, "per_page": per_page, "pages": 0, "items": []}

    if account:
        where_clauses.append("a.name = ?")
        params.append(account)
    else:
        placeholders = ",".join("?" * len(card_ids))
        where_clauses.append(f"t.account_id IN ({placeholders})")
        params.extend(card_ids)

    # Only expenses
    exp_ph = ','.join('?' * len(EXPENSE_TT_IDS))
    where_clauses.append(f"t.transaction_type_id IN ({exp_ph})")
    params.extend(EXPENSE_TT_IDS)

    if date_from:
        where_clauses.append("t.date >= ?")
        params.append(date_from)
    if date_to:
        where_clauses.append("t.date <= ?")
        params.append(date_to)
    if category:
        where_clauses.append("c.name = ?")
        params.append(category)
    if expense_type:
        if expense_type == "Personal":
            where_clauses.append("(ep.name = 'Personal' AND es.name IS NULL)")
        else:
            where_clauses.append("es.name = ?")
            params.append(expense_type)
    if payment_status:
        if payment_status == "Unpaid":
            where_clauses.append("(t.payment_status = 'Unpaid' OR t.payment_status = '')")
        else:
            where_clauses.append("t.payment_status = ?")
            params.append(payment_status)
    if search:
        where_clauses.append("(t.note LIKE ? OR t.description LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]

    where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    sort_map = {
        "date_desc": "t.date DESC, t.time DESC",
        "date_asc":  "t.date ASC, t.time ASC",
        "amount_desc": "t.amount DESC",
        "amount_asc":  "t.amount ASC",
    }
    order_sql = sort_map.get(sort, "t.date DESC, t.time DESC")
    offset = (page - 1) * per_page

    with _db() as conn:
        total = conn.execute(f"SELECT COUNT(*) {_TXN_FROM} {where_sql}", params).fetchone()[0]
        rows = conn.execute(
            f"{_TXN_SELECT} {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "items": [_row_to_dict(r) for r in rows],
    }


class BulkPaymentUpdate(BaseModel):
    ids: List[int]
    payment_status: str = "Paid"


@router.post("/api/credit-cards/mark-paid")
async def bulk_mark_payment(body: BulkPaymentUpdate):
    """Update payment_status for multiple transactions at once."""
    if not body.ids:
        raise HTTPException(status_code=422, detail="No transaction IDs provided")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _db() as conn:
        placeholders = ",".join("?" * len(body.ids))
        conn.execute(
            f"UPDATE transactions SET payment_status = ?, updated_at = ? WHERE id IN ({placeholders})",
            [body.payment_status, now] + body.ids,
        )
    return {"updated": len(body.ids), "payment_status": body.payment_status}


@router.get("/api/credit-cards/cards")
async def list_credit_cards():
    """List all card accounts with current outstanding balance."""
    with _db() as conn:
        credit_ph = ','.join('?' * len(CREDIT_TT_IDS))
        debit_ph = ','.join('?' * len(DEBIT_TT_IDS))
        excl_clause = ""
        excl_params: list = []
        if EXCLUDED_ACCOUNTS:
            ea_ph = ",".join("?" * len(EXCLUDED_ACCOUNTS))
            excl_clause = f"AND a.name NOT IN ({ea_ph})"
            excl_params = list(EXCLUDED_ACCOUNTS)
        rows = conn.execute(
            f"""SELECT
                    a.name, a.icon, a.sort_order, a.cutoff_day,
                    COALESCE(SUM(CASE WHEN t.transaction_type_id IN ({credit_ph}) THEN t.php ELSE 0 END), 0) AS credits,
                    COALESCE(SUM(CASE WHEN t.transaction_type_id IN ({debit_ph}) THEN t.php ELSE 0 END), 0) AS debits,
                    COUNT(t.id) AS txn_count
                FROM accounts a
                LEFT JOIN transactions t ON t.account_id = a.account_id
                WHERE a.group_name = 'Card' {excl_clause}
                GROUP BY a.account_id
                ORDER BY a.sort_order, a.name""",
            list(CREDIT_TT_IDS) + list(DEBIT_TT_IDS) + excl_params,
        ).fetchall()

    return {
        "cards": [{
            "name": r["name"],
            "icon": r["icon"],
            "cutoff_day": r["cutoff_day"],
            "balance": round(r["credits"] - r["debits"], 2),
            "credits": round(r["credits"], 2),
            "debits": round(r["debits"], 2),
            "txn_count": r["txn_count"],
        } for r in rows]
    }


@router.get("/api/credit-cards/monthly-trend")
async def cc_monthly_trend(months: int = Query(6, ge=1, le=24)):
    """Monthly credit card spending breakdown for the last N months."""
    with _db() as conn:
        card_ids = _get_card_account_ids(conn)
        if not card_ids:
            return {"data": []}
        placeholders = ",".join("?" * len(card_ids))
        exp_ph = ','.join('?' * len(EXPENSE_TT_IDS))
        rows = conn.execute(
            f"""SELECT
                    substr(t.date,1,7) AS month,
                    COALESCE(SUM(t.php), 0) AS total,
                    COALESCE(SUM(t.personal_amount), 0) AS personal,
                    COALESCE(SUM(t.non_personal_amount), 0) AS non_personal,
                    COALESCE(SUM(CASE WHEN t.payment_status = 'Paid' THEN t.php ELSE 0 END), 0) AS paid,
                    COALESCE(SUM(CASE WHEN t.payment_status != 'Paid' THEN t.php ELSE 0 END), 0) AS unpaid
                FROM transactions t
                WHERE t.account_id IN ({placeholders})
                  AND t.transaction_type_id IN ({exp_ph})
                GROUP BY month
                ORDER BY month DESC
                LIMIT ?""",
            card_ids + list(EXPENSE_TT_IDS) + [months],
        ).fetchall()

    result = [{
        "month": r["month"],
        "total": round(r["total"], 2),
        "personal": round(r["personal"], 2),
        "non_personal": round(r["non_personal"], 2),
        "paid": round(r["paid"], 2),
        "unpaid": round(r["unpaid"], 2),
    } for r in rows]
    result.reverse()
    return {"data": result}


@router.get("/api/health", include_in_schema=False)
async def finance_health():
    return {"status": "ok"}
