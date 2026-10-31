import hashlib
import json
import math
from datetime import datetime, timezone

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
    for key in ("timestamp", "tradingTime", "sourceTimestamp"):
        if item.get(key) not in (None, ""):
            return str(item[key])
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
        observation = {
            "symbol": symbol,
            "source_timestamp": _source_time(item),
            "received_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        for level in LEVELS:
            observation[f"bid_price_{level}"] = number_or_none(item.get(f"bidPrice{level}"))
            observation[f"bid_volume_{level}"] = number_or_none(item.get(f"bidVol{level}"))
            observation[f"ask_price_{level}"] = number_or_none(item.get(f"askPrice{level}"))
            observation[f"ask_volume_{level}"] = number_or_none(item.get(f"askVol{level}"))

        validation = validate_observation(observation)
        observation["validation_status"] = validation["status"]
        observation["validation_reason"] = validation["reason"]
        observation["raw_payload"] = sanitize_payload(payload)
        mapped.append(observation)
    return mapped


def validate_observation(observation):
    if not observation.get("symbol"):
        return {"status": "invalid", "reason": "missing_symbol"}
    bid1 = observation.get("bid_price_1")
    ask1 = observation.get("ask_price_1")
    if bid1 is not None and ask1 is not None and bid1 > ask1:
        return {"status": "crossed_book", "reason": "bid1_greater_than_ask1"}
    if bid1 is not None and bid1 <= 0:
        return {"status": "invalid", "reason": "bid1_not_positive"}
    if ask1 is not None and ask1 <= 0:
        return {"status": "invalid", "reason": "ask1_not_positive"}

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
    if bid1 is None or ask1 is None or not has_bid or not has_ask:
        return {"status": "partial", "reason": "missing_best_quote_or_depth"}
    return {"status": "valid", "reason": None}


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
    return {
        "mid_price": mid,
        "spread_bps": spread,
        "bid_depth_qty": bid_qty,
        "ask_depth_qty": ask_qty,
        "total_depth_qty": total_qty,
        "bid_depth_value": bid_value,
        "ask_depth_value": ask_value,
        "total_depth_value": total_value,
        "queue_imbalance": ((bid_qty - ask_qty) / total_qty) if total_qty and total_qty > 0 else None,
        "value_imbalance": ((bid_value - ask_value) / total_value) if total_value and total_value > 0 else None,
        "depth_levels_used": min(side["bid_levels"], side["ask_levels"]),
        "calculated_at": datetime.now(timezone.utc).isoformat(),
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