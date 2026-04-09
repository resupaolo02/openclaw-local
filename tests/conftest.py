"""Shared fixtures for hub tests."""

import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace directory with empty DB."""
    db_path = tmp_path / "openclaw.db"
    db_path.touch()
    os.environ["WORKSPACE_DIR"] = str(tmp_path)
    yield tmp_path
    os.environ.pop("WORKSPACE_DIR", None)


@pytest.fixture
def finance_db(tmp_workspace):
    """Initialize a finance DB with schema and seed data."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "hub"))

    from routers.finance import _init_db, DB_PATH

    # Override DB_PATH for test
    import routers.finance as fm
    original_path = fm.DB_PATH
    fm.DB_PATH = str(tmp_workspace / "openclaw.db")
    _init_db()
    yield fm.DB_PATH
    fm.DB_PATH = original_path


@pytest.fixture
def nutrition_db(tmp_workspace):
    """Initialize a nutrition DB with schema and seed data."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "hub"))

    import routers.nutrition as nm
    original_path = nm.DB_PATH
    nm.DB_PATH = str(tmp_workspace / "openclaw.db")

    conn = sqlite3.connect(nm.DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS food_database (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT, source TEXT DEFAULT 'custom',
            food_name TEXT NOT NULL, brand TEXT DEFAULT '',
            serving_size TEXT DEFAULT '1 serving', serving_g REAL DEFAULT 100,
            calories REAL DEFAULT 0, protein_g REAL DEFAULT 0,
            carbs_g REAL DEFAULT 0, fat_g REAL DEFAULT 0,
            fiber_g REAL DEFAULT 0, sugar_g REAL DEFAULT 0,
            sodium_mg REAL DEFAULT 0, tags TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS food_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL, time TEXT DEFAULT '',
            meal_type TEXT DEFAULT 'snack',
            food_name TEXT NOT NULL, serving_size TEXT DEFAULT '',
            calories REAL DEFAULT 0, protein_g REAL DEFAULT 0,
            carbs_g REAL DEFAULT 0, fat_g REAL DEFAULT 0,
            fiber_g REAL DEFAULT 0, sugar_g REAL DEFAULT 0,
            sodium_mg REAL DEFAULT 0, notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS daily_goals (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            calories REAL DEFAULT 2000, protein_g REAL DEFAULT 150,
            carbs_g REAL DEFAULT 200, fat_g REAL DEFAULT 65,
            fiber_g REAL DEFAULT 25
        );
        INSERT OR IGNORE INTO daily_goals (id) VALUES (1);
    """)
    conn.close()
    yield nm.DB_PATH
    nm.DB_PATH = original_path
