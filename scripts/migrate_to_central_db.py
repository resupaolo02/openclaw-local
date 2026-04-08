#!/usr/bin/env python3
"""
Migrate per-service SQLite databases into a single central openclaw.db.

Usage:
    python3 scripts/migrate_to_central_db.py [--workspace /path/to/workspace]

What it does:
    1. Creates openclaw.db in the workspace directory
    2. Copies all tables + data + indexes from finance.db and nutrition.db
    3. Enables WAL mode and busy_timeout for concurrent access
    4. Renames originals to .bak (preserves as backup)

Safe to re-run: skips tables that already exist with data.
"""

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

WORKSPACE = Path("/home/resupaolo/openclaw-local/openclaw-data/workspace")

SOURCE_DBS = {
    "finance.db": [
        "transactions",
        "accounts",
        "budgets",
    ],
    "nutrition.db": [
        "food_log",
        "food_database",
        "daily_goals",
    ],
}


def get_create_statements(conn: sqlite3.Connection, table: str) -> list[str]:
    """Extract CREATE TABLE and CREATE INDEX DDL for a given table."""
    stmts = []
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row and row[0]:
        stmts.append(row[0])

    for idx_row in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (table,),
    ):
        stmts.append(idx_row[0])
    return stmts


def copy_table(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> int:
    """Copy a table's schema and data from src to dst. Returns rows copied."""
    # Check if table already has data in destination
    try:
        existing = dst.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
        if existing > 0:
            print(f"  ⏭  {table}: already has {existing} rows, skipping")
            return 0
    except sqlite3.OperationalError:
        pass  # Table doesn't exist yet

    # Get and execute DDL
    ddl_stmts = get_create_statements(src, table)
    if not ddl_stmts:
        print(f"  ⚠  {table}: no CREATE statement found, skipping")
        return 0

    for stmt in ddl_stmts:
        # Make CREATE statements idempotent
        safe = stmt.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ")
        safe = safe.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ")
        safe = safe.replace("CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS ")
        dst.execute(safe)

    # Copy data
    src_rows = src.execute(f"SELECT * FROM [{table}]").fetchall()
    if not src_rows:
        print(f"  ✓  {table}: schema created (0 rows)")
        return 0

    cols = [desc[0] for desc in src.execute(f"SELECT * FROM [{table}] LIMIT 1").description]
    placeholders = ",".join(["?"] * len(cols))
    col_names = ",".join([f"[{c}]" for c in cols])
    dst.executemany(
        f"INSERT INTO [{table}] ({col_names}) VALUES ({placeholders})", src_rows
    )
    print(f"  ✓  {table}: {len(src_rows)} rows copied")
    return len(src_rows)


def migrate(workspace: Path) -> None:
    central_db = workspace / "openclaw.db"
    print(f"Central database: {central_db}")
    print()

    # Open central DB
    dst = sqlite3.connect(str(central_db))
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA busy_timeout=5000")
    dst.execute("PRAGMA foreign_keys=ON")

    total_tables = 0
    total_rows = 0

    for db_name, tables in SOURCE_DBS.items():
        src_path = workspace / db_name
        if not src_path.exists():
            print(f"⚠  {db_name}: not found at {src_path}, skipping")
            continue

        print(f"📦 Migrating {db_name} ({src_path.stat().st_size / 1024:.0f} KB)")
        src = sqlite3.connect(str(src_path))

        for table in tables:
            rows = copy_table(src, dst, table)
            total_tables += 1
            total_rows += rows

        src.close()

    dst.commit()

    # Verify
    print()
    print("── Verification ──")
    for db_name, tables in SOURCE_DBS.items():
        for table in tables:
            count = dst.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
            print(f"  {table}: {count} rows")

    dst.close()

    # Backup originals
    print()
    print("── Backing up originals ──")
    for db_name in SOURCE_DBS:
        src_path = workspace / db_name
        bak_path = workspace / f"{db_name}.bak"
        if src_path.exists():
            if bak_path.exists():
                print(f"  ⏭  {db_name}.bak already exists, skipping backup")
            else:
                shutil.copy2(src_path, bak_path)
                print(f"  ✓  {db_name} → {db_name}.bak")

    print()
    print(f"✅ Migration complete: {total_tables} tables, {total_rows} rows → openclaw.db")
    print(f"   Central DB size: {central_db.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate to central openclaw.db")
    parser.add_argument("--workspace", type=Path, default=WORKSPACE, help="Workspace directory")
    args = parser.parse_args()

    if not args.workspace.exists():
        print(f"Error: workspace directory {args.workspace} does not exist", file=sys.stderr)
        sys.exit(1)

    migrate(args.workspace)
