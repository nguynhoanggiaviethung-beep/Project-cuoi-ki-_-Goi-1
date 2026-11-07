"""Shared, persisted-bar service for the overview's two-sided market depth."""
from datetime import datetime, timedelta
from statistics import median

from . import config, database
from .market_rules import CONTINUOUS_WINDOWS
from .ranking import is_rankable_stock


def build_market_depth(window_minutes=60, source="ssi", market_meta=None):
    """Return a balanced eligible-stock panel from completed 1m bars only.

    This service aggregates already-computed bid/ask top-three notional values;
    it deliberately does not derive financial metrics from raw snapshots.
    """
    window_minutes = max(5, min(int(window_minutes), 390))
    market_meta = market_meta or {}
    requested_date = market_meta.get("session_date")
    state = market_meta.get("state")
    with database.connect() as db:
        if requested_date:
            dates = db.execute("SELECT DISTINCT session_date FROM bars_1m WHERE source=? AND session_date<=? ORDER BY session_date DESC LIMIT 10",
                               (source, requested_date)).fetchall()
        else:
            dates = db.execute("SELECT DISTINCT session_date FROM bars_1m WHERE source=? ORDER BY session_date DESC LIMIT 10", (source,)).fetchall()
        session_date = next((str(row[0]) for row in dates), None)
        if not session_date:
            return {"source": source, "session_date": None, "rows": [], "n0": 0, "n1": 0, "n2": 0,
                    "n3": 0, "empty": True, "message": "Chưa có bar sổ lệnh đã lưu để vẽ độ sâu."}
        rows = db.execute("""SELECT symbol,minute_vn,market,session_phase,baseline_bucket,
                    bid_depth_value,ask_depth_value,n_snapshots,coverage_status
                FROM bars_1m WHERE source=? AND session_date=? AND coverage_status='ok'
                  AND bid_depth_value IS NOT NULL AND ask_depth_value IS NOT NULL
                ORDER BY minute_vn""", (source, session_date)).fetchall()
    bars = [dict(row) for row in rows]
    # A market-clock cut-off prevents drawing bars from later in the day when
    # the app clock is simulated or the collector has already persisted ahead.
    effective = market_meta.get("effective_now")
    if effective and state in {"IN_SESSION", "LUNCH_BREAK"}:
        bars = [row for row in bars if row["minute_vn"] < str(effective)[:16]]
    by_symbol = {}
    for row in bars:
        # Auction buckets are not valid continuous-session depth observations.
        if row.get("baseline_bucket") not in {"OPEN", "MID", "CLOSE"}:
            continue
        by_symbol.setdefault(row["symbol"], []).append(row)
    all_minutes = sorted({row["minute_vn"] for group in by_symbol.values() for row in group})
    if not all_minutes:
        return {"source": source, "session_date": session_date, "rows": [], "n0": len(by_symbol), "n1": 0,
                "n2": 0, "n3": 0, "empty": True, "message": "Chưa có bar phút liên tục hợp lệ trong phiên gần nhất."}
    latest_minute = datetime.fromisoformat(all_minutes[-1])
    effective_stamp = datetime.fromisoformat(str(effective)) if effective else None
    if state in {"IN_SESSION", "LUNCH_BREAK"} and effective_stamp:
        end = effective_stamp.replace(second=0, microsecond=0)
        fresh = (end - latest_minute).total_seconds() <= 60 and end.date() == latest_minute.date()
        if not fresh:
            end = latest_minute.replace(second=0, microsecond=0) + timedelta(minutes=1)
    else:
        end = latest_minute.replace(second=0, microsecond=0) + timedelta(minutes=1)
    start = end - timedelta(minutes=window_minutes)
    selected_minutes = {minute for minute in all_minutes if start <= datetime.fromisoformat(minute) < end}
    if not selected_minutes:
        return {"source": source, "session_date": session_date, "rows": [], "n0": len(by_symbol), "n1": 0,
                "n2": 0, "n3": 0, "empty": True, "message": "Chưa có bar phút hoàn tất trong cửa sổ thời gian đã chọn."}
    selected = {symbol: [row for row in group if row["minute_vn"] in selected_minutes]
                for symbol, group in by_symbol.items()}
    selected = {symbol: group for symbol, group in selected.items() if group}
    metadata = database.fetch_security_metadata("ssi")
    n0 = len(metadata)
    n1 = sum(1 for symbol, item in metadata.items() if is_rankable_stock(symbol, item.get("stock_type")))
    n2_symbols = {symbol: group for symbol, group in selected.items()
                  if symbol in metadata and is_rankable_stock(symbol, metadata[symbol].get("stock_type"))}
    n2 = len(n2_symbols)
    min_bars = 30
    def expected_minutes(market):
        windows = CONTINUOUS_WINDOWS.get(str(market or "HOSE").upper(), CONTINUOUS_WINDOWS["HOSE"])
        result = []
        minute = start.replace(second=0, microsecond=0)
        while minute < end:
            local_time = minute.timetz().replace(tzinfo=None)
            if any(opened <= local_time < closed for opened, closed, _ in windows):
                result.append(minute.isoformat(timespec="minutes"))
            minute += timedelta(minutes=1)
        return result

    expected_by_market = {market: expected_minutes(market) for market in {str(item.get("board") or "HOSE").upper() for item in metadata.values()}}
    eligible = {symbol: group for symbol, group in n2_symbols.items()
                if len(group) >= min_bars and expected_by_market.get(str(metadata[symbol].get("board") or "HOSE").upper())}
    n3 = len(eligible)
    coverage_threshold = 0.8
    coverage = {symbol: len(group) / max(1, len(expected_by_market[str(metadata[symbol].get("board") or "HOSE").upper()]))
                for symbol, group in eligible.items()}
    panel = {symbol: group for symbol, group in eligible.items()
             if coverage[symbol] >= coverage_threshold}
    relaxed = False
    target_panel_size = min(20, len(eligible))
    if len(panel) < target_panel_size:
        # C.4 allows a documented relaxed threshold when the standard balanced
        # panel would leave fewer than 20 stocks. This is explicit in the payload.
        panel = {symbol: eligible[symbol] for symbol in sorted(eligible, key=lambda key: coverage[key], reverse=True)[:target_panel_size]}
        relaxed = True
    if not panel:
        return {"source": source, "session_date": session_date, "rows": [], "n0": n0, "n1": n1,
                "n2": n2, "n3": n3, "panel_symbols": 0, "coverage_threshold": coverage_threshold,
                "coverage_relaxed": False, "window_minutes": window_minutes, "as_of": None,
                "empty": True, "message": "Chưa đủ 30 bar phút hợp lệ để lập nhóm cổ phiếu cân bằng."}
    minute_rows = []
    expected_chart_minutes = sorted({minute for values in expected_by_market.values() for minute in values
                                     if start <= datetime.fromisoformat(minute) < end})
    for minute in expected_chart_minutes:
        available = [group_by_minute[minute] for group in panel
                     if (group_by_minute := {datetime.fromisoformat(row["minute_vn"]).replace(second=0, microsecond=0).isoformat(timespec="minutes"): row for row in panel[group]}).get(minute)]
        if not available:
            minute_rows.append({"minute": minute, "bid_depth_value": None,
                                "ask_depth_value": None, "symbols_count": 0, "n_snapshots": 0})
            continue
        bids = [float(row["bid_depth_value"]) for row in available]
        asks = [float(row["ask_depth_value"]) for row in available]
        minute_rows.append({"minute": minute, "bid_depth_value": median(bids),
                            "ask_depth_value": median(asks), "symbols_count": len(available),
                            "n_snapshots": sum(int(row.get("n_snapshots") or 0) for row in available)})
    return {"source": source, "session_date": session_date, "rows": minute_rows,
            "n0": n0, "n1": n1, "n2": n2, "n3": n3, "panel_symbols": len(panel),
            "coverage_threshold": coverage_threshold, "coverage_relaxed": relaxed,
            "window_minutes": window_minutes, "as_of": minute_rows[-1]["minute"] if minute_rows else None,
            "empty": not bool(minute_rows),
            "message": None if minute_rows else "Chưa đủ bar hợp lệ để lập nhóm cổ phiếu cân bằng."}
