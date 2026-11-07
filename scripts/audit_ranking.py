"""Audit SSI ranking against stored observations and minute bars.

Run from the project root with: .venv\\Scripts\\python.exe scripts\\audit_ranking.py
"""
import json
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.sli import api, database
from src.sli.analytics import calculate_metrics
from src.sli.ranking import COVERED_WARRANT, is_rankable_stock

SOURCE = "ssi"
WINDOW_MINUTES = 390
EXCLUDED_FLAGS = {"one_sided_limit", "auction_phase", "off_tick", "crossed_book", "late_message",
                  "future_source_timestamp", "invalid_source_timestamp"}


def raw_minute_medians(start_at, end_at):
    sql = """SELECT o.symbol,o.received_at_vn,o.session_phase,o.validation_status,o.flags,
      m.spread_bps,m.spread_ticks,m.total_depth_value,
      o.bid_price_1,o.bid_volume_1,o.bid_price_2,o.bid_volume_2,o.bid_price_3,o.bid_volume_3,
      o.ask_price_1,o.ask_volume_1,o.ask_price_2,o.ask_volume_2,o.ask_price_3,o.ask_volume_3,o.market
      FROM observations o JOIN metrics m ON m.observation_id=o.id
      WHERE o.source=? AND datetime(o.received_at_vn)>=datetime(?) AND datetime(o.received_at_vn)<datetime(?)
      AND o.session_phase IN ('CONTINUOUS_AM','CONTINUOUS_PM') ORDER BY o.symbol,o.received_at_vn"""
    with database.connect() as db:
        raw = [dict(row) for row in db.execute(sql, (SOURCE, start_at, end_at)).fetchall()]
    per_minute = defaultdict(list)
    for row in raw:
        if row["validation_status"] != "valid":
            continue
        try:
            if EXCLUDED_FLAGS.intersection(json.loads(row.get("flags") or "[]")):
                continue
        except (TypeError, json.JSONDecodeError):
            continue
        if any(row.get(key) is None or float(row[key]) <= 0 for key in ("spread_bps", "spread_ticks", "total_depth_value")):
            continue
        stamp = datetime.fromisoformat(row["received_at_vn"].replace("Z", "+00:00"))
        minute = stamp.replace(second=0, microsecond=0).isoformat(timespec="minutes")
        per_minute[(row["symbol"], minute)].append(row)
    grouped = defaultdict(list)
    for (symbol, minute), observations in per_minute.items():
        grouped[symbol].append({"minute_vn": minute,
            "spread_bps": median(float(x["spread_bps"]) for x in observations),
            "spread_ticks": median(float(x["spread_ticks"]) for x in observations),
            "total_depth_value": median(float(x["total_depth_value"]) for x in observations),
            "n_snapshots": len(observations), "coverage_status": "ok",
            "session_phase": observations[0]["session_phase"]})
    for rows in grouped.values():
        rows.sort(key=lambda row: row["minute_vn"])
    return raw, grouped


def run_audit():
    start_at, end_at = api._market_window(WINDOW_MINUTES, SOURCE)
    metadata = database.fetch_security_metadata(SOURCE)
    raw, raw_bars = raw_minute_medians(start_at, end_at)
    original_source = api._source
    api._source = lambda: SOURCE
    try:
        result = api.rankings(limit=2000, window_minutes=WINDOW_MINUTES, start_at=start_at,
                              end_at=end_at, phase=None, board=None)
    finally:
        api._source = original_source
    api_rows = {row["symbol"]: row for row in result["symbols"]}
    assert all(not COVERED_WARRANT.fullmatch(symbol) for symbol in api_rows), "Covered warrant found in ranking"
    assert all(row["bar_count"] >= result["minimum_eligible_samples"] for row in api_rows.values()), "Below bar threshold"
    assert all(row["bar_count"] <= WINDOW_MINUTES for row in api_rows.values()), "More bars than window permits"
    assert all(row["median_spread_bps"] > 0 and row["median_total_depth_value"] > 0 for row in api_rows.values()), "Non-positive metric in ranking"
    assert all(is_rankable_stock(symbol, metadata.get(symbol, {}).get("stock_type")) for symbol in api_rows), "Non-stock symbol in ranking"
    eligible = sorted(api_rows)
    sample_symbols = random.Random(20261105).sample(eligible, min(5, len(eligible)))
    print(f"SSI ranking audit · cửa sổ {start_at} → {end_at} giờ Việt Nam")
    print(f"Catalog: {result['catalog_symbols']} | có bar: {result['symbols_with_window_bars']} | quote hợp lệ: {result['symbols_with_valid_quotes']} | đủ ngưỡng: {len(api_rows)}")
    print(f"Ngưỡng: {result['minimum_eligible_samples']} bar/phút; max bars/window: {WINDOW_MINUTES}; chứng quyền: 0; spread/depth dương: PASS")
    print("So sánh median API với raw observations tái tổng hợp theo phút:")
    for symbol in sample_symbols:
        rows = raw_bars.get(symbol, [])
        raw_spread = median(row["spread_bps"] for row in rows)
        raw_depth = median(row["total_depth_value"] for row in rows)
        api_row = api_rows[symbol]
        spread_error = abs(raw_spread - api_row["median_spread_bps"])
        depth_error = abs(raw_depth - api_row["median_total_depth_value"])
        assert spread_error < 1e-6 and depth_error < 1e-6, f"{symbol}: median mismatch"
        print(f"  {symbol}: spread {raw_spread:.8f} vs API {api_row['median_spread_bps']:.8f} (Δ {spread_error:.2g}); depth {raw_depth:.3f} vs API {api_row['median_total_depth_value']:.3f} (Δ {depth_error:.2g}); {len(rows)} bars")

    one_tick = sum(abs(row["spread_ticks"] - 1) < 1e-9 for row in api_rows.values())
    distribution = defaultdict(list)
    for row in api_rows.values():
        distribution[round(row["spread_ticks"], 2)].append(row["median_spread_bps"])
    print(f"Spread đúng 1 tick: {one_tick}/{len(api_rows)} ({(100*one_tick/len(api_rows)) if api_rows else 0:.1f}%)")
    print("Phân phối spread bps theo số tick (median [min–max]):")
    for ticks in sorted(distribution):
        values = distribution[ticks]
        print(f"  {ticks:g} tick: {median(values):.2f} [{min(values):.2f}–{max(values):.2f}] bps · {len(values)} mã")

    details = []
    level_fields = [f"{side}_{field}_{level}" for level in range(1, 4)
                    for side, field in (("bid", "price"), ("bid", "volume"), ("ask", "price"), ("ask", "volume"))]
    for symbol in sample_symbols:
        candidate = next((row for row in reversed(raw) if row["symbol"] == symbol
                          and row["validation_status"] == "valid"
                          and all(row.get(field) is not None for field in level_fields)), None)
        if candidate:
            details.append(candidate)
        if len(details) == 3:
            break
    print("Đối chiếu depth từ snapshot thô (giá: đồng/cổ phiếu; khối lượng: cổ phiếu):")
    for row in details:
        metrics = calculate_metrics({**{key: row.get(key) for key in level_fields}, "market": row.get("market") or "HOSE"})
        bid_terms = [f"{row[f'bid_price_{i}']:g}×{row[f'bid_volume_{i}']:g}" for i in range(1, 4)]
        ask_terms = [f"{row[f'ask_price_{i}']:g}×{row[f'ask_volume_{i}']:g}" for i in range(1, 4)]
        print(f"  {row['symbol']} · bid: {' + '.join(bid_terms)} = {metrics['bid_depth_value']:,.0f} đồng; ask: {' + '.join(ask_terms)} = {metrics['ask_depth_value']:,.0f} đồng; tổng = {metrics['total_depth_value']:,.0f} đồng")
        assert metrics["total_depth_value"] > 0
    if len(api_rows) < 5:
        print("CẢNH BÁO: Có dưới 5 mã đạt ngưỡng; đã đối chiếu tất cả mã đủ điều kiện.")
    return result


if __name__ == "__main__":
    run_audit()
