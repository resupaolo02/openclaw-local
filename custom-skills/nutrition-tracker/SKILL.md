````skill
---
name: nutrition-tracker
description: Use when the user asks about calories, macros, nutrition, diet, food intake, protein, carbs, fat, fiber, calorie count, what they ate, daily food log, nutrition goals, weight loss, bulking, cutting, meal tracking, or wants to log/add/edit/delete a food entry. Also use when the user asks to search for food nutrition info, look up a food, scan a barcode, or asks "how many calories in [food]". This skill has LIVE access to Paolo's personal nutrition database via the Nutrition microservice. Triggers on: "calories", "macros", "nutrition", "protein", "carbs", "fat", "fiber", "what did I eat", "food log", "log food", "add food", "calorie tracker", "how much protein", "daily intake", "nutrition summary", "diet", "meal", "breakfast", "lunch", "dinner", "snack", "calorie goal", "macro goal", "calorie deficit", "calorie surplus", "MyFitnessPal", "nutrition status", "how am I doing on calories", "log a meal", "track food", "search food", "nutrition info", "how many calories in", "adobo calories", "Jollibee", "Andok's", "Mang Inasal", "Filipino food".
version: 2.0.0
metadata: { "openclaw": { "emoji": "🥗" } }
---

# Nutrition Tracker

Gives Paolo live read/write access to his personal calorie & macro tracker via the Nutrition microservice.

## Core Context

- **Service base URL:** `http://nutrition:9097` (internal Docker network)
- **Timezone:** UTC+8 (PH). Always use today's PH date when no date is specified.
- **Meal types:** `breakfast`, `lunch`, `dinner`, `snack`
- **Web UI:** `/nutrition/` for manual editing
- **Default daily goals:** 2000 kcal · 150g protein · 200g carbs · 65g fat · 25g fiber (user-configurable)

## Food Database

The service has a built-in food database (`food_database` table) with **3 data sources**:

| source | Description |
|---|---|
| `seeded` | ~130 pre-loaded Philippine dishes & fast-food chains (always available offline) |
| `openfoodfacts` | Global branded/packaged foods fetched from Open Food Facts (no API key, cached locally) |
| `usda` | Generic/raw ingredients from USDA FoodData Central (cached locally) |
| `custom` | Paolo's own custom foods |

**Philippine brands pre-loaded:** Jollibee, Andok's, Mang Inasal, Chowking, Max's Restaurant, Greenwich, Ministop, 7-Eleven PH.
**Philippine dishes pre-loaded:** Adobo, Sinigang, Kare-Kare, Lechon, Tinola, Sisig, Bulalo, Pancit, all Silog meals, Kakanin, street food, desserts, beverages, and more.

> When Paolo asks about any food, **always search the database first** (`/api/foods/search`) before asking him to provide nutrition values manually.

## API Reference

All calls use `exec` with `curl`. Base URL: `http://nutrition:9097`

### Food Database — Search
```bash
# Search food database (local + Open Food Facts + USDA)
curl -s "http://nutrition:9097/api/foods/search?q=chicken+adobo&limit=10"
# Search only local/seeded data (offline, faster)
curl -s "http://nutrition:9097/api/foods/search?q=sinigang&limit=10&source=local"
# Filter by source: seeded | openfoodfacts | usda | custom
curl -s "http://nutrition:9097/api/foods/search?q=jollibee&limit=15&source=seeded"
```
Returns: `{query, total, items: [{id, source, food_name, brand, serving_size, serving_g, calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g, sodium_mg, tags}]}`

### Food Database — Barcode Lookup
```bash
curl -s "http://nutrition:9097/api/foods/barcode/4800016012345"
```
Looks up by EAN/UPC barcode via Open Food Facts. Cached locally after first lookup.

### Food Database — Browse / CRUD
```bash
# List all foods in database
curl -s "http://nutrition:9097/api/foods?per_page=50"
# List only seeded PH foods
curl -s "http://nutrition:9097/api/foods?source=seeded&per_page=100"
# Get specific food by id
curl -s "http://nutrition:9097/api/foods/42"
# Add a custom food
curl -s -X POST http://nutrition:9097/api/foods \
  -H "Content-Type: application/json" \
  -d '{"food_name":"Bicol Express","serving_size":"100g","serving_g":100,"calories":220,"protein_g":14,"carbs_g":8,"fat_g":15,"tags":"Filipino,Bicolano,coconut milk,pork"}'
# Update a food entry
curl -s -X PUT http://nutrition:9097/api/foods/42 \
  -H "Content-Type: application/json" \
  -d '{"calories":250,"protein_g":15}'
# Delete a food entry
curl -s -X DELETE http://nutrition:9097/api/foods/42
```

### Quick Log (from Food Database)
```bash
# Log 1 standard serving of food id=5 for lunch
curl -s -X POST http://nutrition:9097/api/log/quick \
  -H "Content-Type: application/json" \
  -d '{"food_id":5,"meal_type":"lunch"}'
# Log 1.5 servings
curl -s -X POST http://nutrition:9097/api/log/quick \
  -H "Content-Type: application/json" \
  -d '{"food_id":5,"meal_type":"lunch","servings":1.5}'
# Log by grams (auto-scales nutrition)
curl -s -X POST http://nutrition:9097/api/log/quick \
  -H "Content-Type: application/json" \
  -d '{"food_id":5,"meal_type":"lunch","grams":250}'
# With a specific date
curl -s -X POST http://nutrition:9097/api/log/quick \
  -H "Content-Type: application/json" \
  -d '{"food_id":5,"meal_type":"dinner","date":"2026-04-06","servings":1}'
```

### Daily Summary
```bash
curl -s "http://nutrition:9097/api/summary"
curl -s "http://nutrition:9097/api/summary?day=2026-04-05"
```

### Weekly Trend
```bash
curl -s "http://nutrition:9097/api/weekly-trend?weeks=4"
```

### List / Search Food Log
```bash
curl -s "http://nutrition:9097/api/log?date=2026-04-06&per_page=100"
curl -s "http://nutrition:9097/api/log?date=2026-04-06&meal_type=lunch"
curl -s "http://nutrition:9097/api/log?date_from=2026-04-01&date_to=2026-04-06"
curl -s "http://nutrition:9097/api/log?search=chicken&per_page=20"
```

### Manual Log Entry
```bash
curl -s -X POST http://nutrition:9097/api/log \
  -H "Content-Type: application/json" \
  -d '{"date":"2026-04-06","time":"12:30:00","meal_type":"lunch","food_name":"Chicken Breast","serving_size":"150g","calories":247,"protein_g":46,"carbs_g":0,"fat_g":5.4}'
```

### Edit / Delete Log Entry
```bash
curl -s -X PUT  http://nutrition:9097/api/log/<id> -H "Content-Type: application/json" -d '{"calories":300}'
curl -s -X DELETE http://nutrition:9097/api/log/<id>
```

### Goals
```bash
curl -s "http://nutrition:9097/api/goals"
curl -s -X PUT http://nutrition:9097/api/goals \
  -H "Content-Type: application/json" \
  -d '{"calories":2000,"protein_g":150,"carbs_g":200,"fat_g":65,"fiber_g":25}'
```

## Workflows

### "How many calories in [food]?" / "Nutrition info for [food]"
1. `GET /api/foods/search?q=<food>&limit=10`
2. If a clear match is found, show the nutrition info per serving from the result.
3. If multiple matches, list the top 3 with serving size and ask Paolo which one.
4. If not found locally, the API automatically queries Open Food Facts + USDA.

### "Log/add [food]"
**Always search the database first — do not ask Paolo for calories manually.**
1. `GET /api/foods/search?q=<food>&limit=10`
2. If found: use the best match → `POST /api/log/quick` with `food_id` + `servings` or `grams`
3. If not found: ask Paolo for calories and macros, then `POST /api/log` manually
4. Confirm with: food logged, serving size, kcal + macros, updated daily total

### "What's my nutrition status?" / "How am I doing today?"
1. `GET /api/summary`
2. Display using the Nutrition Status format below

### "What did I eat today/for [meal]?"
1. `GET /api/log?date=YYYY-MM-DD` (or add `&meal_type=<type>`)
2. List entries grouped by meal with macros

### "Weekly summary / trend"
1. `GET /api/weekly-trend?weeks=2`
2. Show daily calorie table, avg vs goal, highlight over/under days

### "Update my goals"
1. `PUT /api/goals` with new values
2. Confirm changes

### "Add a custom food to my database"
1. `POST /api/foods` with full nutrition data
2. Confirm food was saved with its assigned id

## Response Format

### Nutrition Status
```
🥗 NUTRITION — [Date]

🔥 Calories:  [consumed] / [goal] kcal ([pct]%)  [▓▓▓░░░░░░░]
💪 Protein:   [consumed]g / [goal]g ([pct]%)
🍞 Carbs:     [consumed]g / [goal]g ([pct]%)
🧈 Fat:       [consumed]g / [goal]g ([pct]%)
🌿 Fiber:     [consumed]g / [goal]g ([pct]%)

🍽 By Meal:
  🌅 Breakfast: [kcal] kcal
  ☀️  Lunch:     [kcal] kcal
  🌙 Dinner:    [kcal] kcal
  🍎 Snacks:    [kcal] kcal

[Remaining or over summary]
```

### Food Logged
```
✅ Logged: [food_name] ([serving_size])
🍽 [Meal] · 📅 [Date]
🔥 [calories] kcal  |  💪 [protein]g P  |  🍞 [carbs]g C  |  🧈 [fat]g F
📊 Daily total: [total] / [goal] kcal ([pct]%)
```

### Food Search Result (single item)
```
🔍 [food_name] ([brand if any])
📏 Serving: [serving_size]
🔥 [calories] kcal  |  💪 [protein_g]g P  |  🍞 [carbs_g]g C  |  🧈 [fat_g]g F
🌿 Fiber: [fiber_g]g  |  🧂 Sodium: [sodium_mg]mg
📂 Source: [source]
```

## Notes

- Always use today's PH date (UTC+8) when no date is given
- Philippine dishes are pre-loaded — never ask Paolo to provide calorie data for common Filipino food
- Search is case-insensitive and partial-match; `q=adobo` will find all adobo variants
- `remaining` values are negative when over goal — display as "X over"
- Progress bars: `▓` for filled, `░` for empty (10 chars = 100%): `round(pct/10)` blocks
- Meal types are lowercase: `breakfast`, `lunch`, `dinner`, `snack`
- When logging by grams, the service auto-scales all macros based on `serving_g` in the database
- External API results (Open Food Facts, USDA) are cached locally — repeat searches are instant
- USDA uses `DEMO_KEY` by default (30 req/hr/IP). For heavy use, set `USDA_API_KEY` env var in docker-compose.yml
````
