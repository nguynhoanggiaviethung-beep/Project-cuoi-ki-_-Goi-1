import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

from src.sli import depth_service


def test_market_depth_uses_balanced_common_stock_panel_and_medians():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE bars_1m(
        source TEXT,symbol TEXT,minute_vn TEXT,session_date TEXT,market TEXT,
        session_phase TEXT,baseline_bucket TEXT,bid_depth_value REAL,
        ask_depth_value REAL,n_snapshots INTEGER,coverage_status TEXT)""")
    start = datetime.fromisoformat("2026-11-06T09:15:00+07:00")
    for i in range(30):
        minute = (start + timedelta(minutes=i)).isoformat(timespec="seconds")
        for symbol, bid, ask in (("AAA", 100 + i, 80 + i), ("BBB", 300 + i, 120 + i)):
            db.execute("INSERT INTO bars_1m VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       ("ssi", symbol, minute, "2026-11-06", "HOSE", "CONTINUOUS_AM", "MID",
                        bid, ask, 1, "ok"))
    metadata = {symbol: {"stock_type": "stock"} for symbol in ("AAA", "BBB")}
    with patch.object(depth_service.database, "connect", return_value=db), \
         patch.object(depth_service.database, "fetch_security_metadata", return_value=metadata):
        result = depth_service.build_market_depth(60, "ssi", {"session_date": "2026-11-06", "state": "CLOSED"})
    assert result["n0"] == result["n1"] == result["n2"] == result["n3"] == 2
    assert len(result["rows"]) == 30
    assert result["rows"][0]["bid_depth_value"] == 200
    assert result["rows"][0]["ask_depth_value"] == 100
    assert result["rows"][0]["symbols_count"] == 2


def test_market_depth_excludes_non_stock_and_requires_30_bars():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE bars_1m(
        source TEXT,symbol TEXT,minute_vn TEXT,session_date TEXT,market TEXT,
        session_phase TEXT,baseline_bucket TEXT,bid_depth_value REAL,
        ask_depth_value REAL,n_snapshots INTEGER,coverage_status TEXT)""")
    start = datetime.fromisoformat("2026-11-06T09:15:00+07:00")
    for i in range(12):
        minute = (start + timedelta(minutes=i)).isoformat(timespec="seconds")
        db.execute("INSERT INTO bars_1m VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   ("ssi", "AAA", minute, "2026-11-06", "HOSE", "CONTINUOUS_AM", "MID", 100, 80, 1, "ok"))
    with patch.object(depth_service.database, "connect", return_value=db), \
         patch.object(depth_service.database, "fetch_security_metadata", return_value={"AAA": {"stock_type": "warrant"}}):
        result = depth_service.build_market_depth(60, "ssi", {"session_date": "2026-11-06", "state": "CLOSED"})
    assert result["n3"] == 0
    assert result["rows"] == []
