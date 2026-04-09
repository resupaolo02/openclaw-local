````skill
---
name: calendar-assistant
description: Use when the user wants to manage their calendar, schedule events, set reminders, view their weekly schedule, or check what they have coming up. Also handles Sunday weekly digest generation and remaining-week summaries. Triggers on: "calendar", "schedule", "reminder", "add event", "what do I have", "this week", "next week", "weekly digest", "upcoming", "appointment", "meeting", "block time", "reschedule", "cancel event", "what's on my", "show my schedule", "week ahead".
version: 2.0.0
metadata: { "openclaw": { "emoji": "📅" } }
---

# Calendar Assistant

Manages Paolo's **Google Calendar** (primary: `resupaolo@gmail.com`). Reads and writes real events via the Calendar microservice API. All calendar operations go through `http://hub:8000/calendar` — do NOT attempt direct Google API calls.

## Core Context

- **User:** Paolo (UTC+8, Asia/Manila)
- **Calendar provider:** Google Calendar
- **Primary Calendar ID:** `resupaolo@gmail.com`
- **Calendar service base URL:** `http://hub:8000/calendar` (internal Docker network)
- Dates → YYYY-MM-DD | Times → HH:MM 24h | Timezone → UTC+8

## API Reference (use `exec` with `curl`)

### List upcoming events
```bash
curl -s http://hub:8000/calendar/api/calendar/events
```
Returns: `{"events": [...], "count": N}`
Each event: `id`, `title`, `date`, `time`, `end_time`, `all_day`, `description`, `location`, `html_link`

### Get weekly digest
```bash
# Remaining week (today → Sunday)
curl -s http://hub:8000/calendar/api/calendar/week

# Next week (Mon → Sun)
curl -s http://hub:8000/calendar/api/calendar/week?mode=next
```
Returns: `{"digest": "...formatted text...", "events": [...], ...}`

### Create an event
```bash
curl -s -X POST http://hub:8000/calendar/api/calendar/events \
  -H "Content-Type: application/json" \
  -d '{"title":"TITLE","date":"YYYY-MM-DD","time":"HH:MM","end_time":"HH:MM","location":"...","description":"...","reminder_minutes":30,"all_day":false}'
```

### Delete an event
```bash
curl -s -X DELETE "http://hub:8000/calendar/api/calendar/events/<google-event-id>"
```

## Workflow

### Creating an event or reminder

1. Parse natural language → title, date (YYYY-MM-DD), time (HH:MM 24h), end time, location, description
2. Clarify ambiguity with ONE question if needed
3. Run the create `curl` via `exec`
4. Parse the JSON response and confirm:
   ```
   ✅ Added to your Google Calendar:
   📌 [Title]
   📅 [DayName, DD MMM YYYY] at [HH:MM–HH:MM]
   📍 [Location if set]
   🔔 Reminder [X] min before
   ```

### Viewing the weekly schedule

1. Choose mode:
   - **Any manual trigger** → `mode=remaining` (today → coming Sunday)
   - **Sunday heartbeat** → `mode=next` (next Mon → Sun)
2. Call `GET /api/calendar/week?mode=...` via `exec`
3. Return the `digest` field from the response — it's already nicely formatted

### Listing upcoming events

1. Call `GET /api/calendar/events` via `exec`
2. Display events grouped by date, sorted by time
3. Covers next 60 days by default

### Editing an event

1. List events to find the exact event ID
2. Delete old: `DELETE /api/calendar/events/<id>`
3. Recreate with updated details
4. Confirm what changed

### Cancelling an event

1. List events to confirm which one (ask if ambiguous)
2. `DELETE /api/calendar/events/<google-event-id>`
3. Confirm deletion

## Response Format

### Weekly Digest
Return the `digest` string from the API as-is — already formatted:
```
📅 REMAINING WEEK — 05 Apr to 06 Apr 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SUNDAY, 05 APR
  • 10:00–11:00  Team Sync @ Zoom

MONDAY, 06 APR
  ✨ Nothing scheduled

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 1 event total
```

## Heartbeat Integration (Sunday)

When triggered on **Sunday** as part of heartbeat:
1. Call `POST http://hub:8000/calendar/api/calendar/week/trigger`
   - This fetches next week's events from Google Calendar and caches the digest
2. Parse the `digest` from the response
3. Optionally message Paolo via Telegram with the digest
4. Update `heartbeat-state.json` → set `calendar-weekly-digest` timestamp

Trigger command:
```bash
curl -s -X POST http://hub:8000/calendar/api/calendar/week/trigger
```

## Notes

- Always interpret dates in **UTC+8 (Asia/Manila)**
- Google event IDs are long opaque strings — use them exactly as returned
- All-day events: set `"all_day": true` and omit `time`/`end_time`
- If Calendar service returns 503 → Google token may have expired → tell Paolo to re-run `/home/resupaolo/authorize_google_calendar.py` on the host
- Recurring events are expanded by Google Calendar automatically (`singleEvents=true` is used)
````
