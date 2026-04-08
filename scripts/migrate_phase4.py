#!/usr/bin/env python3
"""
Phase 4 migration script:
1. Create categories table and add category_id FK to transactions
2. Parse installment indicators (N/M) from notes into installment_num/installment_total
3. Drop the account text column from transactions (replaced by account_id FK)
"""
import sqlite3
import re
import sys
from pathlib import Path

DB_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/workspace/openclaw.db")


def migrate():
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")

    cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()]
    print(f"Starting columns: {cols}")

    # ── 1. Create categories table ────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            category_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    cats = conn.execute(
        "SELECT DISTINCT category FROM transactions WHERE category != '' ORDER BY category"
    ).fetchall()
    for (cat,) in cats:
        conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (cat,))
    print(f"Seeded {len(cats)} categories")

    # ── 2. Add category_id column ─────────────────────────────────────────
    if "category_id" not in cols:
        conn.execute(
            "ALTER TABLE transactions ADD COLUMN category_id INTEGER REFERENCES categories(category_id)"
        )
        print("Added category_id column")

    # Populate category_id from matching names
    conn.execute("""
        UPDATE transactions SET category_id = (
            SELECT category_id FROM categories WHERE categories.name = transactions.category
        ) WHERE category != '' AND (category_id IS NULL OR category_id = 0)
    """)
    updated = conn.execute("SELECT changes()").fetchone()[0]
    print(f"Populated category_id for {updated} rows")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_txn_category_id ON transactions(category_id)")

    # ── 3. Parse installments from notes (N/M) ───────────────────────────
    rows = conn.execute(
        "SELECT id, note FROM transactions WHERE installment_num = 0"
    ).fetchall()
    inst_count = 0
    for row_id, note in rows:
        m = re.search(r'\((\d+)/(\d+)\)', note or '')
        if m:
            num, total = int(m.group(1)), int(m.group(2))
            clean_note = re.sub(r'\s*\(\d+/\d+\)\s*', '', note).strip()
            conn.execute(
                "UPDATE transactions SET installment_num=?, installment_total=?, note=? WHERE id=?",
                (num, total, clean_note, row_id),
            )
            inst_count += 1
    print(f"Parsed installment info from {inst_count} notes")

    # Also strip [N/M] prefix from notes that already have installment data
    bracket_rows = conn.execute(
        "SELECT id, note FROM transactions WHERE installment_num > 0 AND note LIKE '[%/%]%'"
    ).fetchall()
    for row_id, note in bracket_rows:
        clean_note = re.sub(r'^\[\d+/\d+\]\s*', '', note).strip()
        if clean_note != note:
            conn.execute("UPDATE transactions SET note=? WHERE id=?", (clean_note, row_id))
    if bracket_rows:
        print(f"Cleaned [N/M] prefix from {len(bracket_rows)} notes")

    # ── 4. Verify all transactions have account_id ────────────────────────
    if "account" in cols:
        missing = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE account_id IS NULL"
        ).fetchone()[0]
        if missing > 0:
            conn.execute("""
                UPDATE transactions SET account_id = (
                    SELECT account_id FROM accounts WHERE accounts.name = transactions.account
                ) WHERE account_id IS NULL AND account != ''
            """)
            print(f"Backfilled account_id for {missing} rows")

        still_missing = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE account_id IS NULL"
        ).fetchone()[0]
        if still_missing > 0:
            print(f"WARNING: {still_missing} transactions still have NULL account_id!")
            print("Cannot safely drop account column. Aborting drop.")
        else:
            # Drop the account index first
            conn.execute("DROP INDEX IF EXISTS idx_account")
            print("Dropped idx_account index")
            # Drop the account column
            conn.execute("ALTER TABLE transactions DROP COLUMN account")
            print("Dropped account column from transactions")
    else:
        print("account column already dropped — skipping")

    conn.commit()

    # ── Verify ────────────────────────────────────────────────────────────
    cols_after = [r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()]
    print(f"\nFinal columns: {cols_after}")
    txn_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    cat_count = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    inst_count2 = conn.execute("SELECT COUNT(*) FROM transactions WHERE installment_num > 0").fetchone()[0]
    null_acct = conn.execute("SELECT COUNT(*) FROM transactions WHERE account_id IS NULL").fetchone()[0]
    print(f"Transactions: {txn_count}, Categories: {cat_count}, Installments: {inst_count2}, Null account_id: {null_acct}")

    conn.close()
    print("\nMigration complete ✓")


if __name__ == "__main__":
    migrate()
