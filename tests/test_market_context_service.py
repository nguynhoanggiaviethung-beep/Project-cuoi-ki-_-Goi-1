from unittest.mock import patch

from src.sli.market_context_service import build_turnover_series, get_market_turnover


def daily(value, volume=2_000_000, index=0):
    return {
        "trading_date": f"2026/10/{index + 1:02d}",
        "total_value": value,
        "total_volume": volume,
        "advancing": 4,
        "declining": 3,
        "unchanged": 1,
        "data_as_of": "2026-11-06T16:00:00+07:00",
    }


def test_value_20_session_average_and_spike_are_causal():
    rows = [daily(10_000_000_000_000, index=i) for i in range(19)]
    rows.append(daily(14_000_000_000_000, index=19))
    rows.append(daily(15_000_000_000_000, index=20))

    result = build_turnover_series(rows, "value", spike_multiplier=1.4)

    assert all(row["ma20"] is None for row in result["rows"][:19])
    assert result["rows"][19]["ma20"] == 10.2
    assert result["rows"][20]["ma20"] == 10.45
    assert result["rows"][20]["is_spike"] is True
    assert round(result["kpis"]["ratio_to_average20_pct"], 2) == 143.54
    assert result["kpis"]["higher_than_pct"] == 100
    assert result["unit"] == "nghìn tỷ đồng"


def test_volume_is_converted_to_million_shares_and_missing_is_not_inferred():
    result = build_turnover_series([daily(None, 720_000_000)], "volume")

    assert result["rows"][0]["value"] == 720
    assert result["rows"][0]["ma20"] is None
    assert result["unit"] == "triệu cổ phiếu"
    assert result["kpis"]["latest_value"] == 720


def test_invalid_metric_is_rejected():
    try:
        build_turnover_series([], "price")
    except ValueError as exc:
        assert "metric" in str(exc)
    else:
        raise AssertionError("invalid metric should be rejected")


def test_closed_market_marks_partial_latest_ohlc_pending_and_uses_last_complete_session():
    rows = [
        {"trading_date": "2026/11/05", "total_value": 10_000_000_000_000,
         "total_volume": 500_000_000, "data_as_of": "2026-11-05T15:00:00+07:00"},
        {"trading_date": "2026/11/06", "total_value": 12_000_000_000_000,
         "total_volume": 600_000_000, "data_as_of": "2026-11-06T15:00:00+07:00"},
    ]
    meta = {"state": "CLOSED", "closed_reason": "weekend", "session_date": "2026-11-06",
            "session_sync": {"status": "ohlc_incomplete"}}
    with patch("src.sli.market_context_service.database.fetch_market_ohlc_sessions", return_value=rows):
        result = get_market_turnover(market_meta=meta)

    assert result["pending_session_date"] == "2026-11-06"
    assert result["pending_ohlc_missing"] is True
    assert result["session_date"] == "2026/11/05"
    assert result["note"] == "Dữ liệu OHLC ngày 2026-11-06 chưa được đồng bộ từ SSI."


def test_closed_session_reuses_same_ohlc_rows_for_value_and_volume_views():
    rows = [daily(2_000_000_000_000, 720_000_000, index=0)]
    meta = {"state": "CLOSED", "closed_reason": "weekend", "session_date": "2030-02-03"}
    with patch("src.sli.market_context_service.database.fetch_market_ohlc_sessions", return_value=rows) as fetch:
        value = get_market_turnover(market_meta=meta, metric="value")
        volume = get_market_turnover(market_meta=meta, metric="volume")

    assert fetch.call_count == 1
    assert value["rows"][0]["value"] == 2
    assert value["unit"] == "nghìn tỷ đồng"
    assert volume["rows"][0]["value"] == 720
    assert volume["unit"] == "triệu cổ phiếu"
