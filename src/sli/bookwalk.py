"""Static, non-executing walk through the visible top-three order book."""
from math import floor

from .analytics import calculate_metrics
from .market_rules import round_lot


def estimate(observation, side, quantity=None, notional=None, max_slippage_bps=None, lot_size=100):
    side = str(side).lower()
    if side not in {"buy", "sell"}:
        raise ValueError("side must be 'buy' or 'sell'")
    if (quantity is None) == (notional is None):
        raise ValueError("provide exactly one of quantity or notional")
    metrics = calculate_metrics(observation)
    mid = metrics["mid_price"]
    if mid is None:
        return {"status": "unavailable", "reason": "valid two-sided snapshot required"}
    levels = []
    for level in range(1, 4):
        price = observation.get(("ask" if side == "buy" else "bid") + f"_price_{level}")
        volume = observation.get(("ask" if side == "buy" else "bid") + f"_volume_{level}")
        if price is not None and volume is not None and price > 0 and volume > 0:
            levels.append((float(price), float(volume)))
    if not levels:
        return {"status": "unavailable", "reason": "visible book side has no valid levels"}
    requested_qty = round_lot(quantity, lot_size) if quantity is not None else None
    if quantity is not None and requested_qty <= 0:
        raise ValueError("quantity must be at least one board lot")
    remaining_qty = requested_qty
    remaining_value = float(notional) if notional is not None else None
    fills = []
    total_qty = total_value = 0.0
    for price, available in levels:
        if remaining_qty is not None:
            fill_qty = min(remaining_qty, floor(available / lot_size) * lot_size)
        else:
            fill_qty = min(floor(available / lot_size) * lot_size, floor(remaining_value / price / lot_size) * lot_size)
        if fill_qty <= 0:
            continue
        value = fill_qty * price
        fills.append({"level_price": price, "quantity": fill_qty, "value": value})
        total_qty += fill_qty
        total_value += value
        if remaining_qty is not None:
            remaining_qty -= fill_qty
            if remaining_qty <= 0:
                break
        else:
            remaining_value -= value
            if remaining_value <= 0:
                break
    if not total_qty:
        return {"status": "unavailable", "reason": "order below one lot at visible prices"}
    requested = requested_qty if requested_qty is not None else float(notional)
    vwap = total_value / total_qty
    slippage = ((vwap - mid) if side == "buy" else (mid - vwap)) / mid * 10000
    fill_ratio = min(1.0, total_qty / requested) if requested_qty is not None else min(1.0, total_value / requested)
    qmax = None
    if max_slippage_bps is not None:
        qmax = 0
        cap = mid * (1 + max_slippage_bps / 10000) if side == "buy" else mid * (1 - max_slippage_bps / 10000)
        q_acc = value_acc = 0.0
        for price, volume in levels:
            lots = floor(volume / lot_size) * lot_size
            if lots <= 0:
                continue
            candidate_q = q_acc + lots
            candidate_value = value_acc + price * lots
            avg = candidate_value / candidate_q
            within = avg <= cap if side == "buy" else avg >= cap
            if within:
                qmax = int(candidate_q)
                q_acc, value_acc = candidate_q, candidate_value
                continue
            if (price > cap and side == "buy") or (price < cap and side == "sell"):
                allowed = 0 if q_acc == 0 else (cap * q_acc - value_acc) / (price - cap)
                qmax += max(0, floor(allowed / lot_size) * lot_size)
                break
    return {
        "status": "full" if fill_ratio >= 1 else "partial_exceeds_visible_depth",
        "side": side, "requested_quantity": requested_qty, "requested_notional": notional,
        "filled_quantity": int(total_qty), "filled_value": total_value,
        "fill_ratio": fill_ratio, "vwap": vwap, "mid_price": mid,
        "slippage_bps": slippage, "levels_reached": len(fills), "fills": fills,
        "q_max_within_slippage": qmax, "max_slippage_bps": max_slippage_bps,
        "lot_size": lot_size,
        "disclaimer": "Static estimate on this snapshot; excludes hidden liquidity, cancellations, queue priority and market reaction. No order is sent.",
    }
