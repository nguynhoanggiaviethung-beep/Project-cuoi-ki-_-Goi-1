from datetime import date, datetime, timedelta
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


def _bars_for_baseline(phases_by_day, current_extra=0):
    rows=[]
    for day,phase,count in phases_by_day:
        for index in range(count):
            hour,minute=(10,index%60) if phase=="MID" else (14,30+index%30) if phase=="CLOSE" else (9,15+index%30)
            rows.append({"symbol":"FPT","minute_vn":f"{day}T{hour:02d}:{minute:02d}+07:00","session_date":day,
                         "market":"HOSE","session_phase":"CONTINUOUS_AM" if phase!="CLOSE" else "CONTINUOUS_PM",
                         "baseline_bucket":phase,"coverage_status":"ok","spread_bps":10+index/100,
                         "total_depth_value":1_000_000+index,"n_snapshots":3})
    for index in range(current_extra):
        rows.append({"symbol":"FPT","minute_vn":f"2026-11-06T14:{index%30:02d}+07:00","session_date":"2026-11-06",
                     "market":"HOSE","session_phase":"CONTINUOUS_PM","baseline_bucket":"CLOSE",
                     "coverage_status":"ok","spread_bps":500+index,"total_depth_value":2_000_000+index,"n_snapshots":3})
    rows.append({"symbol":"FPT","minute_vn":"2026-11-06T14:59+07:00","session_date":"2026-11-06",
                 "market":"HOSE","session_phase":"CONTINUOUS_PM","baseline_bucket":"CLOSE",
                 "coverage_status":"ok","spread_bps":12,"total_depth_value":900_000,"n_snapshots":3})
    return sorted(rows,key=lambda row:row["minute_vn"])


def _build_with_bars(bars):
    as_of=datetime.fromisoformat("2026-11-06T15:00:00+07:00")
    patches=mocked_storage([sample_quote("2026-11-06T14:59:00+07:00")],bars)
    with patches[0],patches[1],patches[2],patches[3],patches[4]:
        return detail_service.build_detail("FPT","ssi",as_of,window_sessions=1)


def _baseline_sessions(count,phase,extra_first=0):
    days=[(date(2026,11,6)-timedelta(days=offset)).isoformat() for offset in range(count,0,-1)]
    return [(day,phase,1+(extra_first if index==0 else 0)) for index,day in enumerate(days)]


def test_baseline_uses_same_phase_when_120_prior_sessions_and_bars_exist():
    bars=_bars_for_baseline(_baseline_sessions(120,"CLOSE"))
    result=_build_with_bars(bars)
    assert result["baseline_eligible"] is True
    assert result["baseline_mode"]=="same_phase"
    assert result["baseline_sessions_have"]==120
    assert result["baseline_bars_have"]==120
    assert result["baseline_sessions_need"]==120
    assert result["baseline_bars_need"]==120


def test_baseline_pools_phases_for_the_same_symbol_when_current_phase_is_sparse():
    bars=_bars_for_baseline(_baseline_sessions(120,"MID"))
    result=_build_with_bars(bars)
    assert result["baseline_eligible"] is True
    assert result["baseline_mode"]=="pooled"
    assert result["baseline_sessions_have"]==120
    assert result["baseline_bars_have"]==120


def test_baseline_needs_120_prior_sessions_and_does_not_look_ahead():
    # Extra current-session bars cannot replace one missing completed session.
    bars=_bars_for_baseline(_baseline_sessions(119,"CLOSE",extra_first=1),current_extra=100)
    result=_build_with_bars(bars)
    assert result["baseline_eligible"] is False
    assert result["baseline_mode"]=="insufficient"
    assert result["baseline_sessions_have"]==119
    assert result["baseline_bars_have"]==120


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
