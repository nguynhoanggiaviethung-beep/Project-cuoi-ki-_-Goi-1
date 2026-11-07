"""Single source of truth for Vietnamese equity-market session time."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import time as monotonic_time

from . import config

_override_started = monotonic_time.monotonic()
try:
    _override_base = datetime.fromisoformat(config.MARKET_CLOCK_OVERRIDE.replace("Z", "+00:00")) if config.MARKET_CLOCK_OVERRIDE else None
except ValueError:
    _override_base = None


def get_clock_now() -> datetime:
    """Return real time by default; an opt-in accelerated clock is for testing only."""
    if _override_base is None:
        return datetime.now(config.TIMEZONE)
    base = _override_base.replace(tzinfo=config.TIMEZONE) if _override_base.tzinfo is None else _override_base.astimezone(config.TIMEZONE)
    return base + timedelta(seconds=(monotonic_time.monotonic() - _override_started) * config.MARKET_CLOCK_SPEED)

SCHEDULES = config.MARKET_SESSION_SCHEDULES


def _local(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=config.TIMEZONE)
    return now.astimezone(config.TIMEZONE)


def _is_open(day: date, exchange: str, holidays=None, calendar=None) -> tuple[bool, str | None]:
    if calendar:
        day_rows=calendar.get(day.isoformat(),calendar)
        if exchange in day_rows:
            return bool(day_rows[exchange]), None if day_rows[exchange] else "holiday"
        if "ALL" in day_rows:
            return bool(day_rows["ALL"]), None if day_rows["ALL"] else "holiday"
    if day.weekday() >= 5:
        return False, "weekend"
    holiday_set = config.MARKET_HOLIDAYS if holidays is None else holidays
    if day.isoformat() in holiday_set:
        return False, "holiday"
    return True, None


def _next_open(day: date, exchange: str, holidays=None) -> datetime:
    candidate = day
    for _ in range(370):
        opened, _ = _is_open(candidate, exchange, holidays)
        if opened:
            return datetime.combine(candidate, time(9), tzinfo=config.TIMEZONE)
        candidate += timedelta(days=1)
    raise RuntimeError("No trading day found in the next year")


def get_market_state(now: datetime, exchange: str = "ALL", *, holidays=None,
                     last_completed_at: datetime | None = None, calendar=None) -> dict:
    """Return exchange/global session state for an injected clock instant.

    Holiday dates are ISO strings in config.MARKET_HOLIDAYS; explicit holidays
    can be supplied by tests/callers. `last_completed_at` anchors CLOSED data.
    """
    local = _local(now)
    exchanges = tuple(SCHEDULES) if exchange.upper() in {"ALL", "VN", "MARKET"} else (exchange.upper(),)
    unknown = [item for item in exchanges if item not in SCHEDULES]
    if unknown:
        raise ValueError(f"Unknown exchange: {unknown[0]}")

    states = []
    boundaries = []
    opened_exchanges = []
    for market in exchanges:
        opened, reason = _is_open(local.date(), market, holidays, calendar)
        if not opened:
            states.append((market, "CLOSED", None, reason))
            continue
        opened_exchanges.append(market)
        matched = None
        for start, end, state, phase in SCHEDULES[market]:
            start_dt = datetime.combine(local.date(), start, tzinfo=config.TIMEZONE)
            end_dt = datetime.combine(local.date(), end, tzinfo=config.TIMEZONE)
            boundaries.extend((start_dt, end_dt))
            if start_dt <= local < end_dt:
                matched = (market, state, phase, None)
        if matched:
            states.append(matched)
        else:
            close_at = max(datetime.combine(local.date(), item[1], tzinfo=config.TIMEZONE)
                           for item in SCHEDULES[market])
            reason = "before_open" if local < min(datetime.combine(local.date(), item[0], tzinfo=config.TIMEZONE)
                                                   for item in SCHEDULES[market]) else "after_close"
            states.append((market, "CLOSED", None, reason))

    active = [item for item in states if item[1] == "IN_SESSION"]
    lunches = [item for item in states if item[1] == "LUNCH_BREAK"]
    if active:
        state = "IN_SESSION"
        # Prioritize continuously matching exchanges, then auction phases.
        phase = next((item[2] for item in active if item[2] == "CONTINUOUS"), active[0][2])
        closed_reason = None
    elif lunches and len(lunches) == len(states):
        state, phase, closed_reason = "LUNCH_BREAK", None, None
    else:
        state, phase = "CLOSED", None
        reasons = {item[3] for item in states if item[3]}
        closed_reason = "weekend" if "weekend" in reasons else "holiday" if "holiday" in reasons else "before_open" if "before_open" in reasons else "after_close"

    future = sorted(point for point in set(boundaries) if point > local)
    def next_global_open(start_day):
        return min(_next_open(start_day, market, holidays) for market in exchanges)

    next_open = next_global_open(local.date())
    if state == "LUNCH_BREAK":
        next_open = datetime.combine(local.date(), time(13), tzinfo=config.TIMEZONE)
    elif state == "IN_SESSION":
        next_open = next_global_open(local.date() + timedelta(days=1))
    elif state == "CLOSED" and closed_reason in {"weekend", "holiday", "after_close"}:
        next_open = next_global_open(local.date() + timedelta(days=1))
    next_change = future[0] if future else next_open
    session_date = local.date().isoformat() if opened_exchanges else None
    effective_now = local
    if state == "LUNCH_BREAK":
        effective_now = datetime.combine(local.date(), time(11, 30), tzinfo=config.TIMEZONE)
    elif state == "CLOSED":
        effective_now = _local(last_completed_at) if last_completed_at is not None else None

    return {"state": state, "phase": phase, "session_date": session_date,
            "effective_now": effective_now.isoformat(timespec="seconds") if effective_now else None,
            "is_trading_day": bool(opened_exchanges), "closed_reason": closed_reason,
            "next_change_at": next_change.isoformat(timespec="seconds") if next_change else None,
            "next_open_at": next_open.isoformat(timespec="seconds"),
            "exchange_states": {m: {"state": s, "phase": p, "closed_reason": r} for m, s, p, r in states}}

