"""Shared, persisted-bar service for the overview's two-sided market depth."""
from datetime import datetime, timedelta
from statistics import median
import time

from . import config, database
from .market_rules import CONTINUOUS_WINDOWS
from .ranking import is_rankable_stock

_closed_session_cache = {}


def _build_market_depth_session(window_minutes=60, source="ssi", market_meta=None):
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
    all_minutes = sorted({datetime.fromisoformat(row["minute_vn"]).replace(tzinfo=None, second=0, microsecond=0).isoformat(timespec="minutes")
                          for group in by_symbol.values() for row in group})
    if not all_minutes:
        return {"source": source, "session_date": session_date, "rows": [], "n0": len(by_symbol), "n1": 0,
                "n2": 0, "n3": 0, "empty": True, "message": "Chưa có bar phút liên tục hợp lệ trong phiên gần nhất."}
    latest_minute = datetime.fromisoformat(all_minutes[-1])
    effective_stamp = datetime.fromisoformat(str(effective)).replace(tzinfo=None) if effective else None
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
    selected = {symbol: [row for row in group
                         if datetime.fromisoformat(row["minute_vn"]).replace(tzinfo=None, second=0, microsecond=0).isoformat(timespec="minutes") in selected_minutes]
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
    panel_by_minute = {
        symbol: {datetime.fromisoformat(row["minute_vn"]).replace(tzinfo=None, second=0, microsecond=0).isoformat(timespec="minutes"): row
                 for row in group}
        for symbol, group in panel.items()
    }
    for minute in expected_chart_minutes:
        available = [minute_map[minute] for minute_map in panel_by_minute.values() if minute in minute_map]
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


def build_market_depth(window_minutes=60, source="ssi", market_meta=None,
                       start_date=None, end_date=None):
    """Return either the latest intraday window or all stored sessions in a date range."""
    meta = market_meta or {}
    if not start_date and not end_date:
        return _build_market_depth_session(window_minutes, source, meta)
    with database.connect() as db:
        clauses = ["source=?"]
        args = [source]
        if start_date:
            clauses.append("session_date>=?")
            args.append(str(start_date)[:10])
        if end_date:
            clauses.append("session_date<=?")
            args.append(str(end_date)[:10])
        dates = [str(row[0]) for row in db.execute(
            f"SELECT DISTINCT session_date FROM bars_1m WHERE {' AND '.join(clauses)} ORDER BY session_date",
            args).fetchall()]
    if not dates:
        return {"source": source, "session_date": None, "session_dates": [], "rows": [],
                "panel_symbols": 0, "n3": 0, "as_of": None, "empty": True,
                "message": "Không có dữ liệu độ sâu trong khoảng phiên đã chọn."}
    sessions = []
    now_monotonic = time.monotonic()
    live_session = str(meta.get("session_date") or "")[:10] if meta.get("state") in {"IN_SESSION", "LUNCH_BREAK"} else None
    for date in dates:
        cached = _closed_session_cache.get((source, date))
        if date != live_session and cached and now_monotonic - cached[0] < 300:
            sessions.append(cached[1])
            continue
        session_meta = dict(meta)
        session_meta["session_date"] = date
        if date != str(meta.get("session_date") or "")[:10]:
            session_meta["state"] = "CLOSED"
            session_meta["effective_now"] = None
        item = _build_market_depth_session(390, source, session_meta)
        if item.get("rows"):
            sessions.append(item)
            if date != live_session:
                _closed_session_cache[(source, date)] = (now_monotonic, item)
    if len(_closed_session_cache) > 256:
        for key, _ in sorted(_closed_session_cache.items(), key=lambda item: item[1][0])[:64]:
            _closed_session_cache.pop(key, None)
    if not sessions:
        return {"source": source, "session_date": dates[-1], "session_dates": dates,
                "rows": [], "panel_symbols": 0, "n3": 0, "as_of": None,
                "empty": True, "message": "Chưa có bar phút hợp lệ trong các phiên đã chọn."}
    rows = [row for item in sessions for row in item["rows"]]
    latest = sessions[-1]
    return {**latest, "source": source, "rows": rows, "session_dates": dates,
            "session_count": len(sessions), "session_date": latest["session_date"],
            "as_of": latest.get("as_of"), "window_minutes": None,
            "empty": False}
