"""Tests for nutrition router — SQLite operations and API."""

import os
import sys
import sqlite3

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "hub"))


class TestNutritionDB:
    """Test nutrition database operations."""

    def test_tables_created(self, nutrition_db):
        """Verify all required tables exist."""
        conn = sqlite3.connect(nutrition_db)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()

        assert "food_database" in tables
        assert "food_log" in tables
        assert "daily_goals" in tables

    def test_daily_goals_default(self, nutrition_db):
        """Verify default daily goals are seeded."""
        conn = sqlite3.connect(nutrition_db)
        row = conn.execute("SELECT calories, protein_g FROM daily_goals WHERE id=1").fetchone()
        conn.close()
        assert row[0] == 2000
        assert row[1] == 150

    def test_insert_food_log(self, nutrition_db):
        """Test logging a food entry."""
        conn = sqlite3.connect(nutrition_db)
        conn.execute("""
            INSERT INTO food_log (date, meal_type, food_name, calories, protein_g, carbs_g, fat_g)
            VALUES ('2026-01-15', 'lunch', 'Chicken Adobo', 350, 25, 5, 20)
        """)
        conn.commit()

        row = conn.execute(
            "SELECT food_name, calories FROM food_log WHERE date='2026-01-15'"
        ).fetchone()
        conn.close()

        assert row[0] == "Chicken Adobo"
        assert row[1] == 350

    def test_insert_custom_food(self, nutrition_db):
        """Test adding a custom food to the database."""
        conn = sqlite3.connect(nutrition_db)
        conn.execute("""
            INSERT INTO food_database (food_name, source, serving_size, calories, protein_g, carbs_g, fat_g)
            VALUES ('Test Food', 'custom', '100g', 200, 15, 30, 5)
        """)
        conn.commit()

        row = conn.execute(
            "SELECT food_name, calories FROM food_database WHERE source='custom'"
        ).fetchone()
        conn.close()

        assert row[0] == "Test Food"
        assert row[1] == 200

    def test_daily_summary_query(self, nutrition_db):
        """Test daily summary aggregation."""
        conn = sqlite3.connect(nutrition_db)
        conn.execute("""
            INSERT INTO food_log (date, meal_type, food_name, calories, protein_g, carbs_g, fat_g)
            VALUES ('2026-01-15', 'breakfast', 'Eggs', 150, 12, 1, 10)
        """)
        conn.execute("""
            INSERT INTO food_log (date, meal_type, food_name, calories, protein_g, carbs_g, fat_g)
            VALUES ('2026-01-15', 'lunch', 'Rice + Adobo', 500, 20, 60, 15)
        """)
        conn.commit()

        result = conn.execute("""
            SELECT
                SUM(calories) as total_cal,
                SUM(protein_g) as total_protein,
                COUNT(*) as entries
            FROM food_log
            WHERE date = '2026-01-15'
        """).fetchone()
        conn.close()

        assert result[0] == 650  # 150 + 500
        assert result[1] == 32   # 12 + 20
        assert result[2] == 2


class TestNutritionAPI:
    """Test nutrition API endpoints."""

    @pytest.fixture(autouse=True)
    def setup_nutrition(self, nutrition_db):
        import routers.nutrition as nm
        nm.DB_PATH = nutrition_db

    def test_health_endpoint(self):
        from routers.nutrition import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/nutrition")
        client = TestClient(app)

        resp = client.get("/nutrition/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_goals_endpoint(self):
        from routers.nutrition import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/nutrition")
        client = TestClient(app)

        resp = client.get("/nutrition/api/goals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["calories"] == 2000

    def test_food_log_empty(self):
        from routers.nutrition import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/nutrition")
        client = TestClient(app)

        resp = client.get("/nutrition/api/log?date=2026-01-01")
        assert resp.status_code == 200

    def test_manual_food_log(self):
        from routers.nutrition import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/nutrition")
        client = TestClient(app)

        resp = client.post("/nutrition/api/log", json={
            "date": "2026-01-20",
            "meal_type": "lunch",
            "food_name": "Test Meal",
            "calories": 400,
            "protein_g": 30,
            "carbs_g": 40,
            "fat_g": 15
        })
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data.get("id") is not None

    def test_summary_endpoint(self):
        from routers.nutrition import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/nutrition")
        client = TestClient(app)

        resp = client.get("/nutrition/api/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "totals" in data or "calories" in data or "date" in data
