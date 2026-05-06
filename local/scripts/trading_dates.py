#!/usr/bin/env python3
"""交易日工具。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DAY_CALENDAR = ROOT_DIR / "data" / "cn_day_qlib" / "calendars" / "day.txt"


def load_trading_calendar(path: Path | None = None) -> list[str]:
    calendar_path = path or DEFAULT_DAY_CALENDAR
    return [line.strip() for line in calendar_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalize_date_str(date_str: str) -> str:
    text = str(date_str).strip()
    if not text:
        return text
    if " " in text:
        text = text.split(" ", 1)[0]
    if "T" in text:
        text = text.split("T", 1)[0]
    return text


def _next_weekday(date_str: str) -> str:
    current = datetime.strptime(normalize_date_str(date_str), "%Y-%m-%d").date()
    while True:
        current += timedelta(days=1)
        if current.weekday() < 5:
            return current.isoformat()


def next_trading_day(date_str: str, calendar: list[str]) -> tuple[str, str]:
    """返回下一个交易日和来源说明。"""
    normalized = normalize_date_str(date_str)
    future_dates = [value for value in calendar if value > normalized]
    if future_dates:
        return future_dates[0], "calendar"
    return _next_weekday(normalized), "weekday_fallback"
