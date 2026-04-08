"""
Finance App E2E Tests — Playwright
Covers all 8 user requirements:
1. Dashboard Recent Transactions loads data
2. Dashboard & Transactions page income/expenses/net match
3. Credit card "All Cards" option via navigation
4. Combined CC breakdown with L/R navigation
5. Unpaid indicator visible on transactions & CC transactions
6. Budget tab removed
7. BDO Corporate AMEX excluded from everywhere
8. Only 3 expense type categories (Personal, Family, Friends)
"""

import re
import time
from playwright.sync_api import sync_playwright, expect

import os
BASE = os.environ.get("FINANCE_URL", "http://finance:9096")


def test_all():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Use mobile viewport
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            is_mobile=True,
        )
        page = context.new_page()
        results = []

        def run(name, fn):
            try:
                fn(page)
                results.append((name, "PASS", ""))
                print(f"  ✓ {name}")
            except Exception as e:
                results.append((name, "FAIL", str(e)))
                print(f"  ✗ {name}: {e}")

        # Navigate to app
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(2000)

        # ── Test 1: Dashboard Recent Transactions loads ──
        run("1. Dashboard Recent Transactions loads", test_recent_txns)

        # ── Test 2: Dashboard & Transactions page values match ──
        run("2. Dashboard vs Transactions income/expenses/net match", test_values_match)

        # ── Test 6: Budget tab removed ──
        run("6. Budget tab removed", test_no_budget_tab)

        # ── Switch to Credit Cards tab ──
        page.click('button[data-tab="creditcards"]')
        page.wait_for_timeout(2000)

        # ── Test 3: "All Cards" option available ──
        run("3. All Cards option in CC navigation", test_all_cards_option)

        # ── Test 4: Combined CC breakdown with L/R nav ──
        run("4. CC breakdown L/R navigation works", test_cc_nav)

        # ── Test 7: BDO Corporate AMEX excluded ──
        run("7. BDO Corporate AMEX excluded", test_no_bdo_amex)

        # ── Test 8: Only 3 expense type categories ──
        run("8. Only 3 expense type categories", test_expense_types)

        # ── Test 5: Unpaid indicator visible ──
        run("5. Unpaid indicator on transactions", test_unpaid_indicator)

        # Summary
        print("\n" + "=" * 60)
        passed = sum(1 for _, s, _ in results if s == "PASS")
        failed = sum(1 for _, s, _ in results if s == "FAIL")
        print(f"Results: {passed} passed, {failed} failed out of {len(results)} tests")
        for name, status, msg in results:
            sym = "✓" if status == "PASS" else "✗"
            line = f"  {sym} {name}"
            if msg:
                line += f"  — {msg[:120]}"
            print(line)
        print("=" * 60)

        browser.close()
        if failed > 0:
            exit(1)


def test_recent_txns(page):
    """Test 1: Dashboard recent transactions section has data"""
    page.click('button[data-tab="dashboard"]')
    page.wait_for_timeout(2000)

    container = page.locator("#recent-txn-list")
    expect(container).to_be_visible()

    # Should have transaction items (txn-row class)
    items = container.locator(".txn-row")
    count = items.count()
    assert count > 0, f"Expected >0 recent transactions, got {count}"
    print(f"    (found {count} recent transactions)")


def test_values_match(page):
    """Test 2: Dashboard & Transactions page income/expenses/net should match"""
    page.click('button[data-tab="dashboard"]')
    page.wait_for_timeout(2000)

    dash_income = parse_php(page.locator("#s-income").text_content())
    dash_expenses = parse_php(page.locator("#s-expense").text_content())
    dash_net_val = parse_php(page.locator("#s-net").text_content())

    print(f"    Dashboard: income={dash_income}, expenses={dash_expenses}, net={dash_net_val}")

    page.click('button[data-tab="transactions"]')
    page.wait_for_timeout(2000)

    txn_income = parse_php(page.locator("#txn-sum-income").text_content())
    txn_expenses = parse_php(page.locator("#txn-sum-expense").text_content())
    txn_net_val = parse_php(page.locator("#txn-sum-net").text_content())

    print(f"    Transactions: income={txn_income}, expenses={txn_expenses}, net={txn_net_val}")

    assert abs(dash_income - txn_income) < 0.02, f"Income mismatch: dash={dash_income} vs txn={txn_income}"
    assert abs(dash_expenses - txn_expenses) < 0.02, f"Expenses mismatch: dash={dash_expenses} vs txn={txn_expenses}"
    assert abs(dash_net_val - txn_net_val) < 0.02, f"Net mismatch: dash={dash_net_val} vs txn={txn_net_val}"


def test_no_budget_tab(page):
    """Test 6: Budget tab should not exist"""
    budget_tab = page.locator('button[data-tab="budget"]')
    assert budget_tab.count() == 0, "Budget tab still exists!"
    # Also check there's no budget pane
    budget_pane = page.locator("#tab-budget")
    assert budget_pane.count() == 0, "Budget tab pane still exists!"


def test_all_cards_option(page):
    """Test 3: CC nav should show 'All Cards' as first option"""
    label = page.locator("#cc-nav-label")
    expect(label).to_be_visible()
    text = label.text_content().strip()
    assert text == "All Cards", f"Expected 'All Cards' label, got '{text}'"


def test_cc_nav(page):
    """Test 4: Combined CC breakdown with L/R navigation"""
    label = page.locator("#cc-nav-label")
    page.wait_for_timeout(1000)
    assert label.text_content().strip() == "All Cards"

    # Click next to go to first card
    page.click("#cc-nav-next")
    page.wait_for_timeout(1500)
    card1_name = label.text_content().strip()
    assert card1_name != "All Cards", f"Expected individual card name, got '{card1_name}'"
    print(f"    First card: {card1_name}")

    # Click prev to go back to All Cards
    page.click("#cc-nav-prev")
    page.wait_for_timeout(1500)
    assert label.text_content().strip() == "All Cards", "Should be back to 'All Cards'"

    # Navigate to last card (press prev from All)
    page.click("#cc-nav-prev")
    page.wait_for_timeout(1500)
    last_name = label.text_content().strip()
    assert last_name != "All Cards", f"Expected last card name, got '{last_name}'"
    print(f"    Last card (wrap-around): {last_name}")

    # Verify breakdown card has stats
    breakdown = page.locator("#cc-card-breakdown")
    expect(breakdown).to_be_visible()
    html = breakdown.inner_html()
    assert "Total Charged" in html, "Breakdown should show Total Charged"
    assert "Paid" in html, "Breakdown should show Paid"
    assert "Unpaid" in html, "Breakdown should show Unpaid"

    # Navigate back to All Cards for remaining tests
    page.click("#cc-nav-next")
    page.wait_for_timeout(1000)


def test_no_bdo_amex(page):
    """Test 7: BDO Corporate AMEX should not appear anywhere"""
    # Check CC card filter dropdown
    options = page.locator("#cc-card-filter option").all_text_contents()
    for opt in options:
        assert "BDO Corporate AMEX" not in opt, f"Found BDO Corporate AMEX in card filter: {opt}"

    # Check CC navigation doesn't include it
    label = page.locator("#cc-nav-label")
    # Navigate through all cards
    all_labels = []
    page.click("#cc-nav-next")  # go to first card
    page.wait_for_timeout(300)
    first = label.text_content().strip()
    all_labels.append(first)
    while True:
        page.click("#cc-nav-next")
        page.wait_for_timeout(300)
        name = label.text_content().strip()
        if name == "All Cards" or name == first:
            break
        all_labels.append(name)

    for name in all_labels:
        assert "BDO Corporate AMEX" not in name, f"BDO Corporate AMEX found in CC nav: {name}"
    print(f"    Cards in navigator: {all_labels}")

    # Navigate back to All Cards
    while label.text_content().strip() != "All Cards":
        page.click("#cc-nav-next")
        page.wait_for_timeout(200)

    # Check the full page text for BDO Corporate AMEX
    page.click('button[data-tab="transactions"]')
    page.wait_for_timeout(1500)
    full_text = page.locator("#txn-list").text_content()
    assert "BDO Corporate AMEX" not in full_text, "BDO Corporate AMEX found in transaction list"


def test_expense_types(page):
    """Test 8: Only Personal, Family, Friends in expense type filter"""
    page.click('button[data-tab="creditcards"]')
    page.wait_for_timeout(1000)

    options = page.locator("#cc-expense-type option").all_text_contents()
    print(f"    CC expense type options: {options}")

    allowed = {"All Types", "Personal", "Family", "Friends"}
    for opt in options:
        assert opt.strip() in allowed, f"Unexpected expense type option: '{opt}'"

    # Check we have all 3 types
    type_values = {o.strip() for o in options} - {"All Types"}
    assert type_values == {"Personal", "Family", "Friends"}, f"Expected exactly Personal/Family/Friends, got {type_values}"


def test_unpaid_indicator(page):
    """Test 5: Unpaid indicator visible on card transactions"""
    page.click('button[data-tab="creditcards"]')
    page.wait_for_timeout(3000)

    # Look for credit card transactions with unpaid indicator
    cc_txn_section = page.locator("#cc-txn-list")
    expect(cc_txn_section).to_be_visible()
    html = cc_txn_section.inner_html()

    has_paid = "✓ Paid" in html or "cc-badge-paid" in html
    has_unpaid = "○ Unpaid" in html or "cc-badge-unpaid" in html
    print(f"    CC txns: has_paid={has_paid}, has_unpaid={has_unpaid}")

    # At least one indicator should be present (either paid or unpaid)
    assert has_paid or has_unpaid, "No payment status indicators found in CC transactions"

    # Now check the transactions page for card transactions
    page.click('button[data-tab="transactions"]')
    page.wait_for_timeout(2000)

    txn_html = page.locator("#txn-list").inner_html()
    has_indicator = "✓ Paid" in txn_html or "○ Unpaid" in txn_html
    print(f"    Txn list: has_indicator={has_indicator}")

    if not has_indicator:
        # Filter by a known card account to find card transactions
        print("    (No card transactions on current page - filtering by card account)")
        card_sel = page.locator("#f-account")
        card_opts = card_sel.locator("option").all_text_contents()
        card_name = None
        for opt in card_opts:
            if any(kw in opt for kw in ["HSBC", "BPI", "Eastwest", "RCBC", "Unionbank", "BDO UnionPay", "Metrobank"]):
                card_name = opt.strip()
                break
        if card_name:
            card_sel.select_option(label=card_name)
            page.locator("text=Apply Filters").first.click()
            page.wait_for_timeout(2000)
            txn_html = page.locator("#txn-list").inner_html()
            has_indicator = "✓ Paid" in txn_html or "○ Unpaid" in txn_html
            print(f"    After filtering by {card_name}: has_indicator={has_indicator}")
            assert has_indicator, f"No Paid/Unpaid indicator for card transactions (filtered by {card_name})"


def parse_php(text):
    """Parse '₱1,234.56' or '-₱1,234.56' to float"""
    if not text:
        return 0.0
    text = text.strip()
    negative = text.startswith("-") or text.startswith("−")
    cleaned = re.sub(r"[^\d.]", "", text)
    val = float(cleaned) if cleaned else 0.0
    return -val if negative else val


if __name__ == "__main__":
    test_all()
