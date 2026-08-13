from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def now_local() -> datetime:
    return datetime.now(SHANGHAI).replace(microsecond=0)


def to_db(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI).isoformat(timespec="seconds")


def from_db(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def weekly_window(reference: datetime | None = None, *, end_weekday: int = 4, end_hour: int = 18) -> dict[str, object]:
    ref = reference or now_local()
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=SHANGHAI)
    monday = (ref - timedelta(days=ref.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end_at = monday + timedelta(days=end_weekday, hours=end_hour)
    if ref < monday or (ref.weekday() >= end_weekday and ref >= end_at + timedelta(days=2)):
        monday += timedelta(days=7)
        end_at += timedelta(days=7)
    period_key = f"week:{monday.strftime('%Y%m%d')}"
    return {
        "periodKey": period_key,
        "startAt": monday,
        "endAt": end_at,
        "label": f"{monday.strftime('%Y-%m-%d')} 至 {end_at.strftime('%Y-%m-%d %H:%M')}",
    }

