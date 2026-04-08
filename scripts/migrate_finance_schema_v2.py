#!/usr/bin/env python3
"""
Finance schema migration v2:
  1. Drop budgets table
  2. Rename accounts.id → account_id
  3. Drop subcategory from transactions
  4. Create expense_types table
  5. Add account_id and expense_type_id FK columns to transactions
  6. Populate FK columns from existing text data

Safe to re-run: every step checks before acting.
"""

import sqlite3
import sys
from pathlib import Path

DB = Path("/home/resupaolo/openclaw-local/openclaw-data/workspace/openclaw.db")


def col_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info([{table}])")]
    return column in cols


def table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def migrate(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")

    # 1. Drop budgets table
    print("1. Dropping budgets table...")
    if table_exists(conn, "budgets"):
        conn.execute("DROP TABLE budgets")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='budgets'")
        print("   ✓ Dropped")
    else:
        print("   ⏭ Already gone")

    # 2. Rename accounts.id → account_id
    print("2. Renaming accounts.id → account_id...")
    if col_exists(conn, "accounts", "id") and not col_exists(conn, "accounts", "account_id"):
        conn.execute("ALTER TABLE accounts RENAME COLUMN id TO account_id")
        print("   ✓ Renamed")
    elif col_exists(conn, "accounts", "account_id"):
        print("   ⏭ Already renamed")
    else:
        print("   ⚠ Unexpected state")

    # 3. Drop subcategory from transactions
    print("3. Dropping subcategory from transactions...")
    if col_exists(conn, "transactions", "subcategory"):
        conn.execute("ALTER TABLE transactions DROP COLUMN subcategory")
        print("   ✓ Dropped")
    else:
        print("   ⏭ Already gone")

    # 4. Create expense_types table
    print("4. Creating expense_types table...")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS expense_types (
            expense_type_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL UNIQUE,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    existing_types = conn.execute(
        "SELECT DISTINCT expense_type FROM transactions WHERE expense_type != '' ORDER BY expense_type"
    ).fetchall()
    inserted = 0
    for (name,) in existing_types:
        try:
            conn.execute("INSERT INTO expense_types (name) VALUES (?)", (name,))
            inserted += 1
        except sqlite3.IntegrityError:
            pass
    print(f"   ✓ Table ready ({inserted} new types seeded)")

    # 5. Add account_id FK column to transactions
    print("5. Adding account_id to transactions...")
    if not col_exists(conn, "transactions", "account_id"):
        conn.execute("ALTER TABLE transactions ADD COLUMN account_id INTEGER REFERENCES accounts(account_id)")
        print("   ✓ Added")
    else:
        print("   ⏭ Already exists")

    # 6. Add expense_type_id FK column to transactions
    print("6. Adding expense_type_id to transactions...")
    if not col_exists(conn, "transactions", "expense_type_id"):
        conn.execute("ALTER TABLE transactions ADD COLUMN expense_type_id INTEGER REFERENCES expense_types(expense_type_id)")
        print("   ✓ Added")
    else:
        print("   ⏭ Already exists")

    # 7. Populate account_id from account name
    print("7. Populating account_id from account names...")
    updated = conn.execute("""
        UPDATE transactions SET account_id = (
            SELECT account_id FROM accounts WHERE accounts.name = transactions.account
        ) WHERE account_id IS NULL AND account != ''
    """).rowcount
    print(f"   ✓ Updated {updated} rows")

    # 8. Populate expense_type_id from expense_type text
    print("8. Populating expense_type_id from expense_type text...")
    updated = conn.execute("""
        UPDATE transactions SET expense_type_id = (
            SELECT expense_type_id FROM expense_types WHERE expense_types.name = transactions.expense_type
        ) WHERE expense_type_id IS NULL AND expense_type != ''
    """).rowcount
    print(f"   ✓ Updated {updated} rows")

    # 9. Create indexes on new FK columns
    conn.execute("CREATE INDEX IF NOT EXISTS idx_txn_account_id ON transactions(account_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_txn_expense_type_id ON transactions(expense_type_id)")
    print("9. ✓ Indexes created")

    conn.commit()

    # Verify
    print("\n── Verification ──")
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    print(f"   Tables: {tables}")
    for t in tables:
        cnt = conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info([{t}])")]
        print(f"   {t}: {cnt} rows, cols={cols}")

    # Verify FK population
    null_acct = conn.execute("SELECT COUNT(*) FROM transactions WHERE account != '' AND account_id IS NULL").fetchone()[0]
    null_exp = conn.execute("SELECT COUNT(*) FROM transactions WHERE expense_type != '' AND expense_type_id IS NULL").fetchone()[0]
    print(f"\n   Unlinked account_id: {null_acct}")
    print(f"   Unlinked expense_type_id: {null_exp}")

    conn.execute("PRAGMA foreign_keys=ON")
    conn.close()
    print("\n✅ Migration complete!")


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DB
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        sys.exit(1)
    migrate(path)
