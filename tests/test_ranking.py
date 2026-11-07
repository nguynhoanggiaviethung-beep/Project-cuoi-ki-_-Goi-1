import unittest

from src.sli.ranking import calculate_symbol_row, is_rankable_stock, rank_rows


class RankingTests(unittest.TestCase):
    def test_excludes_covered_warrants_and_non_stock_types(self):
        for code in ("CVIC2516", "CMSN2623", "CTPB2605", "CVJC2605"):
            self.assertFalse(is_rankable_stock(code, "Stock"))
        self.assertFalse(is_rankable_stock("E1VFVN30", "ETF"))
        self.assertFalse(is_rankable_stock("VDS", "Bond"))
        self.assertTrue(is_rankable_stock("VDS", "Stock"))

    def test_uses_only_complete_continuous_positive_bars(self):
        bars = [
            {"coverage_status": "ok", "session_phase": "CONTINUOUS_AM", "spread_bps": 10.0,
             "spread_ticks": 1.0, "total_depth_value": 1_000_000.0, "n_snapshots": 3},
            {"coverage_status": "sparse", "session_phase": "CONTINUOUS_AM", "spread_bps": 20.0,
             "spread_ticks": 2.0, "total_depth_value": 2_000_000.0, "n_snapshots": 1},
            {"coverage_status": "ok", "session_phase": "ATC", "spread_bps": 20.0,
             "spread_ticks": 2.0, "total_depth_value": 2_000_000.0, "n_snapshots": 1},
            {"coverage_status": "ok", "session_phase": "CONTINUOUS_PM", "spread_bps": 0.0,
             "spread_ticks": 0.0, "total_depth_value": 2_000_000.0, "n_snapshots": 1},
            {"coverage_status": "ok", "session_phase": "CONTINUOUS_PM", "spread_bps": 12.0,
             "spread_ticks": 1.0, "total_depth_value": -1.0, "n_snapshots": 1},
        ]
        row = calculate_symbol_row("VDS", bars, {"VDS": {"board": "HOSE", "stock_type": "Stock", "latest_close": 25_000}})
        self.assertEqual(row["bar_count"], 1)
        self.assertEqual(row["snapshot_count"], 3)
        self.assertEqual(row["spread_ticks"], 1.0)
        self.assertEqual(row["tick_size"], 50.0)

    def test_threshold_sort_and_one_tick_ties(self):
        rows = [
            {"symbol": "B", "bar_count": 30, "spread_ticks": 1.0, "median_spread_ticks": 1.0,
             "median_spread_bps": 10.2, "median_total_depth_value": 10,"spread_expansion_bps":1,"depth_change_pct":0},
            {"symbol": "A", "bar_count": 30, "spread_ticks": 1.0, "median_spread_ticks": 1.0,
             "median_spread_bps": 10.2, "median_total_depth_value": 20,"spread_expansion_bps":1,"depth_change_pct":0},
            {"symbol": "C", "bar_count": 29, "spread_ticks": 0.5, "median_spread_ticks": 0.5,
             "median_spread_bps": 4.0, "median_total_depth_value": 30,"spread_expansion_bps":1,"depth_change_pct":0},
            {"symbol": "D", "bar_count": 30, "spread_ticks": 2.0, "median_spread_ticks": 2.0,
             "median_spread_bps": 5.0, "median_total_depth_value": 40,"spread_expansion_bps":1,"depth_change_pct":0},
        ]
        result = rank_rows(rows, 30)
        self.assertEqual([r["symbol"] for r in result["lowest_spread"]], ["A", "B", "D"])
        self.assertTrue(result["lowest_spread"][0]["one_tick_tie"])
        self.assertEqual(result["one_tick_count"], 2)
        self.assertEqual(len(result["eligible"]), 3)


if __name__ == "__main__":
    unittest.main()
