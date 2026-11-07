import hashlib
import json
import math
from datetime import date, datetime, time, timezone

from . import config
from .market_rules import is_on_tick, market_for, session_phase, tick_size

LEVELS = range(1, 4)


def number_or_none(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _source_time(item):
    for key in ("timestamp", "tradingTime", "sourceTimestamp", "updateTime", "time"):
        if item.get(key) not in (None, ""):
            value = str(item[key])
            if "T" not in value and len(value) <= 15:
                try:
                    clock = time.fromisoformat(value)
                    day = date.fromisoformat(str(item.get("dateString"))) if item.get("dateString") else datetime.now(config.TIMEZONE).date()
                    return datetime.combine(day, clock, tzinfo=config.TIMEZONE).isoformat()
                except ValueError:
                    pass
            return value
    return None


def map_message(payload):
    """Map the actual JSON shape without assuming a fixed nesting path."""
    mapped = []
    seen = set()
    for item in _walk_dicts(payload):
        if "symbol" not in item or not any(
            key in item for key in ("bidPrice1", "bidVol1", "askPrice1", "askVol1")
        ):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        received = datetime.now(config.TIMEZONE).isoformat(timespec="milliseconds")
        source_timestamp = _source_time(item)
        source_time_vn = _to_local_timestamp(source_timestamp)
        event_time = source_time_vn or received
        delay_seconds = None
        time_flags = []
        if source_timestamp and source_time_vn is None:
            time_flags.append("invalid_source_timestamp")
        elif source_time_vn:
            delay_seconds = (datetime.fromisoformat(received) - datetime.fromisoformat(source_time_vn)).total_seconds()
            if delay_seconds > config.MAX_EVENT_DELAY_SECONDS:
                time_flags.append("late_message")
            elif delay_seconds < -config.CLOCK_SKEW_TOLERANCE_SECONDS:
                time_flags.append("future_source_timestamp")
        market = market_for(symbol, item.get("market", item.get("exchange")))
        phase = session_phase(event_time, market)
        observation = {
            "symbol": symbol,
            "source_timestamp": source_timestamp,
            "source_ts_vn": source_time_vn,
            "source_delay_seconds": delay_seconds,
            "time_flags": time_flags,
            "received_at_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "received_at_vn": received,
            "market": market,
            "session_phase": phase,
            "ref_price": number_or_none(item.get("refPrice", item.get("referencePrice"))),
            "ceiling_price": number_or_none(item.get("ceilingPrice", item.get("ceiling"))),
            "floor_price": number_or_none(item.get("floorPrice", item.get("floor"))),
            "last_price": number_or_none(item.get("closePrice", item.get("lastPrice", item.get("matchPrice")))),
            "total_trading_value": number_or_none(item.get("totalTradingValue")),
            "total_trading_volume": number_or_none(item.get("totalTradingVolume")),
        }
        for level in LEVELS:
            observation[f"bid_price_{level}"] = number_or_none(item.get(f"bidPrice{level}"))
            observation[f"bid_volume_{level}"] = number_or_none(item.get(f"bidVol{level}"))
            observation[f"ask_price_{level}"] = number_or_none(item.get(f"askPrice{level}"))
            observation[f"ask_volume_{level}"] = number_or_none(item.get(f"askVol{level}"))

        validation = validate_observation(observation)
        observation["validation_status"] = validation["status"]
        observation["validation_reason"] = validation["reason"]
        observation["flags"] = validation.get("flags", [])
        observation["raw_payload"] = sanitize_payload(item)
        mapped.append(observation)
    return mapped


def validate_observation(observation):
    flags = list(observation.get("time_flags", []))
    if not observation.get("symbol"):
        return {"status": "invalid", "reason": "missing_symbol"}
    bid1 = observation.get("bid_price_1")
    ask1 = observation.get("ask_price_1")
    if bid1 is not None and ask1 is not None and bid1 > ask1:
        return {"status": "crossed_book", "reason": "bid1_greater_than_ask1", "flags": ["crossed_book"]}
    if bid1 is not None and ask1 is not None and bid1 == ask1:
        flags.append("locked")
    if bid1 is not None and bid1 <= 0:
        return {"status": "invalid", "reason": "bid1_not_positive"}
    if ask1 is not None and ask1 <= 0:
        return {"status": "invalid", "reason": "ask1_not_positive"}

    for level in LEVELS:
        for side in ("bid", "ask"):
            price = observation.get(f"{side}_price_{level}")
            volume = observation.get(f"{side}_volume_{level}")
            if price is not None and price <= 0:
                return {"status": "invalid", "reason": f"{side}{level}_price_not_positive", "flags": flags}
            if volume is not None and volume < 0:
                return {"status": "invalid", "reason": f"{side}{level}_volume_negative", "flags": flags}

    has_bid = any(
        observation.get(f"bid_price_{i}") is not None
        and observation.get(f"bid_volume_{i}") is not None
        and observation[f"bid_price_{i}"] > 0
        and observation[f"bid_volume_{i}"] >= 0
        for i in LEVELS
    )
    has_ask = any(
        observation.get(f"ask_price_{i}") is not None
        and observation.get(f"ask_volume_{i}") is not None
        and observation[f"ask_price_{i}"] > 0
        and observation[f"ask_volume_{i}"] >= 0
        for i in LEVELS
    )
    if observation.get("session_phase") in {"ATO", "ATC"}:
        flags.append("auction_phase")
    bid_missing = bid1 is None or not has_bid
    ask_missing = ask1 is None or not has_ask
    if (ask_missing and bid1 is not None and bid1 == observation.get("ceiling_price")) or (
        bid_missing and ask1 is not None and ask1 == observation.get("floor_price")
    ):
        flags.append("one_sided_limit")
    if (bid1 is not None and not is_on_tick(bid1, observation.get("market", "HOSE"))) or (
        ask1 is not None and not is_on_tick(ask1, observation.get("market", "HOSE"))
    ):
        flags.append("off_tick")
    if bid_missing or ask_missing:
        return {"status": "partial", "reason": "missing_best_quote_or_depth", "flags": flags}
    if flags and "locked" not in flags:
        status = "auction_phase" if "auction_phase" in flags else "valid"
        return {"status": status, "reason": ",".join(flags), "flags": flags}
    return {"status": "valid", "reason": "locked" if "locked" in flags else None, "flags": flags}


def calculate_metrics(observation):
    bid1 = observation.get("bid_price_1")
    ask1 = observation.get("ask_price_1")
    mid = spread = None
    if bid1 is not None and ask1 is not None and bid1 > 0 and ask1 > 0 and ask1 >= bid1:
        mid = (ask1 + bid1) / 2
        if mid > 0:
            spread = ((ask1 - bid1) / mid) * 10000

    side = {}
    for prefix in ("bid", "ask"):
        valid_levels = []
        for level in LEVELS:
            price = observation.get(f"{prefix}_price_{level}")
            volume = observation.get(f"{prefix}_volume_{level}")
            if price is not None and volume is not None and price > 0 and volume >= 0:
                valid_levels.append((price, volume))
        side[f"{prefix}_depth_qty"] = sum(volume for _, volume in valid_levels) if valid_levels else None
        side[f"{prefix}_depth_value"] = sum(price * volume for price, volume in valid_levels) if valid_levels else None
        side[f"{prefix}_levels"] = len(valid_levels)

    bid_qty, ask_qty = side["bid_depth_qty"], side["ask_depth_qty"]
    bid_value, ask_value = side["bid_depth_value"], side["ask_depth_value"]
    total_qty = bid_qty + ask_qty if bid_qty is not None and ask_qty is not None else None
    total_value = bid_value + ask_value if bid_value is not None and ask_value is not None else None
    reference = observation.get("mid_price") or mid
    market = observation.get("market", "HOSE")
    tick = tick_size(reference, market) if reference is not None else None
    spread_abs = (ask1 - bid1) if bid1 is not None and ask1 is not None else None
    return {
        "mid_price": mid,
        "spread_abs": spread_abs,
        "spread_bps": spread,
        "spread_ticks": spread_abs / tick if spread_abs is not None and tick else None,
        "tick_size": tick,
        "bid_depth_qty": bid_qty,
        "ask_depth_qty": ask_qty,
        "total_depth_qty": total_qty,
        "bid_depth_value": bid_value,
        "ask_depth_value": ask_value,
        "total_depth_value": total_value,
        "queue_imbalance": ((bid_qty - ask_qty) / total_qty) if total_qty and total_qty > 0 else None,
        "value_imbalance": ((bid_value - ask_value) / total_value) if total_value and total_value > 0 else None,
        "depth_levels_used": min(side["bid_levels"], side["ask_levels"]),
        "calculated_at": datetime.now(config.TIMEZONE).isoformat(timespec="milliseconds"),
    }


def sanitize_payload(value):
    blocked = {"token", "access_token", "refresh_token", "password", "authorization", "api_key", "apikey"}
    if isinstance(value, dict):
        return {key: sanitize_payload(child) for key, child in value.items() if str(key).lower() not in blocked}
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    return value


def payload_hash(payload):
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _to_local_timestamp(value):
    if not value:
        return None
    text = str(value).strip()
    try:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
            for pattern in ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue
            if parsed is None:
                raise ValueError("unsupported timestamp format")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=config.TIMEZONE)
        return parsed.astimezone(config.TIMEZONE).isoformat(timespec="milliseconds")
    except ValueError:
        try:
            numeric = float(value)
            if numeric > 10_000_000_000:
                numeric /= 1000
            return datetime.fromtimestamp(numeric, config.TIMEZONE).isoformat(timespec="milliseconds")
        except (ValueError, TypeError, OSError, OverflowError):
            return None


def quantile(values, percentile):
    ordered = sorted(float(value) for value in values if value is not None)
    if not ordered:
        return None
    position = (len(ordered) - 1) * percentile / 100
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight
