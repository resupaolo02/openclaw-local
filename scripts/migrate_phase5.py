#!/usr/bin/env python3
"""
Phase 5 Migration: Schema normalization
1. Drop category TEXT column (use category_id FK)
2. Split expense_type_id → expense_type_id_primary + expense_type_id_secondary
3. Create transaction_types table + transaction_type_id FK, drop type TEXT
4. Add cutoff_day to accounts
5. Drop old text columns: category, expense_type, type, and old expense_type_id
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("/home/resupaolo/openclaw-local/openclaw-data/workspace/openclaw.db")

def migrate(conn: sqlite3.Connection):
    cur = conn.cursor()

    # ── 1. Transaction Types table ──────────────────────────────────────────
    print("\n=== Step 1: Create transaction_types table ===")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transaction_types (
            transaction_type_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT    NOT NULL UNIQUE,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Seed from existing distinct type values (preserving exact names)
    type_names = [r[0] for r in cur.execute(
        "SELECT DISTINCT type FROM transactions WHERE type != '' ORDER BY type"
    ).fetchall()]
    print(f"  Found {len(type_names)} distinct type values: {type_names}")
    for name in type_names:
        cur.execute("INSERT OR IGNORE INTO transaction_types (name) VALUES (?)", (name,))
    conn.commit()

    # Verify
    tt_rows = cur.execute("SELECT transaction_type_id, name FROM transaction_types ORDER BY transaction_type_id").fetchall()
    tt_map = {r[1]: r[0] for r in tt_rows}
    print(f"  Transaction types seeded: {dict(tt_rows)}")

    # Add transaction_type_id column
    cols = [r[1] for r in cur.execute("PRAGMA table_info(transactions)").fetchall()]
    if "transaction_type_id" not in cols:
        cur.execute("ALTER TABLE transactions ADD COLUMN transaction_type_id INTEGER REFERENCES transaction_types(transaction_type_id)")
        print("  Added transaction_type_id column")
    else:
        print("  transaction_type_id column already exists")

    # Populate transaction_type_id from type text
    for name, tid in tt_map.items():
        affected = cur.execute(
            "UPDATE transactions SET transaction_type_id = ? WHERE type = ? AND (transaction_type_id IS NULL OR transaction_type_id != ?)",
            (tid, name, tid)
        ).rowcount
        if affected:
            print(f"  Set transaction_type_id={tid} for type='{name}': {affected} rows")
    conn.commit()

    # Verify no NULLs
    null_count = cur.execute("SELECT COUNT(*) FROM transactions WHERE transaction_type_id IS NULL").fetchone()[0]
    if null_count > 0:
        print(f"  WARNING: {null_count} transactions still have NULL transaction_type_id")
    else:
        print("  ✓ All transactions have transaction_type_id populated")

    # ── 2. Expense type split ───────────────────────────────────────────────
    print("\n=== Step 2: Split expense_type into primary/secondary ===")

    # Ensure Personal exists with id=4
    personal_id = cur.execute("SELECT expense_type_id FROM expense_types WHERE name = 'Personal'").fetchone()
    if not personal_id:
        cur.execute("INSERT INTO expense_types (name) VALUES ('Personal')")
        personal_id = cur.execute("SELECT expense_type_id FROM expense_types WHERE name = 'Personal'").fetchone()
    PERSONAL_ID = personal_id[0]
    print(f"  Personal expense_type_id = {PERSONAL_ID}")

    if "expense_type_id_primary" not in cols:
        cur.execute("ALTER TABLE transactions ADD COLUMN expense_type_id_primary INTEGER REFERENCES expense_types(expense_type_id)")
        print("  Added expense_type_id_primary column")
    if "expense_type_id_secondary" not in cols:
        cur.execute("ALTER TABLE transactions ADD COLUMN expense_type_id_secondary INTEGER REFERENCES expense_types(expense_type_id)")
        print("  Added expense_type_id_secondary column")
    conn.commit()

    # Migration logic:
    # - expense_type_id IS NULL or empty → primary=NULL, secondary=NULL (non-expense txns)
    # - expense_type_id = Personal → primary=Personal, secondary=NULL
    # - expense_type_id = other → primary=Personal, secondary=original

    # Set primary=Personal for all that have expense_type_id set
    affected = cur.execute(
        "UPDATE transactions SET expense_type_id_primary = ? WHERE expense_type_id IS NOT NULL AND expense_type_id_primary IS NULL",
        (PERSONAL_ID,)
    ).rowcount
    print(f"  Set primary=Personal for {affected} rows")

    # Set secondary = original expense_type_id where it's NOT Personal
    affected = cur.execute(
        "UPDATE transactions SET expense_type_id_secondary = expense_type_id WHERE expense_type_id IS NOT NULL AND expense_type_id != ? AND expense_type_id_secondary IS NULL",
        (PERSONAL_ID,)
    ).rowcount
    print(f"  Set secondary=original for {affected} non-Personal rows")
    conn.commit()

    # Verify
    dist = cur.execute("""
        SELECT ep.name AS primary_name, es.name AS secondary_name, COUNT(*)
        FROM transactions t
        LEFT JOIN expense_types ep ON t.expense_type_id_primary = ep.expense_type_id
        LEFT JOIN expense_types es ON t.expense_type_id_secondary = es.expense_type_id
        GROUP BY ep.name, es.name
        ORDER BY COUNT(*) DESC
    """).fetchall()
    print("  Distribution after migration:")
    for r in dist:
        print(f"    primary={r[0]}, secondary={r[1]}: {r[2]} rows")

    # ── 3. Cutoff day on accounts ───────────────────────────────────────────
    print("\n=== Step 3: Add cutoff_day to accounts ===")
    acct_cols = [r[1] for r in cur.execute("PRAGMA table_info(accounts)").fetchall()]
    if "cutoff_day" not in acct_cols:
        cur.execute("ALTER TABLE accounts ADD COLUMN cutoff_day INTEGER NOT NULL DEFAULT 15")
        print("  Added cutoff_day column (default 15)")
    else:
        print("  cutoff_day column already exists")
    conn.commit()

    # ── 4. Drop old text columns and old expense_type_id ────────────────────
    print("\n=== Step 4: Drop deprecated columns ===")

    # Re-check columns after additions
    cols = [r[1] for r in cur.execute("PRAGMA table_info(transactions)").fetchall()]

    # Drop category (use category_id)
    if "category" in cols:
        cur.execute("DROP INDEX IF EXISTS idx_category")
        cur.execute("ALTER TABLE transactions DROP COLUMN category")
        print("  Dropped 'category' column")
    else:
        print("  'category' already dropped")

    # Drop type (use transaction_type_id)
    if "type" in cols:
        cur.execute("DROP INDEX IF EXISTS idx_type")
        cur.execute("ALTER TABLE transactions DROP COLUMN type")
        print("  Dropped 'type' column")
    else:
        print("  'type' already dropped")

    # Drop expense_type text (use expense_type_id_primary/secondary)
    if "expense_type" in cols:
        cur.execute("DROP INDEX IF EXISTS idx_expense_type")
        cur.execute("ALTER TABLE transactions DROP COLUMN expense_type")
        print("  Dropped 'expense_type' text column")
    else:
        print("  'expense_type' already dropped")

    # Drop old single expense_type_id (replaced by primary/secondary)
    if "expense_type_id" in cols:
        cur.execute("DROP INDEX IF EXISTS idx_txn_expense_type_id")
        cur.execute("ALTER TABLE transactions DROP COLUMN expense_type_id")
        print("  Dropped old 'expense_type_id' column")
    else:
        print("  'expense_type_id' already dropped")

    conn.commit()

    # ── 5. Create new indexes ───────────────────────────────────────────────
    print("\n=== Step 5: Create new indexes ===")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_txn_type_id ON transactions(transaction_type_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_txn_et_primary ON transactions(expense_type_id_primary)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_txn_et_secondary ON transactions(expense_type_id_secondary)")
    conn.commit()
    print("  Created indexes: idx_txn_type_id, idx_txn_et_primary, idx_txn_et_secondary")

    # ── Final verification ──────────────────────────────────────────────────
    print("\n=== Final Schema ===")
    for r in cur.execute("PRAGMA table_info(transactions)").fetchall():
        print(f"  {r[1]:30s} {r[2]}")
    print()
    for r in cur.execute("PRAGMA table_info(accounts)").fetchall():
        print(f"  accounts.{r[1]:20s} {r[2]}")
    print()
    total = cur.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    tt_count = cur.execute("SELECT COUNT(*) FROM transaction_types").fetchone()[0]
    null_tt = cur.execute("SELECT COUNT(*) FROM transactions WHERE transaction_type_id IS NULL").fetchone()[0]
    null_cat = cur.execute("SELECT COUNT(*) FROM transactions WHERE category_id IS NULL").fetchone()[0]
    print(f"  Total transactions: {total}")
    print(f"  Transaction types: {tt_count}")
    print(f"  NULL transaction_type_id: {null_tt}")
    print(f"  NULL category_id: {null_cat}")


if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        migrate(conn)
        print("\n✅ Migration complete!")
    except Exception as e:
        conn.rollback()
        print(f"\n❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()
