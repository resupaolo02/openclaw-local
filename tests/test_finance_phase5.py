#!/usr/bin/env python3
"""Phase 5 E2E tests for finance app after schema normalization."""

import json
import sys
import urllib.request
import urllib.error
import urllib.parse

BASE = "http://localhost:9096"

def api(path: str, method="GET", body=None):
    """Call the finance API and return parsed JSON."""
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    if body:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

results = []

def test(name, fn):
    try:
        fn()
        results.append(("✅", name))
    except Exception as e:
        results.append(("❌", f"{name}: {e}"))


# ── Health ────────────────────────────────────────────────────────────────────
def t_health():
    data = api("/api/health")
    assert data["status"] == "ok"
test("Health check", t_health)


# ── Meta ──────────────────────────────────────────────────────────────────────
def t_meta():
    m = api("/api/meta")
    assert "types" in m
    assert "expense_types" in m
    assert "card_accounts" in m
    assert "categories" in m
    assert "Exp." in m["types"]
    assert "Income" in m["types"]
    assert "Personal" in m["expense_types"]
    assert "payment_statuses" in m
    # Check accounts have cutoff_day
    grouped = m["accounts_grouped"]
    for group, accounts in grouped.items():
        for acct in accounts:
            assert "cutoff_day" in acct, f"Missing cutoff_day in {acct['name']}"
test("Meta endpoint returns types, expense_types, cutoff_day", t_meta)


# ── Transactions ──────────────────────────────────────────────────────────────
def t_transactions():
    data = api("/api/transactions?per_page=5")
    assert data["total"] > 0
    item = data["items"][0]
    # Check new field names exist
    assert "type" in item, "Missing 'type' from JOIN alias"
    assert "category" in item, "Missing 'category' from JOIN alias"
    assert "account" in item, "Missing 'account' from JOIN alias"
    assert "transaction_type_id" in item
    assert "expense_type_id_primary" in item
    assert "expense_type_id_secondary" in item
    assert "expense_type_primary" in item
    assert "expense_type_secondary" in item
    # type should be a valid string, not None
    assert item["type"] is not None, "type should not be None"
test("Transactions list with new schema fields", t_transactions)


def t_transaction_single():
    data = api("/api/transactions/1")
    assert data["id"] == 1
    assert "type" in data
    assert "category" in data
    assert "account" in data
test("Single transaction GET", t_transaction_single)


# ── Summary ───────────────────────────────────────────────────────────────────
def t_summary():
    data = api("/api/summary?month=2025-04")
    assert "income" in data
    assert "expenses" in data
    assert "net" in data
    assert data["income"] >= 0
    assert data["expenses"] >= 0
test("Summary for 2025-04", t_summary)


def t_summary_alltime():
    data = api("/api/summary")
    assert data["all_time_income"] > 0
    assert data["all_time_expenses"] > 0
test("Summary all-time totals", t_summary_alltime)


# ── Monthly Trend ─────────────────────────────────────────────────────────────
def t_monthly_trend():
    data = api("/api/monthly-trend")
    assert "data" in data
    assert len(data["data"]) > 0
    item = data["data"][0]
    assert "month" in item
    assert "income" in item
    assert "expenses" in item
test("Monthly trend", t_monthly_trend)


# ── Category Breakdown ────────────────────────────────────────────────────────
def t_category_breakdown():
    data = api("/api/category-breakdown?month=2025-04")
    assert "data" in data
    assert len(data["data"]) > 0
    item = data["data"][0]
    assert "category" in item
    assert "total" in item
test("Category breakdown", t_category_breakdown)


# ── Accounts ──────────────────────────────────────────────────────────────────
def t_accounts():
    data = api("/api/accounts")
    assert len(data["accounts"]) > 0
    acct = data["accounts"][0]
    assert "cutoff_day" in acct, "Account missing cutoff_day"
    assert acct["cutoff_day"] == 15 or isinstance(acct["cutoff_day"], int)
test("Accounts with cutoff_day", t_accounts)


def t_account_records():
    data = api("/api/account-records")
    assert len(data["accounts"]) > 0
    acct = data["accounts"][0]
    assert "cutoff_day" in acct
test("Account records with cutoff_day", t_account_records)


# ── Credit Card Endpoints ─────────────────────────────────────────────────────
def t_cc_cards():
    data = api("/api/credit-cards/cards")
    assert len(data["cards"]) > 0
    card = data["cards"][0]
    assert "cutoff_day" in card, "CC card missing cutoff_day"
test("CC cards list with cutoff_day", t_cc_cards)


def t_cc_summary_cutoff():
    """Test CC summary with cutoff-based date range."""
    data = api("/api/credit-cards/summary?date_from=2025-03-16&date_to=2025-04-15")
    assert "cards" in data
    assert "totals" in data
    assert "expense_breakdown" in data
    assert data["date_from"] == "2025-03-16"
    assert data["date_to"] == "2025-04-15"
test("CC summary with cutoff date range", t_cc_summary_cutoff)


def t_cc_summary_month():
    """Test CC summary with legacy month parameter still works."""
    data = api("/api/credit-cards/summary?month=2025-04")
    assert len(data["cards"]) > 0
    assert data["totals"]["total_charged"] > 0
test("CC summary with legacy month param", t_cc_summary_month)


def t_cc_expense_breakdown():
    """Verify expense breakdown uses primary/secondary, maps to 3 categories."""
    data = api("/api/credit-cards/summary?month=2025-04")
    types = [b["type"] for b in data["expense_breakdown"]]
    for t in types:
        assert t in ["Personal", "Family", "Friends"], f"Unexpected expense type in CC breakdown: {t}"
test("CC expense breakdown uses 3 canonical categories", t_cc_expense_breakdown)


def t_cc_transactions():
    data = api("/api/credit-cards/transactions?per_page=3&date_from=2025-03-16&date_to=2025-04-15")
    assert data["total"] >= 0
    if data["items"]:
        item = data["items"][0]
        assert "type" in item
        assert "expense_type_primary" in item
        assert "expense_type_secondary" in item
test("CC transactions with cutoff range", t_cc_transactions)


def t_cc_monthly_trend():
    data = api("/api/credit-cards/monthly-trend")
    assert "data" in data
test("CC monthly trend", t_cc_monthly_trend)


# ── Transaction Type Filter ───────────────────────────────────────────────────
def t_filter_by_type():
    data = api("/api/transactions?per_page=3&type=Income")
    if data["items"]:
        for item in data["items"]:
            assert item["type"] == "Income", f"Expected Income, got {item['type']}"
test("Filter transactions by type name", t_filter_by_type)


def t_filter_by_category():
    encoded = urllib.parse.quote("🍔 Food")
    data = api(f"/api/transactions?per_page=3&category={encoded}")
    if data["items"]:
        for item in data["items"]:
            assert "Food" in (item["category"] or ""), f"Expected Food category, got {item['category']}"
test("Filter transactions by category name", t_filter_by_category)


# ── Export ────────────────────────────────────────────────────────────────────
def t_export_csv():
    url = f"{BASE}/api/export/csv"
    with urllib.request.urlopen(url) as resp:
        first_line = resp.readline().decode().strip()
        assert "id" in first_line
        assert "expense_type_primary" in first_line
        assert "expense_type_secondary" in first_line
        assert "transaction_type_id" in first_line
test("CSV export has new column headers", t_export_csv)


# ── Expense Type Guardrails ──────────────────────────────────────────────────
def t_guardrail_primary_must_be_personal():
    """Creating a transaction with non-Personal primary should fail."""
    try:
        api("/api/transactions", method="POST", body={
            "date": "2099-01-01",
            "type": "Exp.",
            "amount": 100,
            "expense_type_primary": "Family",
        })
        assert False, "Should have rejected non-Personal primary"
    except urllib.error.HTTPError as e:
        assert e.code == 422
test("Guardrail: primary expense type must be Personal", t_guardrail_primary_must_be_personal)


# ── Print Results ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Phase 5 E2E Test Results")
print("=" * 60)
passed = 0
failed = 0
for status, name in results:
    print(f"  {status} {name}")
    if status == "✅":
        passed += 1
    else:
        failed += 1

print(f"\n  {passed}/{passed + failed} passed")
if failed:
    print(f"  {failed} FAILED")
    sys.exit(1)
else:
    print("  ALL PASS ✓")
    sys.exit(0)
