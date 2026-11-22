"""Static, non-executing walk through the visible top-three order book."""
from math import floor

from .analytics import calculate_metrics
from .market_rules import round_lot
from . import config


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
    for level in range(1, config.ORDER_COST_LEVELS + 1):
        price = observation.get(("ask" if side == "buy" else "bid") + f"_price_{level}")
        volume = observation.get(("ask" if side == "buy" else "bid") + f"_volume_{level}")
        if price is not None and volume is not None and price > 0 and volume > 0:
            levels.append((float(price), float(volume)))
    if not levels:
        return {"status": "unavailable", "reason": "visible book side has no valid levels"}
    visible_depth_value = sum(price * floor(volume / lot_size) * lot_size for price, volume in levels)
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
    # A notional budget is converted to board lots. Cash left below the cost of
    # the next 100-share lot is normal rounding residue, not missing depth.
    notional_rounding_residue = (notional is not None and remaining_value is not None
                                 and remaining_value < levels[-1][0] * lot_size)
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
        "status": "full" if fill_ratio >= 1 or notional_rounding_residue else "partial_exceeds_visible_depth",
        "side": side, "requested_quantity": requested_qty, "requested_notional": notional,
        "filled_quantity": int(total_qty), "filled_value": total_value,
        "fill_ratio": fill_ratio, "vwap": vwap, "mid_price": mid,
        "slippage_bps": slippage, "levels_reached": len(fills), "levels_used": len(fills),
        "visible_depth_value": visible_depth_value, "fills": fills,
        "q_max_within_slippage": qmax, "max_slippage_bps": max_slippage_bps,
        "lot_size": lot_size,
        "disclaimer": "Static estimate on this snapshot; excludes hidden liquidity, cancellations, queue priority and market reaction. No order is sent.",
    }


def _normalize_levels(levels, side, max_levels=None, lot=100):
    """Return valid, ordered (price, lot-rounded volume) levels for one side."""
    if side not in {"buy", "sell"}:
        raise ValueError("side must be 'buy' or 'sell'")
    count=max_levels or config.ORDER_COST_LEVELS
    normalized=[]
    for item in list(levels or [])[:max(1,int(count))]:
        if isinstance(item, dict):
            price=item.get("price",item.get("level_price"));volume=item.get("volume",item.get("quantity",item.get("shares")))
        else:
            try: price,volume=item[0],item[1]
            except (TypeError,IndexError): continue
        try: price=float(price);volume=floor(float(volume)/lot)*lot
        except (TypeError,ValueError,OverflowError): continue
        if price>0 and volume>=lot: normalized.append((price,volume))
    normalized.sort(key=lambda row:row[0],reverse=side=="sell")
    return normalized


def book_walk_by_value(levels, order_value_vnd, mid, side, lot=100, max_levels=None):
    """Walk visible SSI levels for a cash-sized order without extrapolation."""
    side=str(side or "").lower()
    if side not in {"buy","sell"}: raise ValueError("side must be 'buy' or 'sell'")
    if isinstance(order_value_vnd,bool) or not isinstance(order_value_vnd,(int,float)) or order_value_vnd<=0:
        raise ValueError("order_value_vnd must be positive")
    if isinstance(lot,bool) or int(lot)<1: raise ValueError("lot must be positive")
    lot=int(lot)
    try: mid=float(mid)
    except (TypeError,ValueError): mid=0
    if mid<=0:return {"status":"unavailable","reason":"valid two-sided snapshot required","filled_value":0,"vwap":None,"slippage_bps":None,"extra_cost_vnd":None,"fill_ratio":0,"levels_used":0,"levels_touched":0,"visible_depth_value":0}
    usable=_normalize_levels(levels,side,max_levels,lot)
    visible=sum(price*volume for price,volume in usable)
    if not usable:return {"status":"unavailable","reason":"visible book side has no valid levels","filled_value":0,"vwap":None,"slippage_bps":None,"extra_cost_vnd":None,"fill_ratio":0,"levels_used":0,"levels_touched":0,"visible_depth_value":0}
    requested=float(order_value_vnd);remaining=requested;filled_qty=filled_value=0.0;touched=0
    for price,available in usable:
        take=min(int(available),floor(max(0.0,remaining)/price/lot)*lot)
        if take<lot:continue
        value=take*price;filled_qty+=take;filled_value+=value;remaining=max(0.0,requested-filled_value);touched+=1
        if remaining<price*lot:break
    if filled_qty<=0:return {"status":"unavailable","reason":"order value is below one visible board lot","filled_value":0,"vwap":None,"slippage_bps":None,"extra_cost_vnd":None,"fill_ratio":0,"levels_used":0,"levels_touched":0,"visible_depth_value":visible}
    vwap=filled_value/filled_qty
    slippage=((vwap-mid) if side=="buy" else (mid-vwap))/mid*10000
    fill_ratio=min(1.0,filled_value/requested)
    remainder= max(0.0,requested-filled_value)
    one_lot_value=usable[min(touched,len(usable))-1][0]*lot
    is_full=filled_value>=requested*.99 or remainder<one_lot_value
    return {"status":"full" if is_full else "partial_exceeds_visible_depth","filled_value":filled_value,"filled_quantity":int(filled_qty),"vwap":vwap,"mid":mid,"slippage_bps":slippage,"extra_cost_vnd":filled_value*slippage/10000,"fill_ratio":fill_ratio,"levels_used":touched,"levels_touched":touched,"visible_depth_value":visible,"requested_value_vnd":requested,"side":side,"lot":lot}


def max_order_within_threshold(levels, mid, side, threshold_bps, lot=100, max_levels=None):
    """Solve the largest whole-lot order within the slippage budget by price level."""
    side=str(side or "").lower()
    if side not in {"buy","sell"}: raise ValueError("side must be 'buy' or 'sell'")
    if threshold_bps is None or float(threshold_bps)<0: raise ValueError("threshold_bps must be non-negative")
    try: mid=float(mid)
    except (TypeError,ValueError): mid=0
    usable=_normalize_levels(levels,side,max_levels,lot)
    if mid<=0 or not usable:return {"value":0,"shares":0,"full_depth":False,"zero":True,"visible_depth_value":0,"levels_used":0}
    threshold=float(threshold_bps)
    cap=mid*(1+threshold/10000) if side=="buy" else mid*(1-threshold/10000)
    quantity=value=0.0;used=0;exhausted=True
    for price,available in usable:
        take=int(available)
        if quantity>0:
            if side=="buy" and price>cap:
                allowed=(cap*quantity-value)/(price-cap)
                take=min(take,max(0,floor(allowed/lot+1e-9)*lot))
            elif side=="sell" and price<cap:
                allowed=(value-cap*quantity)/(cap-price)
                take=min(take,max(0,floor(allowed/lot+1e-9)*lot))
        elif (side=="buy" and price>cap) or (side=="sell" and price<cap):
            take=0
        if take<=0:
            exhausted=False
            break
        quantity+=take;value+=price*take;used+=1
        if take<int(available):
            exhausted=False
            break
    zero=quantity<=0
    slippage=((value/quantity-mid) if side=="buy" else (mid-value/quantity))/mid*10000 if quantity else None
    return {"value":value,"shares":int(quantity),"full_depth":bool(exhausted and quantity>0),"zero":zero,"visible_depth_value":sum(price*volume for price,volume in usable),"levels_used":used,"slippage_bps":slippage}
