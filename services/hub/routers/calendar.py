"""Calendar router — Google Calendar integration."""

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger("calendar")

WORKSPACE          = Path(os.getenv("WORKSPACE_DIR", "/workspace"))
CALENDAR_DATA_FILE = WORKSPACE / "calendar-data.json"
GCAL_TOKEN_FILE    = WORKSPACE / "google-token.json"

PH_TZ = datetime.timezone(datetime.timedelta(hours=8))

_DAYS   = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
_MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

# ── Google Calendar setup ────────────────────────────────────────────────────

try:
    from google.oauth2.credentials import Credentials as GCredentials
    from google.auth.transport.requests import Request as GRequest
    from googleapiclient.discovery import build as _gcal_build
    _GCAL_AVAILABLE = True
except ImportError:
    _GCAL_AVAILABLE = False
    logger.warning("Google Calendar libraries not installed — calendar features disabled")

_GCAL_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _read_cal_config() -> dict:
    default: dict[str, Any] = {
        "calendar_id":   "resupaolo@gmail.com",
        "weekly_digest": {"generated_at": None, "week_start": None,
                          "week_end": None, "content": ""},
    }
    if not CALENDAR_DATA_FILE.exists():
        CALENDAR_DATA_FILE.write_text(json.dumps(default, indent=2))
        return default
    try:
        return json.loads(CALENDAR_DATA_FILE.read_text())
    except Exception:
        return default


def _write_cal_config(data: dict) -> None:
    CALENDAR_DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _get_gcal_service():
    if not _GCAL_AVAILABLE:
        raise HTTPException(status_code=503,
            detail="Google Calendar libraries not installed in container")
    if not GCAL_TOKEN_FILE.exists():
        raise HTTPException(status_code=503,
            detail="Google Calendar not configured — token file missing")
    creds = GCredentials.from_authorized_user_file(str(GCAL_TOKEN_FILE), _GCAL_SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(GRequest())
                GCAL_TOKEN_FILE.write_text(creds.to_json())
            except Exception as exc:
                raise HTTPException(status_code=503,
                    detail=f"Token refresh failed: {exc}") from exc
        else:
            raise HTTPException(status_code=503,
                detail="Google Calendar token invalid — re-run the authorize script")
    return _gcal_build("calendar", "v3", credentials=creds, cache_discovery=False)


def _gcal_to_internal(ev: dict) -> dict:
    start = ev.get("start", {})
    end   = ev.get("end",   {})
    all_day      = "dateTime" not in start
    date_str     = (start.get("dateTime") or start.get("date") or "")[:10]
    time_str     = start.get("dateTime", "")[11:16]
    end_time_str = end.get("dateTime",   "")[11:16]
    return {
        "id":          ev.get("id", ""),
        "title":       ev.get("summary") or "(No title)",
        "date":        date_str,
        "time":        time_str,
        "end_time":    end_time_str,
        "all_day":     all_day,
        "description": ev.get("description", ""),
        "location":    ev.get("location",    ""),
        "html_link":   ev.get("htmlLink",    ""),
    }


def _fmt_day_header(d: datetime.date) -> str:
    return f"{_DAYS[d.weekday()].upper()}, {d.day:02d} {_MONTHS[d.month - 1].upper()}"


def _build_digest(events: list[dict], start: datetime.date, end: datetime.date,
                  label: str) -> str:
    by_date: dict[str, list[dict]] = {}
    for ev in events:
        try:
            ev_date = datetime.date.fromisoformat(ev["date"])
        except Exception:
            continue
        if start <= ev_date <= end:
            by_date.setdefault(ev["date"], []).append(ev)

    s = f"{start.day:02d} {_MONTHS[start.month-1]} {start.year}"
    e = f"{end.day:02d}   {_MONTHS[end.month-1]}   {end.year}"
    lines = [f"📅 {label} — {s} to {e}", "━" * 38, ""]
    total = 0
    cur = start
    while cur <= end:
        lines.append(_fmt_day_header(cur))
        day_evs = sorted(by_date.get(cur.isoformat(), []),
                         key=lambda ev: ev.get("time", ""))
        if day_evs:
            for ev in day_evs:
                t, et    = ev.get("time", ""), ev.get("end_time", "")
                time_str = ("All day" if ev.get("all_day")
                            else (f"{t}–{et}" if et else (t or "?")))
                loc      = f" @ {ev['location']}" if ev.get("location") else ""
                note     = f" ({ev['description']})" if ev.get("description") else ""
                lines.append(f"  • {time_str}  {ev['title']}{loc}{note}")
            total += len(day_evs)
        else:
            lines.append("  ✨ Nothing scheduled")
        lines.append("")
        cur += datetime.timedelta(days=1)
    lines += ["━" * 38, f"📊 {total} event{'s' if total != 1 else ''} total"]
    return "\n".join(lines)


def _compute_week_range(mode: str) -> tuple[datetime.date, datetime.date, str]:
    today = datetime.datetime.now(PH_TZ).date()
    if mode == "next":
        days = (7 - today.weekday()) % 7 or 7
        start = today + datetime.timedelta(days=days)
        end   = start + datetime.timedelta(days=6)
        return start, end, "WEEK AHEAD"
    days_sun = (6 - today.weekday()) % 7
    start    = today
    end      = today + datetime.timedelta(days=days_sun)
    label    = "REMAINING WEEK" if today.weekday() > 0 else "THIS WEEK"
    return start, end, label


def _fetch_gcal_events(service, cal_id: str,
                       start: datetime.date, end: datetime.date) -> list[dict]:
    time_min = datetime.datetime.combine(
        start, datetime.time.min, tzinfo=PH_TZ).isoformat()
    time_max = datetime.datetime.combine(
        end,   datetime.time.max, tzinfo=PH_TZ).isoformat()
    result = service.events().list(
        calendarId=cal_id, timeMin=time_min, timeMax=time_max,
        singleEvents=True, orderBy="startTime", maxResults=250,
    ).execute()
    return [_gcal_to_internal(e) for e in result.get("items", [])]


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/api/calendar/week")
async def calendar_week(mode: str = "remaining"):
    import asyncio
    loop = asyncio.get_running_loop()

    def _compute():
        cfg    = _read_cal_config()
        cal_id = cfg.get("calendar_id", "resupaolo@gmail.com")
        start, end, label = _compute_week_range(mode)
        wd = cfg.get("weekly_digest", {})
        if (mode == "next"
                and wd.get("week_start") == start.isoformat()
                and wd.get("week_end")   == end.isoformat()
                and wd.get("content")):
            return {"mode": "next", "week_start": wd["week_start"],
                    "week_end": wd["week_end"], "generated_at": wd.get("generated_at"),
                    "digest": wd["content"], "events": wd.get("events", [])}
        service = _get_gcal_service()
        events  = _fetch_gcal_events(service, cal_id, start, end)
        digest  = _build_digest(events, start, end, label)
        now_iso = datetime.datetime.now(PH_TZ).isoformat()
        if mode == "next":
            cfg["weekly_digest"] = {"generated_at": now_iso,
                                    "week_start": start.isoformat(),
                                    "week_end": end.isoformat(), "content": digest,
                                    "events": events}
            _write_cal_config(cfg)
        return {"mode": mode, "week_start": start.isoformat(),
                "week_end": end.isoformat(), "generated_at": now_iso,
                "digest": digest, "events": events}

    return await loop.run_in_executor(None, _compute)


@router.post("/api/calendar/week/trigger")
async def calendar_week_trigger():
    import asyncio
    loop = asyncio.get_running_loop()

    def _compute():
        cfg    = _read_cal_config()
        cal_id = cfg.get("calendar_id", "resupaolo@gmail.com")
        today  = datetime.datetime.now(PH_TZ).date()
        days_sun = (6 - today.weekday()) % 7
        start  = today
        end    = today + datetime.timedelta(days=days_sun)
        label  = "REMAINING WEEK" if today.weekday() > 0 else "THIS WEEK"
        service = _get_gcal_service()
        events  = _fetch_gcal_events(service, cal_id, start, end)
        digest  = _build_digest(events, start, end, label)
        now_iso = datetime.datetime.now(PH_TZ).isoformat()
        cfg["weekly_digest"] = {"generated_at": now_iso,
                                "week_start": start.isoformat(),
                                "week_end": end.isoformat(), "content": digest}
        _write_cal_config(cfg)
        return {"triggered": True, "week_start": start.isoformat(),
                "week_end": end.isoformat(), "generated_at": now_iso,
                "digest": digest, "events": events}

    return await loop.run_in_executor(None, _compute)


@router.get("/api/calendar/events")
async def calendar_events_list(days: int = 60):
    import asyncio
    loop = asyncio.get_running_loop()

    def _get():
        cfg    = _read_cal_config()
        cal_id = cfg.get("calendar_id", "resupaolo@gmail.com")
        today  = datetime.datetime.now(PH_TZ).date()
        end    = today + datetime.timedelta(days=days)
        service = _get_gcal_service()
        events  = _fetch_gcal_events(service, cal_id, today, end)
        return {"events": events, "count": len(events), "calendar_id": cal_id}

    return await loop.run_in_executor(None, _get)


class CalendarEventCreate(BaseModel):
    title: str
    date: str
    time: str = "09:00"
    end_time: str = "10:00"
    description: str = ""
    location: str = ""
    reminder_minutes: int = 30
    all_day: bool = False


@router.post("/api/calendar/events", status_code=201)
async def calendar_events_create(ev: CalendarEventCreate):
    import asyncio
    loop = asyncio.get_running_loop()

    def _create():
        cfg    = _read_cal_config()
        cal_id = cfg.get("calendar_id", "resupaolo@gmail.com")
        service = _get_gcal_service()
        if ev.all_day or not ev.time:
            body: dict[str, Any] = {
                "summary": ev.title, "start": {"date": ev.date},
                "end": {"date": ev.date}, "description": ev.description,
                "location": ev.location,
            }
        else:
            tz_offset = "+08:00"
            end_t = ev.end_time or ev.time
            body = {
                "summary": ev.title,
                "start": {"dateTime": f"{ev.date}T{ev.time}:00{tz_offset}", "timeZone": "Asia/Manila"},
                "end":   {"dateTime": f"{ev.date}T{end_t}:00{tz_offset}",   "timeZone": "Asia/Manila"},
                "description": ev.description, "location": ev.location,
                "reminders": {"useDefault": False, "overrides": [
                    {"method": "popup", "minutes": ev.reminder_minutes}]},
            }
        created = service.events().insert(calendarId=cal_id, body=body).execute()
        return _gcal_to_internal(created)

    return await loop.run_in_executor(None, _create)


@router.delete("/api/calendar/events/{event_id:path}")
async def calendar_events_delete(event_id: str):
    import asyncio
    loop = asyncio.get_running_loop()

    def _delete():
        cfg    = _read_cal_config()
        cal_id = cfg.get("calendar_id", "resupaolo@gmail.com")
        service = _get_gcal_service()
        try:
            service.events().delete(calendarId=cal_id, eventId=event_id).execute()
        except Exception as exc:
            raise HTTPException(status_code=404,
                detail=f"Could not delete event: {exc}") from exc
        return {"deleted": True, "id": event_id}

    return await loop.run_in_executor(None, _delete)


@router.get("/api/health", include_in_schema=False)
async def health():
    return {"status": "ok"}
