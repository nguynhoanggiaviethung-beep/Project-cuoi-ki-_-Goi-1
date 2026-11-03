"""Vietnam equity market conventions used by SLI.

Hours/tick schedules are configuration defaults from the project specification;
confirm them with the relevant exchange before relying on production results.
"""
from datetime import datetime, time

from . import config

CONTINUOUS_WINDOWS = {
    "HOSE": ((time(9, 15), time(11, 30), "CONTINUOUS_AM"), (time(13, 0), time(14, 30), "CONTINUOUS_PM")),
    "HNX": ((time(9, 0), time(11, 30), "CONTINUOUS_AM"), (time(13, 0), time(14, 30), "CONTINUOUS_PM")),
    "UPCOM": ((time(9, 0), time(11, 30), "CONTINUOUS_AM"), (time(13, 0), time(15, 0), "CONTINUOUS_PM")),
}


def market_for(symbol, explicit=None):
    return (explicit or config.SYMBOL_MARKETS.get(str(symbol).upper(), "HOSE")).upper()


def tick_size(price, market="HOSE"):
    if price is None or price <= 0:
        return None
    market = market.upper()
    if market == "HOSE":
        if price < 10_000:
            return 10.0
        if price <= 49_950:
            return 50.0
        if price >= 50_000:
            return 100.0
        return None
    if market in {"HNX", "UPCOM"}:
        return 100.0
    return None


def session_phase(value, market="HOSE"):
    """Classify local market time; holidays are handled by session_calendar."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "UNKNOWN"
    if value.tzinfo is None:
        value = value.replace(tzinfo=config.TIMEZONE)
    local = value.astimezone(config.TIMEZONE).time().replace(tzinfo=None)
    market = market.upper()
    if market == "HOSE" and time(9, 0) <= local < time(9, 15):
        return "ATO"
    windows = CONTINUOUS_WINDOWS.get(market, CONTINUOUS_WINDOWS["HOSE"])
    for start, end, phase in windows:
        if start <= local < end:
            return phase
    if time(11, 30) <= local < time(13, 0):
        return "BREAK"
    if market in {"HOSE", "HNX"} and time(14, 30) <= local <= time(14, 45):
        return "ATC"
    if time(9, 0) <= local <= time(15, 0):
        return "CLOSED"
    return "CLOSED"


def baseline_bucket(value, market="HOSE"):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=config.TIMEZONE)
    local = value.astimezone(config.TIMEZONE).time().replace(tzinfo=None)
    market = market.upper()
    windows = CONTINUOUS_WINDOWS.get(market, CONTINUOUS_WINDOWS["HOSE"])
    open_start = windows[0][0]
    close_end = windows[-1][1]
    open_start_minute = open_start.hour * 60 + open_start.minute
    open_end_minute = open_start_minute + 45
    open_end = time(open_end_minute // 60, open_end_minute % 60)
    if open_start <= local < open_end:
        return "OPEN"
    close_start_minute = close_end.hour * 60 + close_end.minute - 30
    close_start = time(close_start_minute // 60, close_start_minute % 60)
    if close_start <= local < close_end:
        return "CLOSE"
    if any(start <= local < end for start, end, _ in windows):
        return "MID"
    return None


def is_on_tick(price, market="HOSE"):
    tick = tick_size(price, market)
    if tick is None:
        return False
    return abs(price / tick - round(price / tick)) < 1e-7


def round_lot(quantity, lot_size=100):
    return max(0, int(quantity) // lot_size * lot_size)
