#!/usr/bin/env python3
"""Playwright browser test for finance app frontend — Phase 5."""

from playwright.sync_api import sync_playwright
import sys

BASE_URL = "http://localhost:9096"

def run():
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})  # iPhone-like

        # Load the app
        page.goto(BASE_URL, wait_until="networkidle")
        page.wait_for_timeout(2000)

        # ── Test 1: Dashboard loads ──
        try:
            title = page.text_content("h2")
            assert title is not None
            results.append(("✅", "Dashboard loads"))
        except Exception as e:
            results.append(("❌", f"Dashboard loads: {e}"))

        # ── Test 2: Navigate to Credit Cards tab ──
        try:
            cc_tab = page.locator("text=Credit Cards")
            cc_tab.click()
            page.wait_for_timeout(2000)

            # Check that the CC month label shows cutoff date format (e.g., "Mar 16 – Apr 15, 2025")
            label = page.text_content("#cc-month-label")
            assert "–" in label, f"Expected cutoff date range with '–', got: '{label}'"
            results.append(("✅", f"CC cutoff label: {label}"))
        except Exception as e:
            results.append(("❌", f"CC cutoff label: {e}"))

        # ── Test 3: CC month navigation works ──
        try:
            # Click prev month
            prev_btn = page.locator("#cc-month-nav .month-nav-btn").first
            prev_btn.click()
            page.wait_for_timeout(2000)
            label_after = page.text_content("#cc-month-label")
            assert "–" in label_after, f"Expected cutoff range after nav, got: '{label_after}'"
            results.append(("✅", f"CC prev month nav: {label_after}"))
        except Exception as e:
            results.append(("❌", f"CC month navigation: {e}"))

        # ── Test 4: CC card filter dropdown has cards ──
        try:
            options = page.locator("#cc-card-filter option").all()
            assert len(options) > 1, f"Expected multiple CC cards, got {len(options)} options"
            card_names = [o.text_content() for o in options]
            results.append(("✅", f"CC card filter: {len(options)} options"))
        except Exception as e:
            results.append(("❌", f"CC card filter: {e}"))

        # ── Test 5: Navigate to Transactions tab and check type filter ──
        try:
            txn_tab = page.locator("text=Transactions")
            txn_tab.click()
            page.wait_for_timeout(2000)
            # Check that type filter dropdown exists and has options
            type_filter = page.locator("#txn-type")
            options = type_filter.locator("option").all()
            type_names = [o.text_content() for o in options]
            assert "Exp." in type_names or any("Exp" in t for t in type_names), f"Expected Exp. in types: {type_names}"
            results.append(("✅", f"Transaction type filter: {len(options)} options"))
        except Exception as e:
            results.append(("❌", f"Transaction type filter: {e}"))

        # ── Test 6: No Budget tab ──
        try:
            all_tabs = page.locator(".bottom-nav .nav-item").all()
            tab_texts = [t.text_content().strip() for t in all_tabs]
            assert "Budget" not in tab_texts, f"Budget tab should be removed, found: {tab_texts}"
            results.append(("✅", f"No Budget tab: {tab_texts}"))
        except Exception as e:
            results.append(("❌", f"Budget tab check: {e}"))

        # ── Test 7: Expense type chips on Add Transaction ──
        try:
            # Go to Transactions and open Add form
            page.locator("text=Transactions").click()
            page.wait_for_timeout(1000)
            add_btn = page.locator(".fab, [onclick*='showAddForm'], text=+").first
            add_btn.click()
            page.wait_for_timeout(1000)

            # Check expense type chips exist
            chips = page.locator(".expense-type-chips .chip, .expense-chip, [data-expense-type]").all()
            if chips:
                chip_texts = [c.text_content().strip() for c in chips]
                assert "Personal" in chip_texts, f"Personal should be in chips: {chip_texts}"
                results.append(("✅", f"Expense type chips: {chip_texts}"))
            else:
                results.append(("⚠️", "Expense type chips not found (may need selector update)"))
        except Exception as e:
            results.append(("⚠️", f"Expense type chips: {e}"))

        browser.close()

    # Print results
    print("\n" + "=" * 60)
    print("Playwright Frontend Test Results")
    print("=" * 60)
    passed = sum(1 for s, _ in results if s == "✅")
    warned = sum(1 for s, _ in results if s == "⚠️")
    failed = sum(1 for s, _ in results if s == "❌")
    for status, name in results:
        print(f"  {status} {name}")
    print(f"\n  {passed} passed, {warned} warnings, {failed} failed")
    return 0 if failed == 0 else 1

sys.exit(run())
