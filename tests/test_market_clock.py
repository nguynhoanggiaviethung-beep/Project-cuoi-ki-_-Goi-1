from datetime import datetime
from pathlib import Path
import tempfile

import pytest

from src.sli.market_clock import get_market_state
from src.sli import config, database


def at(value):
    return datetime.fromisoformat(value + "+07:00")


@pytest.mark.parametrize(
    ("stamp", "state", "phase", "next_change"),
    [
        ("2026-11-06T08:59:59", "CLOSED", None, "2026-11-06T09:00:00+07:00"),
        ("2026-11-06T09:00:00", "IN_SESSION", "ATO", "2026-11-06T09:15:00+07:00"),
        ("2026-11-06T09:15:00", "IN_SESSION", "CONTINUOUS", "2026-11-06T11:30:00+07:00"),
        ("2026-11-06T11:30:00", "LUNCH_BREAK", None, "2026-11-06T13:00:00+07:00"),
        ("2026-11-06T13:00:00", "IN_SESSION", "CONTINUOUS", "2026-11-06T14:30:00+07:00"),
        ("2026-11-06T14:30:00", "IN_SESSION", "ATC", "2026-11-06T14:45:00+07:00"),
        ("2026-11-06T14:45:00", "CLOSED", None, "2026-11-09T09:00:00+07:00"),
    ],
)
def test_hose_session_clock_table(stamp, state, phase, next_change):
    result = get_market_state(at(stamp), "HOSE")
    assert (result["state"], result["phase"]) == (state, phase)
    assert result["next_change_at"] == next_change
    assert result["is_trading_day"] is True
    if stamp.endswith("11:30:00"):
        assert result["effective_now"] == "2026-11-06T11:30:00+07:00"
        assert result["next_open_at"] == "2026-11-06T13:00:00+07:00"


@pytest.mark.parametrize("stamp", ["2026-11-07T10:00:00", "2026-11-08T10:00:00"])
def test_weekend_is_closed(stamp):
    result = get_market_state(at(stamp), "ALL")
    assert result["state"] == "CLOSED"
    assert result["closed_reason"] == "weekend"
    assert result["is_trading_day"] is False
    assert result["next_open_at"] == "2026-11-09T09:00:00+07:00"


def test_holiday_is_closed_and_next_open_skips_weekend():
    result = get_market_state(at("2026-11-06T10:00:00"), "ALL", holidays={"2026-11-06"})
    assert result["state"] == "CLOSED"
    assert result["closed_reason"] == "holiday"
    assert result["next_open_at"] == "2026-11-09T09:00:00+07:00"


def test_global_market_uses_upcom_close_at_1500():
    result = get_market_state(at("2026-11-06T14:45:00"), "ALL")
    assert result["state"] == "IN_SESSION"
    assert result["exchange_states"]["HOSE"]["state"] == "CLOSED"
    assert result["exchange_states"]["UPCOM"]["state"] == "IN_SESSION"
    assert result["next_change_at"] == "2026-11-06T15:00:00+07:00"


def test_session_close_and_ohlc_status_are_persisted():
    original=config.DB_PATH
    with tempfile.TemporaryDirectory() as folder:
        config.DB_PATH=Path(folder)/"sessions.sqlite3"
        try:
            database.initialize()
            database.record_session_close("2026-11-06")
            pending=database.get_session_record("2026-11-06")
            assert pending["status"]=="closed_ohlc_pending"
            result=database.update_session_ohlc_status("2026-11-06")
            assert result["complete"] is False
            assert database.get_session_record("2026-11-06")["status"]=="ohlc_incomplete"
        finally:
            config.DB_PATH=original


def test_historical_queries_can_hold_back_an_unfinished_session():
    original=config.DB_PATH
    with tempfile.TemporaryDirectory() as folder:
        config.DB_PATH=Path(folder)/"history.sqlite3"
        try:
            database.initialize()
            with database.connect() as db:
                db.execute("INSERT INTO securities_catalog(source,symbol,board,name_vi,stock_type,updated_at) VALUES('ssi','ABC','HOSE','ABC','stock','2026-11-06T17:00:00+07:00')")
                for day,close in (("2026/11/04",100),("2026/11/05",110),("2026/11/06",90)):
                    db.execute("""INSERT INTO historical_prices(source,symbol,trading_date,timeframe,open_price,high_price,low_price,close_price,volume,value,fetched_at)
                        VALUES('ssi','ABC',?,'1d',?,?,?,?,1000,100000,'2026-11-06T17:00:00+07:00')""",
                        (day,close,close,close,close))
            history=database.fetch_historical_prices("ABC",source="ssi",exclude_date="2026-11-06")
            assert [row["trading_date"] for row in history]==["2026/11/04","2026/11/05"]
            page=database.fetch_latest_historical_page(page=1,page_size=10,source="ssi",exclude_date="2026-11-06")
            assert page["rows"][0]["trading_date"]=="2026/11/05"
            ranked=database.fetch_market_leaders(limit=5,source="ssi",as_of="2026/11/05")
            assert ranked[0]["trading_date"]=="2026/11/05"
        finally:
            config.DB_PATH=original


