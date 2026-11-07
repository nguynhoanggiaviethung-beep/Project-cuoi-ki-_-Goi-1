"""Read-only contract audit for the D.1 detail-page service."""
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.sli import database
from src.sli.detail_service import build_detail


def main():
    if not database.config.DB_PATH.exists():
        raise SystemExit("Chưa có cơ sở dữ liệu SLI để kiểm tra.")
    source = "ssi"
    symbols = database.list_catalog_symbols(source)
    metadata = database.fetch_security_metadata(source)
    stocks = [s for s in symbols if (metadata.get(s, {}).get("stock_type") or "stock").lower() in
              {"stock", "common stock", "equity", "ordinary share", "common equity"}]
    default_symbol = database.fetch_detail_default_symbol(source)
    audit_symbols = list(dict.fromkeys(([default_symbol] if default_symbol else []) + stocks[:2]))
    with database.connect() as db:
        depth_coverage = db.execute("SELECT COUNT(*),SUM(CASE WHEN bid_depth_value IS NOT NULL AND ask_depth_value IS NOT NULL THEN 1 ELSE 0 END) FROM bars_1m WHERE source=?", (source,)).fetchone()
    total_bars, depth_bars = depth_coverage[0] or 0, depth_coverage[1] or 0
    print(f"catalog_symbols={len(symbols)}; common_stock_candidates={len(stocks)}; default_by_bar_coverage={default_symbol}; depth_bars={depth_bars}/{total_bars}")
    for symbol in audit_symbols:
        payload = build_detail(symbol, source, datetime.now(database.config.TIMEZONE))
        widgets = payload.get("widgets", {})
        same_clock = bool(payload.get("as_of")) and all("status" in item for item in widgets.values())
        expected = next((row for row in database.fetch_observations(source, symbol=symbol, limit=2000)
                         if row.get("validation_status") == "valid"), None)
        actual = widgets.get("orderbook", {}).get("data")
        quote_matches = (actual is None and expected is None) or bool(actual and expected and
                        actual.get("received_at_vn") == expected.get("received_at_vn") and
                        actual.get("bid_price_1") == expected.get("bid_price_1") and
                        actual.get("ask_price_1") == expected.get("ask_price_1"))
        chart = widgets.get("charts", {}).get("data") or {}
        depth_points = sum(1 for row in chart.get("depth", [])
                           if row.get("bid_depth_value") is not None and row.get("ask_depth_value") is not None)
        print(f"{symbol}: state={payload.get('state')}; widgets={len(widgets)}; one_as_of={same_clock}; latest_quote_matches_db={quote_matches}; depth_points={depth_points}; baseline_eligible={payload.get('baseline_eligible')}")
        assert same_clock and len(widgets) == 6 and quote_matches


if __name__ == "__main__":
    main()
