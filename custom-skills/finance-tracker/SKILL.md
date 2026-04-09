````skill
---
name: finance-tracker
description: Use when the user asks about their personal finances, expenses, income, spending, money, savings, transactions, account balances, net worth, financial summary, or wants to log/add/edit/delete a transaction. This skill has LIVE access to Paolo's real transaction database (5,076+ entries from Aug 2023–present) via both the Finance API at http://finance:9096 and directly via SQLite at /home/node/.openclaw/workspace/openclaw.db. Triggers on: "expenses", "income", "spending", "how much did I spend", "how much did I earn", "transactions", "account balance", "net worth", "financial summary", "budget", "cash flow", "top categories", "add expense", "log expense", "add income", "log income", "track spending", "monthly summary", "what did I spend", "financial tracker", "finance", "money", "savings", "salary", "allowance", "credit card balance", "how much is in", "transfer", "my finances", "openclaw.db", "finance database", "sqlite".
version: 1.0.0
metadata: { "openclaw": { "emoji": "💰" } }
---

# Finance Tracker

Gives Paolo live read/write access to his personal finance database via the Finance microservice. All data is real — fetched directly from the SQLite database populated from his expense-tracker spreadsheet.

## Core Context

- **User:** Paolo Resurreccion (PH-based, currency: PHP ₱)
- **Finance service base URL:** `http://finance:9096` (internal Docker network)
- **Database:** 5,000+ transactions, Aug 2023 – present
- **18 accounts** tracked (see account list below)
- **Date format:** YYYY-MM-DD | **Amounts:** always in PHP unless stated otherwise
- **Web UI:** available at `/finance/` for manual editing

## Database — Direct Access

The SQLite database is accessible directly from this agent. Use Python via `exec` for any query not covered by the API.

- **Path (from this agent):** `/home/node/.openclaw/workspace/openclaw.db`
- **Path (from finance service):** `/workspace/openclaw.db`

### Direct Query Example
```python
import sqlite3
conn = sqlite3.connect('/home/node/.openclaw/workspace/openclaw.db')
cursor = conn.cursor()
cursor.execute("SELECT ...")
rows = cursor.fetchall()
conn.close()
```

### Full Schema

```sql
-- accounts: account_id, name, group_name, icon, sort_order, cutoff_day, balance_offset
-- transactions: id, date, time, note, amount, php, currency, description,
--               payment_status, personal_amount, non_personal_amount,
--               installment_num, installment_total,
--               account_id, category_id, transaction_type_id,
--               expense_type_id_primary, expense_type_id_secondary,
--               created_at, updated_at
-- categories:  category_id, name
-- transaction_types: transaction_type_id, name
-- expense_types: expense_type_id, name
-- food_database, food_log, daily_goals (nutrition — see nutrition-tracker skill)
```

### Account List (account_id → name → group)
```
1  BDO Corporate AMEX          Card
2  BDO UnionPay Gold            Card
3  BPI Banko                    Savings
4  BPI Gold Rewards             Card
5  Cash                         Cash
6  Eastwest Platinum            Card
7  HSBC Visa Gold               Card
8  LazPay Later                 Loan
9  MP2 Savings                  Investment
10 Maya Bank                    Savings
11 Metrobank Travel Visa Sig.   Card
12 RCBC Gold Mastercard         Card
13 RCBC Hexagon                 Savings
14 RCBC Hexagon Platinum        Card
15 SPay Later                   Loan
16 Unionbank Visa Platinum      Card
17 Zed                          Card
2110 BDO (Mastercard)           Card
```

### Reference Lookup Queries
```python
# Get all accounts with IDs
cursor.execute("SELECT account_id, name, group_name FROM accounts ORDER BY sort_order")

# Get all categories
cursor.execute("SELECT category_id, name FROM categories ORDER BY name")

# Get all transaction types
cursor.execute("SELECT transaction_type_id, name FROM transaction_types")
# Results: 1=Exp., 2=Expense Balance, 3=Income, 4=Income Balance, 5=Transfer-In, 6=Transfer-Out

# Get all expense types
cursor.execute("SELECT expense_type_id, name FROM expense_types")
# Results: 1=Business, 2=Family, 3=Friends, 4=Personal, 5=Reimbursement, 6=Work
```

### Useful Direct SQL Patterns
```python
# Monthly spending by category (use this for complex breakdowns)
cursor.execute("""
    SELECT c.name, SUM(t.php) as total, COUNT(*) as count
    FROM transactions t
    JOIN categories c ON t.category_id = c.category_id
    JOIN transaction_types tt ON t.transaction_type_id = tt.transaction_type_id
    WHERE tt.name = 'Exp.' AND t.date LIKE '2026-04%'
    GROUP BY c.category_id ORDER BY total DESC
""")

# Account balance (credits - debits)
cursor.execute("""
    SELECT a.name, a.group_name,
        SUM(CASE WHEN tt.name IN ('Income','Transfer-In','Income Balance') THEN t.php ELSE 0 END) as credits,
        SUM(CASE WHEN tt.name IN ('Exp.','Transfer-Out','Expense Balance') THEN t.php ELSE 0 END) as debits,
        (a.balance_offset +
         SUM(CASE WHEN tt.name IN ('Income','Transfer-In','Income Balance') THEN t.php ELSE 0 END) -
         SUM(CASE WHEN tt.name IN ('Exp.','Transfer-Out','Expense Balance') THEN t.php ELSE 0 END)
        ) as balance
    FROM accounts a
    LEFT JOIN transactions t ON a.account_id = t.account_id
    LEFT JOIN transaction_types tt ON t.transaction_type_id = tt.transaction_type_id
    GROUP BY a.account_id ORDER BY a.sort_order
""")

# Unpaid credit card transactions
cursor.execute("""
    SELECT t.date, a.name as account, c.name as category, t.note, t.php
    FROM transactions t
    JOIN accounts a ON t.account_id = a.account_id
    JOIN categories c ON t.category_id = c.category_id
    WHERE t.payment_status = 'Unpaid' AND a.group_name = 'Card'
    ORDER BY t.date DESC
""")
```

## Account Reference

Full account list with current balances: `/app/custom-skills/finance-tracker/references/ACCOUNTS.md`

## Data Access Strategy

**Use the Finance API (`http://finance:9096`)** for standard queries — summaries, trends, category breakdowns, CRUD operations. Faster and simpler for common tasks.

**Use Direct SQLite (`/home/node/.openclaw/workspace/openclaw.db`)** for:
- Custom SQL queries not covered by the API
- Complex multi-table joins or aggregations
- When the user asks you to look at the raw data / database directly
- Any question about where finance data is stored

Both access the exact same database file. The API is a FastAPI wrapper around the same SQLite.

## API Reference

All calls use `exec` with `curl`. Base URL: `http://finance:9096`

### Summary (income/expenses/net for a month)
```bash
# Current month:
curl -s "http://finance:9096/api/summary"
# Specific month (YYYY-MM):
curl -s "http://finance:9096/api/summary?month=2026-03"
```
Returns: `income`, `expenses`, `net`, `transaction_count`, `all_time_income`, `all_time_expenses`, `all_time_net`

### Monthly Trend (last N months)
```bash
curl -s "http://finance:9096/api/monthly-trend?months=6"
```
Returns: array of `{month, income, expenses}` sorted oldest→newest

### Category Breakdown (spending or income by category)
```bash
# Top expense categories for a month:
curl -s "http://finance:9096/api/category-breakdown?month=2026-03&type=expense"
# Top income categories:
curl -s "http://finance:9096/api/category-breakdown?month=2026-03&type=income"
```
Returns: `data` array of `{category, total}` sorted by total desc

### Account Balances (all accounts + net worth)
```bash
curl -s "http://finance:9096/api/accounts"
```
Returns: `accounts` array (account, credits, debits, balance, txn_count) + `total_balance`

### List / Search Transactions
```bash
# Recent 10:
curl -s "http://finance:9096/api/transactions?per_page=10&sort=date_desc"
# Filter by account:
curl -s "http://finance:9096/api/transactions?account=Cash&per_page=20"
# Filter by type (Exp. / Income / Transfer-In / Transfer-Out):
curl -s "http://finance:9096/api/transactions?type=Income&per_page=20"
# Filter by category:
curl -s "http://finance:9096/api/transactions?category=%F0%9F%8D%9C%20Food&per_page=20"
# Filter by date range:
curl -s "http://finance:9096/api/transactions?date_from=2026-03-01&date_to=2026-03-31&per_page=100"
# Search by note/description:
curl -s "http://finance:9096/api/transactions?search=salary&per_page=10"
# Sort options: date_desc, date_asc, amount_desc, amount_asc
```
Returns: `{total, page, per_page, pages, items: [{id, date, time, account, category, note, type, amount, php, currency}]}`

### Get Single Transaction
```bash
curl -s "http://finance:9096/api/transactions/<id>"
```

### Add a Transaction
```bash
curl -s -X POST http://finance:9096/api/transactions \
  -H "Content-Type: application/json" \
  -d '{"date":"YYYY-MM-DD","time":"HH:MM:SS","account":"Cash","category":"🍜 Food","note":"Brief description","type":"Exp.","amount":250.00,"php":250.00,"currency":"PHP","expense_type":"Personal","payment_status":"Unpaid"}'
```
Valid types: `Exp.`, `Income`, `Transfer-In`, `Transfer-Out`, `Expense Balance`, `Income Balance`
Valid expense_types: `Personal`, `Family`, `Friends`, `Business`, `Work`, `Personal + Family`, `Personal + Friends`, `Friend`
Valid payment_statuses: `Paid`, `Unpaid`

**Credit card transactions**: When using a card account, always include `expense_type` and `payment_status`.

### Edit a Transaction
```bash
curl -s -X PUT http://finance:9096/api/transactions/<id> \
  -H "Content-Type: application/json" \
  -d '{"note":"Updated note","amount":300.00,"php":300.00}'
```
Only include fields you want to change.

### Delete a Transaction
```bash
curl -s -X DELETE http://finance:9096/api/transactions/<id>
```

### Credit Card Summary (per-card breakdown)
```bash
# Current month:
curl -s "http://finance:9096/api/credit-cards/summary"
# Specific month:
curl -s "http://finance:9096/api/credit-cards/summary?month=2026-03"
```
Returns: `cards` array with `{account, total_charged, total_paid, total_unpaid, personal_total, non_personal_total, txn_count}`, `totals`, `expense_breakdown`

### Credit Card Transactions (filtered list)
```bash
# All CC transactions for a month:
curl -s "http://finance:9096/api/credit-cards/transactions?month=2026-03&per_page=100"
# Filter by card:
curl -s "http://finance:9096/api/credit-cards/transactions?account=HSBC%20Visa%20Gold&month=2026-03"
# Filter by expense type:
curl -s "http://finance:9096/api/credit-cards/transactions?expense_type=Family&month=2026-03"
# Filter by payment status:
curl -s "http://finance:9096/api/credit-cards/transactions?payment_status=Unpaid"
```

### List Credit Cards
```bash
curl -s "http://finance:9096/api/credit-cards/cards"
```
Returns: array of `{name, icon, balance, credits, debits, txn_count}`

### Mark Transactions as Paid/Unpaid
```bash
curl -s -X POST http://finance:9096/api/credit-cards/mark-paid \
  -H "Content-Type: application/json" \
  -d '{"ids":[101,102,103],"payment_status":"Paid"}'
```

### Credit Card Monthly Trend
```bash
curl -s "http://finance:9096/api/credit-cards/monthly-trend?months=6"
```
Returns: monthly breakdown with `{month, total, personal, non_personal, paid, unpaid}`

### Available Accounts & Categories (for dropdowns)
```bash
curl -s "http://finance:9096/api/meta"
```

## Workflows

### "How much did I spend / earn this month?"
1. `GET /api/summary` (or with `?month=YYYY-MM` for past months)
2. Format response showing income, expenses, net savings, and savings rate %

### "What are my top expenses this month?"
1. `GET /api/category-breakdown?month=YYYY-MM&type=expense`
2. Show ranked list with amounts and % of total expenses

### "What's my net worth / account balances?"
1. `GET /api/accounts`
2. Show each account balance, highlight positive/negative, sum total

### "Show my recent transactions" / "What did I spend on X?"
1. `GET /api/transactions` with appropriate filters (`account`, `category`, `search`, `date_from`, `date_to`)
2. Display as a clean table with date, account, category, note, amount

### "Monthly trend / how has my spending changed?"
1. `GET /api/monthly-trend?months=6` (or 12)
2. Show month-by-month table with income, expenses, net, and trend

### "Credit card summary / how much do I owe?"
1. `GET /api/credit-cards/summary?month=YYYY-MM`
2. Show per-card breakdown: total charged, paid, unpaid, personal vs non-personal
3. Highlight unpaid amounts that need attention

### "What are my unpaid credit card transactions?"
1. `GET /api/credit-cards/transactions?payment_status=Unpaid&per_page=100`
2. Show list with card, date, note, amount, and expense type

### "How much of my credit card spending is personal?"
1. `GET /api/credit-cards/summary?month=YYYY-MM`
2. Compare personal_total vs non_personal_total from the totals
3. Show expense_breakdown for detailed per-type view

### "Add an expense / Log income / Record a transaction"
1. Parse details: date (default today), account, amount, category, note, type
2. If any required field is missing, ask ONE clarifying question
3. `POST /api/transactions` with the data
4. Confirm with a summary of what was added

### "Edit / Fix a transaction"
1. `GET /api/transactions?search=<note>&per_page=5` to find it
2. Show the matching transaction(s) and confirm which one
3. `PUT /api/transactions/<id>` with only the changed fields
4. Confirm the change

### "Delete a transaction"
1. Find transaction first (search by note/date/amount)
2. Confirm with user before deleting
3. `DELETE /api/transactions/<id>`
4. Confirm deletion

## Response Format

### Monthly Summary
```
💰 FINANCIAL SUMMARY — [Month YYYY]

📈 Income:    ₱[amount]
📉 Expenses:  ₱[amount]
💵 Net:       ₱[+/- amount] ([savings rate]%)

🏆 Top Expense Categories:
  1. [Category] — ₱[amount] ([%])
  2. [Category] — ₱[amount] ([%])
  3. [Category] — ₱[amount] ([%])

📊 All-time net worth: ₱[amount]
```

### Account Balances
```
🏦 ACCOUNT BALANCES

✅ [Account]    ₱[balance] (positive)
⚠️  [Account]    ₱[balance] (negative — outstanding balance)
...

💎 NET WORTH: ₱[total]
```

### Transaction Added/Edited
```
✅ Transaction [added/updated]:
📅 [Date]  |  [Account]  |  [Category]
📝 [Note]
💸 [Type]: ₱[amount]
```

## Notes

- **Negative balances** on credit card accounts = outstanding balance owed (normal)
- **Positive balances** on bank/cash accounts = available funds
- Transaction types: `Exp.` = expense, `Income` = income, `Transfer-In/Out` = between accounts, `Expense/Income Balance` = manual reconciliation entries
- Category emojis are part of the category name — include them exactly
- `php` field = amount converted to PHP (same as `amount` for PHP transactions)
- Always use today's date in PH timezone (UTC+8) when adding transactions without explicit dates
- The finance web UI is at `/finance/` for Paolo to see a visual view of the same data
````
