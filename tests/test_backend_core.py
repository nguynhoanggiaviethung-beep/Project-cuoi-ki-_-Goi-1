import tempfile
import unittest
from pathlib import Path

from src.sli import config, database
from src.sli.analytics import calculate_metrics, map_message, quantile, validate_observation
from src.sli.bookwalk import estimate
from src.sli.market_rules import baseline_bucket, session_phase, tick_size


def book():
    return {
        "symbol": "ABC", "market": "HOSE",
        "bid_price_1": 99_000, "bid_volume_1": 1_000,
        "bid_price_2": 98_900, "bid_volume_2": 1_000,
        "bid_price_3": 98_800, "bid_volume_3": 1_000,
        "ask_price_1": 99_100, "ask_volume_1": 1_000,
        "ask_price_2": 99_200, "ask_volume_2": 1_000,
        "ask_price_3": 99_300, "ask_volume_3": 1_000,
    }


class BackendCoreTests(unittest.TestCase):
    def test_spread_depth_and_imbalance(self):
        metrics = calculate_metrics(book())
        self.assertAlmostEqual(metrics["mid_price"], 99_050)
        self.assertAlmostEqual(metrics["spread_bps"], 10000 * 100 / 99050)
        self.assertEqual(metrics["spread_ticks"], 1)
        self.assertEqual(metrics["bid_depth_qty"], 3000)
        self.assertEqual(metrics["ask_depth_qty"], 3000)
        self.assertEqual(metrics["queue_imbalance"], 0)

    def test_tick_schedule(self):
        self.assertEqual(tick_size(9_990, "HOSE"), 10)
        self.assertEqual(tick_size(10_000, "HOSE"), 50)
        self.assertEqual(tick_size(49_950, "HOSE"), 50)
        self.assertIsNone(tick_size(49_970, "HOSE"))
        self.assertEqual(tick_size(50_000, "HOSE"), 100)
        self.assertEqual(tick_size(10_000, "HNX"), 100)

    def test_exchange_phases_and_buckets(self):
        self.assertEqual(session_phase("2026-11-02T09:05:00+07:00", "HOSE"), "ATO")
        self.assertEqual(session_phase("2026-11-02T09:05:00+07:00", "HNX"), "CONTINUOUS_AM")
        self.assertEqual(session_phase("2026-11-02T14:40:00+07:00", "UPCOM"), "CONTINUOUS_PM")
        self.assertEqual(baseline_bucket("2026-11-02T09:30:00+07:00", "HOSE"), "OPEN")
        self.assertEqual(baseline_bucket("2026-11-02T14:15:00+07:00", "HOSE"), "CLOSE")

    def test_validation_flags_locked_off_tick_and_negative_volume(self):
        item = book()
        item["ask_price_1"] = item["bid_price_1"]
        self.assertIn("locked", validate_observation(item)["flags"])
        item = book()
        item["bid_price_1"] = 99_050
        self.assertIn("off_tick", validate_observation(item)["flags"])
        item["bid_volume_1"] = -1
        self.assertEqual(validate_observation(item)["status"], "invalid")

    def test_mapper_handles_nested_payload_and_local_time(self):
        payload = {"data": [{"symbol": "abc", "tradingTime": "09:25:00",
                             "bidPrice1": "99000", "bidVol1": 100,
                             "askPrice1": "99100", "askVol1": 120}]}
        mapped = map_message(payload)
        self.assertEqual(len(mapped), 1)
        self.assertEqual(mapped[0]["symbol"], "ABC")
        self.assertTrue(mapped[0]["source_ts_vn"].endswith("+07:00"))
        self.assertEqual(mapped[0]["session_phase"], "CONTINUOUS_AM")

    def test_bookwalk_partial_and_slippage_limit(self):
        item = book()
        result = estimate(item, "buy", quantity=1500, max_slippage_bps=5.1)
        self.assertEqual(result["status"], "full")
        self.assertEqual(result["filled_quantity"], 1500)
        self.assertEqual(result["q_max_within_slippage"], 1000)
        partial = estimate(item, "buy", quantity=5000)
        self.assertEqual(partial["status"], "partial_exceeds_visible_depth")
        self.assertEqual(partial["fill_ratio"], 0.6)

    def test_quantile_linear_interpolation(self):
        self.assertEqual(quantile([0, 10, 20, 30], 50), 15)

    def test_database_schema_and_state_change_dedup(self):
        original = config.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            config.DB_PATH = Path(folder) / "test.sqlite3"
            try:
                database.initialize()
                item = book()
                item.update({"received_at_utc": "2026-11-02T09:20:00+00:00",
                             "received_at_vn": "2026-11-02T16:20:00+07:00",
                             "source_ts_vn": None, "raw_payload": {"symbol": "ABC", "snapshot": 9001},
                             "validation_status": "valid", "flags": [], "session_phase": "CLOSED"})
                first = database.insert_observation(item, "live")
                second = database.insert_observation(item, "live")
                self.assertTrue(first["inserted"])
                self.assertTrue(second["duplicate"])
                row = database.fetch_observations("live", "ABC", 1)[0]
                self.assertIn("spread_ticks", row)
                with database.connect() as db:
                    self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                    self.assertIsNotNone(db.execute("SELECT source_ts_vn FROM observations LIMIT 1").fetchone())
                database.update_stale_alert("live", True, 120, None)
                active = database.fetch_alerts("live", 10)
                self.assertTrue(any(item["alert_type"] == "stale_data" and item["status"] == "active" for item in active))
                database.update_stale_alert("live", False, 120, None)
                resolved = database.fetch_alerts("live", 10)
                self.assertTrue(any(item["alert_type"] == "stale_data" and item["status"] == "resolved" for item in resolved))
            finally:
                config.DB_PATH = original

    def test_completed_session_baseline_triggers_combined_alert(self):
        original_path = config.DB_PATH
        original_min = config.BASELINE_MIN_BARS
        original_k = config.ALERT_CONSECUTIVE_BARS
        with tempfile.TemporaryDirectory() as folder:
            config.DB_PATH = Path(folder) / "alerts.sqlite3"
            config.BASELINE_MIN_BARS = 2
            config.ALERT_CONSECUTIVE_BARS = 1
            try:
                database.initialize()
                with database.connect() as db:
                    for index in range(2):
                        db.execute("""INSERT INTO bars_1m
                            (source,symbol,minute_vn,session_date,market,session_phase,baseline_bucket,
                             spread_bps,spread_ticks,bid_depth_qty,ask_depth_qty,total_depth_value,
                             queue_imbalance,n_snapshots,coverage_status)
                            VALUES ('live','ABC',?,'2026-11-02','HOSE','CONTINUOUS_AM','OPEN',
                                    10,1,1000,1000,200000000,0,5,'ok')""",
                                   (f"2026-11-02T09:{20+index:02d}:00+07:00",))
                    observation = {
                        "symbol": "ABC", "market": "HOSE", "session_phase": "CONTINUOUS_AM",
                        "received_at_utc": "2026-11-03T02:20:00+00:00",
                        "received_at_vn": "2026-11-03T09:20:00+07:00",
                        "raw_payload": {"snapshot": "high-spread-low-depth"}, "validation_status": "valid", "flags": [],
                        "bid_price_1": 99_000, "bid_volume_1": 100,
                        "bid_price_2": 98_900, "bid_volume_2": 100,
                        "bid_price_3": 98_800, "bid_volume_3": 100,
                        "ask_price_1": 100_000, "ask_volume_1": 100,
                        "ask_price_2": 100_100, "ask_volume_2": 100,
                        "ask_price_3": 100_200, "ask_volume_3": 100,
                    }
                    database._insert(db, observation, "live")
                    types = {r[0] for r in db.execute("SELECT alert_type FROM alerts WHERE source='live'")}
                self.assertIn("spread_widening", types)
                self.assertIn("depth_drop", types)
                self.assertIn("combined_liquidity_stress", types)
            finally:
                config.DB_PATH = original_path
                config.BASELINE_MIN_BARS = original_min
                config.ALERT_CONSECUTIVE_BARS = original_k


if __name__ == "__main__":
    unittest.main()
