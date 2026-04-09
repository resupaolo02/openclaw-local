"""Tests for hub app — route registration and static serving."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "hub"))


class TestRouterImports:
    """Verify all router modules can be imported."""

    def test_import_core(self):
        from routers import core
        assert hasattr(core, "router")

    def test_import_chat(self):
        from routers import chat
        assert hasattr(chat, "router")

    def test_import_finance(self):
        from routers import finance
        assert hasattr(finance, "router")

    def test_import_nutrition(self):
        from routers import nutrition
        assert hasattr(nutrition, "router")

    def test_import_calendar(self):
        from routers import calendar
        assert hasattr(calendar, "router")

    def test_import_monitor(self):
        from routers import monitor
        assert hasattr(monitor, "router")

    def test_import_heartbeat(self):
        from routers import heartbeat
        assert hasattr(heartbeat, "router")


class TestRouteRegistration:
    """Verify critical routes are registered on each router."""

    def test_core_has_health(self):
        from routers.core import router
        paths = [r.path for r in router.routes]
        assert "/health" in paths

    def test_core_has_containers(self):
        from routers.core import router
        paths = [r.path for r in router.routes]
        assert "/containers" in paths

    def test_finance_has_api_routes(self):
        from routers.finance import router
        paths = [r.path for r in router.routes]
        assert "/api/health" in paths
        assert "/api/transactions" in paths
        assert "/api/summary" in paths

    def test_nutrition_has_api_routes(self):
        from routers.nutrition import router
        paths = [r.path for r in router.routes]
        assert "/api/health" in paths
        assert "/api/log" in paths

    def test_chat_has_api_routes(self):
        from routers.chat import router
        paths = [r.path for r in router.routes]
        assert "/api/health" in paths
        assert "/api/chat" in paths

    def test_calendar_has_api_routes(self):
        from routers.calendar import router
        paths = [r.path for r in router.routes]
        assert "/api/health" in paths
        assert "/api/calendar/events" in paths

    def test_monitor_has_api_routes(self):
        from routers.monitor import router
        paths = [r.path for r in router.routes]
        assert "/api/health" in paths
        assert "/api/status" in paths

    def test_heartbeat_has_api_routes(self):
        from routers.heartbeat import router
        paths = [r.path for r in router.routes]
        assert "/api/health" in paths
        assert "/api/heartbeat" in paths


class TestStaticHTML:
    """Test that static HTML files exist."""

    @pytest.fixture
    def static_dir(self):
        return os.path.join(
            os.path.dirname(__file__), "..", "services", "hub", "static"
        )

    @pytest.mark.parametrize("service", [
        "landing", "chat", "finance", "nutrition",
        "calendar", "monitor", "heartbeat"
    ])
    def test_html_exists(self, static_dir, service):
        html = os.path.join(static_dir, service, "index.html")
        assert os.path.isfile(html), f"Missing static/{service}/index.html"
