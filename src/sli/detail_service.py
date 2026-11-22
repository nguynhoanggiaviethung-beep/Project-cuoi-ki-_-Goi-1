"""Single-clock detail-page payload; finance calculations stay in this service."""
from datetime import datetime, timedelta
from statistics import median

from . import config, database
from .analytics import percentile_rank, quantile
from .market_rules import session_phase
from .ranking import is_rankable_stock



def _saved_depth_medians(rows):
    """Aggregate already-calculated metrics; never derives depth from raw book fields."""
    usable = []
    excluded = {"one_sided_limit", "auction_phase", "off_tick", "crossed_book",
                "late_message", "future_source_timestamp", "invalid_source_timestamp"}
    import json
    for row in rows:
        try:
            flags = set(json.loads(row.get("flags") or "[]"))
        except (TypeError, ValueError):
            flags = set()
        if row.get("validation_status") != "valid" or flags.intersection(excluded):
            continue
        if row.get("bid_depth_value") is not None and row.get("ask_depth_value") is not None:
            usable.append(row)
    if not usable:
        return None, None
    return (median(float(row["bid_depth_value"]) for row in usable),
            median(float(row["ask_depth_value"]) for row in usable))


def backfill_saved_depth_bars(source="ssi"):
    """Backfill new bar fields from persisted per-snapshot metrics, outside request handling."""
    import json
    excluded = {"one_sided_limit", "auction_phase", "off_tick", "crossed_book",
                "late_message", "future_source_timestamp", "invalid_source_timestamp"}
    with database.connect() as db:
        missing = db.execute("SELECT symbol,minute_vn FROM bars_1m WHERE source=? AND (bid_depth_value IS NULL OR ask_depth_value IS NULL)",
                             (source,)).fetchall()
        keys = {(row["symbol"], row["minute_vn"][:16]) for row in missing}
        if not keys:
            return 0
        first_minute = min(key[1] for key in keys)
        last_minute = max(key[1] for key in keys)
        cursor = db.execute("""SELECT o.symbol,o.received_at_vn,o.validation_status,o.flags,
                     m.bid_depth_value,m.ask_depth_value
            FROM observations o JOIN metrics m ON m.observation_id=o.id
            WHERE o.source=? AND o.received_at_vn>=? AND o.received_at_vn<?
              AND o.validation_status='valid' AND m.bid_depth_value IS NOT NULL AND m.ask_depth_value IS NOT NULL
            ORDER BY o.received_at_vn""", (source, first_minute, last_minute + "~"))
        grouped = {}
        while True:
            batch = cursor.fetchmany(10000)
            if not batch:
                break
            for row in batch:
                received = row["received_at_vn"] or ""
                key = (row["symbol"], received[:16])
                if key not in keys:
                    continue
                try:
                    flags = set(json.loads(row["flags"] or "[]"))
                except (TypeError, ValueError):
                    flags = set()
                if flags.intersection(excluded):
                    continue
                grouped.setdefault(key, []).append(dict(row))
        updates = []
        for symbol, minute in missing:
            bid, ask = _saved_depth_medians(grouped.get((symbol, minute[:16]), []))
            if bid is not None and ask is not None:
                updates.append((bid, ask, source, symbol, minute))
        db.executemany("UPDATE bars_1m SET bid_depth_value=?,ask_depth_value=? WHERE source=? AND symbol=? AND minute_vn=?", updates)
    return len(updates)


def _stamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=config.TIMEZONE)
        return parsed.astimezone(config.TIMEZONE)
    except (TypeError, ValueError):
        return None


def _independent(fn):
    try:
        return {"status": "ready", "data": fn(), "message": None}
    except Exception:
        return {"status": "unavailable", "data": None, "message": "Khối dữ liệu hiện chưa khả dụng."}


def build_detail(symbol, source="ssi", as_of=None, window_sessions=5, from_date=None, to_date=None, watchlist_mode=False):
    """Build all detail widgets from persisted metrics/bars using one snapshot clock."""
    as_of = as_of or datetime.now(config.TIMEZONE)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=config.TIMEZONE)
    as_of = as_of.astimezone(config.TIMEZONE)
    ticker = str(symbol).strip().upper()
    metadata = database.fetch_company_metadata("ssi", ticker)
    if not metadata or not is_rankable_stock(ticker, metadata.get("stock_type")):
        return {"symbol": ticker, "source": source, "as_of": as_of.isoformat(timespec="seconds"),
                "error": "Mã cổ phiếu không có trong danh mục."}
    market = metadata.get("board") or "HOSE"
    phase = session_phase(as_of, market)
    quote_load = _independent(lambda: list(reversed(database.fetch_observations(source, symbol=ticker, limit=2000))))
    rows = quote_load["data"] or []
    valid = [row for row in rows if row.get("validation_status") == "valid"]
    latest = valid[-1] if valid else None
    latest_at = _stamp((latest or {}).get("source_ts_vn") or (latest or {}).get("received_at_vn") or (latest or {}).get("received_at_utc"))
    age = max(0, (as_of - latest_at).total_seconds()) if latest_at else None
    today_rows = [row for row in valid if _stamp(row.get("received_at_vn") or row.get("received_at_utc"))
                  and _stamp(row.get("received_at_vn") or row.get("received_at_utc")).date() == as_of.date()]
    live_phase = phase in {"CONTINUOUS_AM", "CONTINUOUS_PM"}
    one_sided = bool(latest) and any(latest.get(f"{side}_price_1") is None or latest.get(f"{side}_volume_1") is None
                                     for side in ("bid", "ask"))
    if live_phase and latest and today_rows and age is not None and age <= config.STALE_AFTER_SECONDS:
        state = "quote"
    elif live_phase and today_rows:
        state = "stale"
    elif live_phase:
        state = "no_quote"
    elif latest:
        state = "outside_session"
    else:
        state = "no_quote"
    if one_sided and state in {"quote", "stale", "outside_session"}:
        state = "one_sided"

    # The watchlist needs only recent closes; the detail page keeps full history.
    # Avoid reading unrelated symbols' alert rows once per watchlist row.
    history_limit = 25 if watchlist_mode else 1000
    history_load = _independent(lambda: database.fetch_historical_prices(ticker, limit=history_limit, source="ssi"))
    alert_load = _independent(lambda: [item for item in database.fetch_alerts(source, limit=500, symbol=ticker)
                                      if item.get("status") == "active"])
    bar_start=f"{from_date}T00:00:00" if from_date else None
    bar_end=f"{to_date}T23:59:59" if to_date else None
    bars_load = _independent(lambda: database.fetch_bars(source, symbol=ticker, limit=50000,
                                                         start_at=bar_start,end_at=bar_end))
    price_rows = history_load["data"] or []
    bars = bars_load["data"] or []
    # Keep completed bars only; timestamps and window boundaries share the page clock.
    current_minute = as_of.replace(second=0, microsecond=0)
    bars = [bar for bar in bars if (stamp := _stamp(bar.get("minute_vn"))) and stamp < current_minute]
    sessions = sorted({str(bar.get("session_date")) for bar in bars if bar.get("session_date")}, reverse=True)
    if from_date or to_date:
        selected_sessions={day for day in sessions if (not from_date or day>=from_date) and (not to_date or day<=to_date)}
    else:
        selected_sessions = set(sessions[:max(1, min(int(window_sessions), 20))])
    window_bars = [bar for bar in bars if str(bar.get("session_date")) in selected_sessions]
    chart_bars = [bar for bar in window_bars if bar.get("coverage_status") == "ok"]
    current_bucket = None
    if chart_bars:
        current_bucket = chart_bars[-1].get("baseline_bucket")
    if from_date:
        baseline_sessions=set([day for day in sessions if day<from_date][:config.BASELINE_SESSIONS])
    else:
        baseline_sessions = set(sessions[max(1, min(int(window_sessions), 20)):][:config.BASELINE_SESSIONS])
    baseline_candidates = [bar for bar in bars if str(bar.get("session_date")) in baseline_sessions
                           and bar.get("coverage_status") == "ok"
                           and (not market or bar.get("market") == market)
                           and bar.get("spread_bps") is not None
                           and bar.get("total_depth_value") is not None]

    def qualifies(rows):
        days = {str(row.get("session_date")) for row in rows if row.get("session_date")}
        return (len(days), len(rows))

    same_phase_rows = [bar for bar in baseline_candidates
                       if current_bucket is not None and bar.get("baseline_bucket") == current_bucket]
    same_phase_sessions, same_phase_bars = qualifies(same_phase_rows)
    min_sessions = int(getattr(config, "MIN_BASELINE_SESSIONS", config.BASELINE_SESSIONS))
    min_bars = int(config.BASELINE_MIN_BARS)
    if same_phase_sessions >= min_sessions and same_phase_bars >= min_bars:
        baseline, baseline_mode = same_phase_rows, "same_phase"
    else:
        pooled_rows = [bar for bar in baseline_candidates if bar.get("baseline_bucket")]
        pooled_sessions, pooled_bars = qualifies(pooled_rows)
        if pooled_sessions >= min_sessions and pooled_bars >= min_bars:
            baseline, baseline_mode = pooled_rows, "pooled"
        else:
            baseline, baseline_mode = [], "insufficient"
    baseline_session_count, baseline_bar_count = qualifies(baseline)
    spread_values = sorted(float(b["spread_bps"]) for b in baseline)
    depth_values = sorted(float(b["total_depth_value"]) for b in baseline)
    baseline_eligible = bool(baseline)
    def percentile(values, p):
        return quantile(values,p*100)
    spread = (latest or {}).get("spread_bps")
    spread_percentile = percentile_rank(spread_values,spread)
    depth = (latest or {}).get("total_depth_value")
    depth_percentile = percentile_rank(depth_values,depth)
    active_alerts = alert_load["data"] or []
    latest_price = price_rows[-1] if price_rows else None
    prior_price = price_rows[-2] if len(price_rows) > 1 else None
    active_dates = {str(row.get("trading_date")) for row in price_rows if row.get("volume") and float(row["volume"]) > 0}
    recent_dates = sorted(active_dates, reverse=True)[:5]
    last_trade_date = max(active_dates) if active_dates else None
    try:
        days_since_trade = (as_of.date() - datetime.fromisoformat(last_trade_date).date()).days if last_trade_date else None
    except ValueError:
        days_since_trade = None

    # The persisted quote contract currently contains at most three levels.
    # Build a cumulative curve only for HNX/UPCoM and report the observed count;
    # never synthesize levels 4–10 when SSI did not provide/store them.
    depth_curve = {"bid": [], "ask": [], "levels_used": 0, "as_of": None}
    if market in {"HNX", "UPCOM"} and latest:
        bid_levels = [(float(latest[f"bid_price_{i}"]), float(latest[f"bid_volume_{i}"]))
                      for i in range(1, 11) if latest.get(f"bid_price_{i}") is not None and latest.get(f"bid_volume_{i}") is not None
                      and float(latest[f"bid_price_{i}"]) > 0 and float(latest[f"bid_volume_{i}"]) > 0]
        ask_levels = [(float(latest[f"ask_price_{i}"]), float(latest[f"ask_volume_{i}"]))
                      for i in range(1, 11) if latest.get(f"ask_price_{i}") is not None and latest.get(f"ask_volume_{i}") is not None
                      and float(latest[f"ask_price_{i}"]) > 0 and float(latest[f"ask_volume_{i}"]) > 0]
        bid_levels.sort(key=lambda item: item[0], reverse=True)
        ask_levels.sort(key=lambda item: item[0])
        cumulative = 0.0
        for price, volume in bid_levels:
            cumulative += volume
            depth_curve["bid"].append({"price": price, "cumulative_volume": cumulative})
        depth_curve["bid"].reverse()
        cumulative = 0.0
        for price, volume in ask_levels:
            cumulative += volume
            depth_curve["ask"].append({"price": price, "cumulative_volume": cumulative})
        depth_curve["levels_used"] = max(len(bid_levels), len(ask_levels))
        depth_curve["as_of"] = ((latest or {}).get("source_ts_vn") or (latest or {}).get("received_at_vn"))

    result = {
        "symbol": ticker, "source": source, "board": market, "company_name": metadata.get("name_vi"),
        "as_of": as_of.isoformat(timespec="seconds"), "market_phase": phase, "state": state,
        "quote_age_seconds": round(age, 1) if age is not None else None,
        "quote_time": ((latest or {}).get("source_ts_vn") or (latest or {}).get("received_at_vn") or (latest or {}).get("received_at_utc")),
        "depth_curve": depth_curve,
        "snapshot_count": len(rows), "valid_snapshot_count": len(valid),
        "baseline_eligible": baseline_eligible,
        "baseline_session_count": baseline_session_count,
        "baseline_mode": baseline_mode,
        "baseline_sessions_have": baseline_session_count if baseline_eligible else max(same_phase_sessions, pooled_sessions if 'pooled_sessions' in locals() else 0),
        "baseline_sessions_need": min_sessions,
        "baseline_bars_have": baseline_bar_count if baseline_eligible else max(same_phase_bars, pooled_bars if 'pooled_bars' in locals() else 0),
        "baseline_bars_need": min_bars,
        "widgets": {},
    }
    result["widgets"]["company"] = _independent(lambda: {"name": metadata.get("name_vi"), "board": market})
    result["widgets"]["orderbook"] = _independent(lambda: latest if latest else None)
    result["widgets"]["kpis"] = _independent(lambda: {
        "spread_bps": spread, "spread_ticks": (latest or {}).get("spread_ticks"), "tick_size": (latest or {}).get("tick_size"), "spread_percentile": spread_percentile if baseline_eligible else None,
        "spread_baseline_count": len(spread_values), "bid_depth_value": (latest or {}).get("bid_depth_value"),
        "ask_depth_value": (latest or {}).get("ask_depth_value"),
        "depth_percentile": depth_percentile if baseline_eligible else None,
        "depth_baseline_count": len(depth_values), "baseline_session_count": baseline_session_count, "queue_imbalance": (latest or {}).get("queue_imbalance"),
        "value_imbalance": (latest or {}).get("value_imbalance"), "active_alert_count": len(active_alerts),
    })
    result["widgets"]["charts"] = _independent(lambda: {
        "window_sessions": max(1, min(int(window_sessions), 20)), "session_dates": sorted(selected_sessions),
        "spread": [{"minute": b.get("minute_vn"), "value": b.get("spread_bps"), "spread_bps": b.get("spread_bps"), "baseline_p25": percentile(spread_values, .25) if baseline_eligible else None,
                    "baseline_p90": percentile(spread_values, .90) if baseline_eligible else None, "sample_count": b.get("n_snapshots"), "symbol": ticker} for b in chart_bars],
        "depth": [{"minute": b.get("minute_vn"), "bid": b.get("bid_depth_value"), "ask": b.get("ask_depth_value"), "bid_depth_value": b.get("bid_depth_value"),
                   "ask_depth_value": b.get("ask_depth_value"), "symbol": ticker} for b in chart_bars],
        "depth_curve": depth_curve, "levels_used": depth_curve["levels_used"],
        "baseline_sample_count": len(baseline), "baseline_eligible": baseline_eligible,
        "baseline_session_count": baseline_session_count,
        "baseline_mode": baseline_mode,
        "baseline_sessions_have": result["baseline_sessions_have"],
        "baseline_sessions_need": min_sessions,
        "baseline_bars_have": result["baseline_bars_have"],
        "baseline_bars_need": min_bars,
        "spread_baseline_p25": percentile(spread_values, .25) if baseline_eligible else None,
        "spread_baseline_p90": percentile(spread_values, .90) if baseline_eligible else None,
    })
    result["widgets"]["alerts"] = _independent(lambda: active_alerts)
    result["widgets"]["history"] = _independent(lambda: {
        "rows": price_rows, "latest": latest_price, "prior": prior_price,
        "sessions_with_trades_in_last_5": len(recent_dates), "last_trade_date": last_trade_date,
        "days_since_last_trade": days_since_trade,
    })
    # Surface storage failures within only the widgets that depend on that store.
    if quote_load["status"] != "ready":
        result["widgets"]["orderbook"] = quote_load
        result["widgets"]["kpis"] = quote_load
    if bars_load["status"] != "ready":
        result["widgets"]["charts"] = bars_load
    if history_load["status"] != "ready":
        result["widgets"]["history"] = history_load
    if alert_load["status"] != "ready":
        result["widgets"]["alerts"] = alert_load
    return result
