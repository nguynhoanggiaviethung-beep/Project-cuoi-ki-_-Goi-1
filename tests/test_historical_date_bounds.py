import tempfile
from pathlib import Path
from unittest.mock import patch

from src.sli import config, database


def test_history_bounds_and_latest_date_use_only_catalogued_common_stocks():
    with tempfile.TemporaryDirectory() as folder, patch.object(config, "DB_PATH", Path(folder) / "test.sqlite3"):
        database.initialize()
        with database.connect() as db:
            db.executemany(
                "INSERT INTO securities_catalog(source,symbol,stock_type,updated_at) VALUES ('ssi',?,?,?)",
                [("AAA", "stock", "2026-11-08"), ("BBB", "stock", "2026-11-08"),
                 ("WARRANT", "warrant", "2026-11-08")],
            )
            db.executemany(
                """INSERT INTO historical_prices(
                    source,symbol,trading_date,timeframe,close_price,volume,value,fetched_at
                ) VALUES ('ssi',?,?,'1d',100,100,1000,?)""",
                [
                    ("AAA", "2025/01/02", "2025-01-03T08:00:00+07:00"),
                    ("AAA", "2026/11/06", "2026-11-07T08:00:00+07:00"),
                    ("BBB", "2025/02/03", "2025-02-04T08:00:00+07:00"),
                    ("BBB", "2026/11/05", "2026-11-06T08:00:00+07:00"),
                    ("WARRANT", "2000/01/01", "2000-01-02T08:00:00+07:00"),
                ],
            )

        assert database.historical_date_bounds("ssi") == {
            "earliest_date": "2025-01-02", "latest_date": "2026-11-06"
        }
        assert database.latest_historical_date("ssi") == "2026-11-06"
        assert database.latest_data_as_of("ssi") == "2026-11-07T08:00:00+07:00"
