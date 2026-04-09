"""Tests for finance router — SQLite operations."""

import os
import sys
import sqlite3

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "hub"))


class TestFinanceDB:
    """Test finance database initialization and operations."""

    def test_db_tables_created(self, finance_db):
        """Verify all required tables exist after init."""
        conn = sqlite3.connect(finance_db)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()

        assert "transactions" in tables
        assert "accounts" in tables
        assert "categories" in tables
        assert "transaction_types" in tables
        assert "expense_types" in tables

    def test_transaction_columns(self, finance_db):
        """Verify transaction table has expected columns."""
        conn = sqlite3.connect(finance_db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()]
        conn.close()

        assert "date" in cols
        assert "amount" in cols
        assert "account_id" in cols
        assert "category_id" in cols
        assert "transaction_type_id" in cols

    def test_insert_transaction(self, finance_db):
        """Test inserting and retrieving a transaction."""
        conn = sqlite3.connect(finance_db)
        conn.execute("""
            INSERT INTO transactions (date, time, amount, description, account_id,
                                      category_id, transaction_type_id)
            VALUES ('2026-01-15', '10:00', 500.0, 'Test expense', 1, 1, 1)
        """)
        conn.commit()

        row = conn.execute(
            "SELECT * FROM transactions WHERE description='Test expense'"
        ).fetchone()
        conn.close()

        assert row is not None


class TestFinanceAPI:
    """Test finance API endpoints via TestClient."""

    @pytest.fixture(autouse=True)
    def setup_finance(self, finance_db):
        import routers.finance as fm
        fm.DB_PATH = finance_db

    def test_health_endpoint(self):
        from routers.finance import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/finance")
        client = TestClient(app)

        resp = client.get("/finance/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_summary_endpoint(self):
        from routers.finance import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/finance")
        client = TestClient(app)

        resp = client.get("/finance/api/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "expenses" in data or "all_time_expenses" in data

    def test_transactions_list(self):
        from routers.finance import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/finance")
        client = TestClient(app)

        resp = client.get("/finance/api/transactions")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "per_page" in data

    def test_create_transaction(self):
        from routers.finance import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/finance")
        client = TestClient(app)

        resp = client.post("/finance/api/transactions", json={
            "date": "2026-01-20",
            "account": "Cash",
            "category": "Food",
            "type": "Expense",
            "amount": 250.0,
            "description": "API test transaction"
        })
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data.get("id") is not None

    def test_accounts_endpoint(self):
        from routers.finance import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/finance")
        client = TestClient(app)

        resp = client.get("/finance/api/accounts")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "accounts" in data

    def test_meta_endpoint(self):
        from routers.finance import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/finance")
        client = TestClient(app)

        resp = client.get("/finance/api/meta")
        assert resp.status_code == 200
        data = resp.json()
        assert "categories" in data
