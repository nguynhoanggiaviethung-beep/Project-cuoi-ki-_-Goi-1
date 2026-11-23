import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.sli import config, database
from src.sli.api import _derive_pipeline_state
from src.sli.analytics import calculate_metrics, map_message, percentile_rank, quantile, validate_observation
from src.sli.bookwalk import estimate, book_walk_by_value, max_order_within_threshold
from src.sli.market_rules import baseline_bucket, session_phase, tick_size
from src.sli.worker_lock import CollectorWorkerLock, WorkerAlreadyRunning


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
        self.assertEqual(session_phase("2026-11-02T14:59:59+07:00", "UPCOM"), "CONTINUOUS_PM")
        self.assertEqual(session_phase("2026-11-02T15:00:00+07:00", "UPCOM"), "CLOSED")
        self.assertEqual(baseline_bucket("2026-11-02T09:30:00+07:00", "HOSE"), "OPEN")
        self.assertEqual(baseline_bucket("2026-11-02T14:15:00+07:00", "HOSE"), "CLOSE")

    def test_bar_uses_current_exchange_clock_instead_of_stale_observation_phase(self):
        original = config.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            config.DB_PATH = Path(folder) / "upcom.sqlite3"
            try:
                database.initialize()
                item = {
                    "symbol": "ABC", "market": "UPCOM", "session_phase": "CLOSED",
                    "received_at_utc": "2026-11-06T07:59:30+00:00",
                    "received_at_vn": "2026-11-06T14:59:30+07:00",
                    "source_ts_vn": "2026-11-06T14:59:30+07:00",
                    "raw_payload": {"symbol": "ABC"}, "validation_status": "valid",
                    "flags": [], "bid_price_1": 10_000, "bid_volume_1": 100,
                    "bid_price_2": 9_900, "bid_volume_2": 100,
                    "bid_price_3": 9_800, "bid_volume_3": 100,
                    "ask_price_1": 10_100, "ask_volume_1": 100,
                    "ask_price_2": 10_200, "ask_volume_2": 100,
                    "ask_price_3": 10_300, "ask_volume_3": 100,
                }
                database.insert_observation(item, "ssi")
                with database.connect() as db:
                    bar = db.execute("SELECT * FROM bars_1m WHERE source='ssi' AND symbol='ABC'").fetchone()
                self.assertIsNotNone(bar)
                self.assertEqual(bar["session_phase"], "CONTINUOUS_PM")
                self.assertEqual(bar["baseline_bucket"], "CLOSE")
            finally:
                config.DB_PATH = original

    def test_observation_batch_commits_chunks_and_refreshes_each_minute_bar(self):
        original = config.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            config.DB_PATH = Path(folder) / "batch.sqlite3"
            try:
                database.initialize()
                base = datetime.fromisoformat("2026-11-06T09:15:00+07:00")
                items = []
                for offset in range(205):
                    local = base + timedelta(seconds=offset)
                    utc = local.astimezone(timezone.utc)
                    item = book()
                    item.update({
                        "received_at_vn": local.isoformat(timespec="milliseconds"),
                        "received_at_utc": utc.isoformat(timespec="milliseconds"),
                        "source_ts_vn": local.isoformat(timespec="milliseconds"),
                        "source_timestamp": local.isoformat(timespec="milliseconds"),
                        "raw_payload": {}, "validation_status": "valid", "flags": [],
                        "session_phase": "CONTINUOUS_AM",
                    })
                    items.append(item)

                result = database.insert_observation_batch(items, "ssi")
                self.assertEqual(result["inserted"], 205)
                self.assertEqual(result["duplicate"], 0)
                with database.connect() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM observations WHERE source='ssi'").fetchone()[0], 205)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM metrics").fetchone()[0], 205)
                    bars = db.execute("SELECT n_snapshots FROM bars_1m WHERE source='ssi' ORDER BY minute_vn").fetchall()
                self.assertEqual([row[0] for row in bars], [60, 60, 60, 25])
                retry = database.insert_observation_batch(items, "ssi")
                self.assertEqual(retry["inserted"], 0)
                self.assertEqual(retry["duplicate"], 205)
            finally:
                config.DB_PATH = original

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
        payload = {"data": [{"symbol": "abc", "dateString": "2026-11-02", "tradingTime": "09:25:00",
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

    def test_bookwalk_by_value_and_threshold_matches_inv_d_appendix(self):
        asks=[(101_000,800),(101_500,1_200),(102_000,1_500)]
        bids=[(99_000,1_000),(98_500,1_500),(98_000,2_000)]
        result=book_walk_by_value(asks,202_600_000,100_000,"buy")
        self.assertEqual(result["status"],"full")
        self.assertEqual(result["filled_value"],202_600_000)
        self.assertEqual(result["vwap"],101_300)
        self.assertAlmostEqual(result["slippage_bps"],130)
        self.assertEqual(result["fill_ratio"],1)
        partial=book_walk_by_value(asks,500_000_000,100_000,"buy")
        self.assertEqual(partial["status"],"partial_exceeds_visible_depth")
        self.assertEqual(partial["filled_value"],355_600_000)
        self.assertEqual(partial["visible_depth_value"],355_600_000)
        maximum=max_order_within_threshold(asks,100_000,"buy",150)
        self.assertEqual(maximum["shares"],2_800)
        self.assertEqual(maximum["value"],284_200_000)
        self.assertFalse(maximum["full_depth"])
        sold=book_walk_by_value(bids,202_600_000,100_000,"sell")
        self.assertEqual(sold["status"],"full")
        self.assertEqual(sold["levels_used"],2)
        self.assertGreater(sold["slippage_bps"],0)
        sell_max=max_order_within_threshold(bids,100_000,"sell",150)
        self.assertEqual(sell_max["shares"],3_500)
        self.assertEqual(sell_max["value"],344_750_000)
        too_small=book_walk_by_value(asks,1_000_000,100_000,"buy")
        self.assertEqual(too_small["status"],"unavailable")
        zero=max_order_within_threshold(asks,100_000,"buy",0)
        self.assertTrue(zero["zero"])
        self.assertEqual(zero["shares"],0)

    def test_bookwalk_cash_examples_and_visible_depth_limit(self):
        levels=[(45_250,8_000),(45_300,12_000),(45_350,15_000)]
        mid=45_225
        small=book_walk_by_value(levels,50_000_000,mid,"buy")
        self.assertEqual(small["status"],"full")
        self.assertLessEqual(abs(small["slippage_bps"]-5.5),0.2)
        large=book_walk_by_value(levels,500_000_000,mid,"buy")
        self.assertEqual(large["status"],"full")
        self.assertLessEqual(abs(large["slippage_bps"]-8.5),0.2)
        low_price=book_walk_by_value([(8_350,10_000),(8_360,15_000),(8_370,20_000)],500_000_000,8_340,"buy")
        self.assertEqual(low_price["status"],"partial_exceeds_visible_depth")
        self.assertLess(low_price["filled_value"],500_000_000)
        odd_lot=book_walk_by_value([(10_000,199)],999_999,10_000,"buy")
        self.assertEqual(odd_lot["status"],"unavailable")
        no_side=book_walk_by_value([],50_000_000,45_225,"buy")
        self.assertEqual(no_side["status"],"unavailable")

    def test_quantile_linear_interpolation(self):
        self.assertEqual(quantile([0, 10, 20, 30], 50), 15)
        self.assertEqual(percentile_rank([0, 10, 20, 30], 15), 50)
        self.assertEqual(percentile_rank([0, 10, 10, 20], 10), 50)

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

    def test_collector_run_heartbeat_and_orphan_recovery(self):
        original = config.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            config.DB_PATH = Path(folder) / "runs.sqlite3"
            try:
                database.initialize()
                run_id = database.start_collector_run("worker-test", 1234)
                database.update_collector_heartbeat(run_id, {
                    "connection_status": "connected", "messages_received": 7,
                    "rows_valid": 6, "rows_invalid": 1, "last_message_at": "2026-11-06T10:00:00+07:00",
                    "subscription_status": "confirmed", "subscription_topics": ["quote.hose"],
                    "subscribed_boards": ["HOSE"], "subscription_ack_count": 1,
                    "quote_persistence_status": "saved", "last_persisted_at": "2026-11-06T10:00:01+07:00",
                    "trade_subscription_status": "partial", "trade_subscription_topics": ["trade.hose"],
                    "trade_persistence_status": "saved", "trade_messages_received": 3,
                    "trade_messages_saved": 2, "last_trade_received_at": "2026-11-06T10:00:00+07:00",
                    "last_trade_persisted_at": "2026-11-06T10:00:01+07:00", "queue_depth": 0,
                })
                run = database.latest_collector_run()
                self.assertEqual(run["worker_id"], "worker-test")
                self.assertEqual(run["process_id"], 1234)
                self.assertEqual(run["connection_status"], "connected")
                self.assertEqual(run["messages_received"], 7)
                self.assertEqual(run["subscription_status"], "confirmed")
                self.assertEqual(run["subscription_ack_count"], 1)
                self.assertEqual(run["quote_persistence_status"], "saved")
                self.assertEqual(run["last_persisted_at"], "2026-11-06T10:00:01+07:00")
                self.assertEqual(run["trade_messages_saved"], 2)
                self.assertIsNotNone(run["heartbeat_at"])
                self.assertEqual(database.close_abandoned_collector_runs(), 1)
                run = database.latest_collector_run()
                self.assertEqual(run["status"], "abandoned")
                self.assertIsNotNone(run["ended_at"])
                self.assertEqual(database.close_abandoned_collector_runs(), 0)
            finally:
                config.DB_PATH = original

    def test_realtime_health_requires_every_pipeline_stage(self):
        from datetime import datetime, timezone
        now = datetime(2026, 11, 20, 4, 0, tzinfo=timezone.utc)
        recent = "2026-11-20T11:59:50+07:00"
        common = dict(expected_stream=True, worker_active=True, connection_status="connected",
                      subscription_status="confirmed", received_at=recent, persisted_at=recent,
                      received_count=1, valid_count=1, persistence_status="saved",
                      queue_depth=0, queue_dropped=0, database_busy=False, now=now)
        result = _derive_pipeline_state(**common)
        self.assertEqual(result["state"], "live")
        self.assertTrue(result["verified"])
        self.assertTrue(all(result["checks"].values()))

        result = _derive_pipeline_state(**{**common, "subscription_status": "unverified"})
        self.assertEqual(result["state"], "subscription_unverified")
        self.assertFalse(result["verified"])
        result = _derive_pipeline_state(**{**common, "received_at": None, "received_count": 0})
        self.assertEqual(result["state"], "awaiting_quote")
        result = _derive_pipeline_state(**{**common, "persisted_at": None, "persistence_status": "writing"})
        self.assertEqual(result["state"], "awaiting_persistence")
        result = _derive_pipeline_state(**{**common, "persistence_status": "failed"})
        self.assertEqual(result["state"], "persistence_error")
        result = _derive_pipeline_state(**{**common, "queue_dropped": 1})
        self.assertEqual(result["state"], "degraded")
        result = _derive_pipeline_state(**{**common, "expected_stream": False})
        self.assertEqual(result["state"], "outside_session")

    def test_inv_c_positions_migration_is_additive_and_idempotent(self):
        original = config.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            config.DB_PATH = Path(folder) / "profile.sqlite3"
            try:
                with database.connect() as db:
                    db.execute("CREATE TABLE watchlists(id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
                    db.execute("INSERT INTO watchlists(id,name) VALUES(1,'Existing')")
                self.assertTrue(database.migrate_inv_c_positions())
                self.assertFalse(database.migrate_inv_c_positions())
                with database.connect() as db:
                    columns = {row[1] for row in db.execute("PRAGMA table_info(portfolio_positions)")}
                    self.assertTrue({"symbol", "shares", "average_cost", "opened_on", "note"}.issubset(columns))
                    self.assertEqual(db.execute("SELECT name FROM watchlists WHERE id=1").fetchone()[0], "Existing")
                position_id = database.add_local_position("FPT", 1000, 100000, "2026-11-20", "test")
                self.assertEqual(database.fetch_local_positions()[0]["id"], position_id)
            finally:
                config.DB_PATH = original

    def test_collector_worker_lock_rejects_duplicate(self):
        identity = Path(config.DB_PATH).parent / "lock-test.sqlite3"
        first = CollectorWorkerLock(identity).acquire()
        try:
            with self.assertRaises(WorkerAlreadyRunning):
                CollectorWorkerLock(identity).acquire()
        finally:
            first.release()

    def test_completed_session_baseline_triggers_combined_alert(self):
        original_path = config.DB_PATH
        original_min = config.BASELINE_MIN_BARS
        original_sessions = config.BASELINE_SESSIONS
        original_min_sessions = config.MIN_BASELINE_SESSIONS
        original_k = config.ALERT_CONSECUTIVE_BARS
        with tempfile.TemporaryDirectory() as folder:
            config.DB_PATH = Path(folder) / "alerts.sqlite3"
            config.BASELINE_MIN_BARS = 2
            config.BASELINE_SESSIONS = 1
            config.MIN_BASELINE_SESSIONS = 1
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
                config.BASELINE_SESSIONS = original_sessions
                config.MIN_BASELINE_SESSIONS = original_min_sessions
                config.ALERT_CONSECUTIVE_BARS = original_k


if __name__ == "__main__":
    unittest.main()
