from datetime import datetime
from unittest.mock import patch

from src.sli import detail_service


def sample_quote(stamp="2026-11-05T10:00:00+07:00"):
    return {
        "symbol": "FPT", "validation_status": "valid", "received_at_vn": stamp,
        "source_ts_vn": stamp, "spread_bps": 12.0, "spread_ticks": 1,
        "bid_depth_value": 1000.0, "ask_depth_value": 900.0, "total_depth_value": 1900.0,
        "queue_imbalance": .05, "value_imbalance": .02,
        "bid_price_1": 100000, "bid_volume_1": 100, "ask_price_1": 100100, "ask_volume_1": 100,
    }


def mocked_storage(quote_rows=None, bars=None, history=None):
    return (
        patch.object(detail_service.database, "fetch_company_metadata", return_value={"symbol":"FPT", "name_vi":"Công ty FPT", "board":"HOSE", "stock_type":"stock"}),
        patch.object(detail_service.database, "fetch_observations", return_value=quote_rows or []),
        patch.object(detail_service.database, "fetch_bars", return_value=bars or []),
        patch.object(detail_service.database, "fetch_historical_prices", return_value=history or []),
        patch.object(detail_service.database, "fetch_alerts", return_value=[]),
    )


def test_detail_payload_uses_one_as_of_and_has_independent_widgets():
    as_of = datetime.fromisoformat("2026-11-05T10:00:10+07:00")
    patches = mocked_storage([sample_quote()])
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = detail_service.build_detail("FPT", "ssi", as_of)
    assert result["as_of"] == "2026-11-05T10:00:10+07:00"
    assert result["state"] == "quote"
    assert all("status" in widget for widget in result["widgets"].values())
    assert result["widgets"]["kpis"]["data"]["spread_percentile"] is None


def test_detail_does_not_invent_baseline_without_history():
    as_of = datetime.fromisoformat("2026-11-05T10:00:10+07:00")
    patches = mocked_storage([sample_quote()])
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = detail_service.build_detail("FPT", "ssi", as_of)
    assert result["baseline_eligible"] is False
    assert result["widgets"]["charts"]["data"]["baseline_sample_count"] == 0
    assert result["widgets"]["kpis"]["data"]["spread_percentile"] is None


def test_detail_rejects_non_stock_catalog_entry():
    as_of = datetime.fromisoformat("2026-11-05T10:00:10+07:00")
    with patch.object(detail_service.database, "fetch_company_metadata", return_value={"stock_type":"etf"}):
        result = detail_service.build_detail("ETF1", "ssi", as_of)
    assert result["error"] == "Mã cổ phiếu không có trong danh mục."


def test_detail_marks_old_quote_stale_during_continuous_session():
    as_of = datetime.fromisoformat("2026-11-05T10:05:00+07:00")
    old = sample_quote("2026-11-05T10:00:00+07:00")
    patches = mocked_storage([old])
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = detail_service.build_detail("FPT", "ssi", as_of)
    assert result["state"] == "stale"


def test_hnx_depth_curve_reports_only_saved_levels_without_fabricating_more():
    as_of = datetime.fromisoformat("2026-11-05T15:00:00+07:00")
    quote = sample_quote("2026-11-05T14:29:00+07:00")
    quote.update({"bid_price_2": 99900, "bid_volume_2": 200,
                  "ask_price_2": 100200, "ask_volume_2": 300})
    patches = mocked_storage([quote])
    with patch.object(detail_service.database, "fetch_company_metadata",
                      return_value={"symbol":"FPT", "name_vi":"Công ty FPT", "board":"HNX", "stock_type":"stock"}), \
         patches[1], patches[2], patches[3], patches[4]:
        result = detail_service.build_detail("FPT", "ssi", as_of)
    curve = result["widgets"]["charts"]["data"]["depth_curve"]
    assert curve["levels_used"] == 2
    assert len(curve["bid"]) == len(curve["ask"]) == 2
    assert curve["bid"][0]["cumulative_volume"] == 300
    assert curve["ask"][-1]["cumulative_volume"] == 400


def test_depth_backfill_uses_only_saved_valid_metrics():
    rows = [
        {"validation_status":"valid", "flags":"[]", "bid_depth_value":100, "ask_depth_value":80},
        {"validation_status":"valid", "flags":"[]", "bid_depth_value":300, "ask_depth_value":120},
        {"validation_status":"invalid", "flags":"[]", "bid_depth_value":999, "ask_depth_value":999},
        {"validation_status":"valid", "flags":"[\"one_sided_limit\"]", "bid_depth_value":999, "ask_depth_value":999},
    ]
    assert detail_service._saved_depth_medians(rows) == (200.0, 100.0)
