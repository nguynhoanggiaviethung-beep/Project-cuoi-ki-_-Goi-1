"""Shared ranking calculations for the API and audit tooling."""
import re
from statistics import median

from .market_rules import tick_size

COVERED_WARRANT = re.compile(r"^C[A-Z]{3}\d{4}$")
COMMON_STOCK_TYPES = {"stock", "common stock", "equity", "ordinary share", "common equity"}


def is_rankable_stock(symbol, stock_type=None):
    """Keep catalogued common stocks; conservatively reject known non-stocks."""
    ticker = str(symbol or "").strip().upper()
    if not ticker or COVERED_WARRANT.fullmatch(ticker):
        return False
    if stock_type is not None and str(stock_type).strip():
        return str(stock_type).strip().lower() in COMMON_STOCK_TYPES
    # Fallback for older catalog rows without a type: known warrant pattern only.
    return True


def calculate_symbol_row(symbol, bars, metadata):
    """Summarize valid completed 1-minute bars for one symbol."""
    usable = [row for row in bars if row.get("coverage_status") == "ok"
              and row.get("session_phase") in {"CONTINUOUS_AM", "CONTINUOUS_PM"}
              and row.get("spread_bps") is not None and row.get("spread_bps") > 0
              and row.get("total_depth_value") is not None and row.get("total_depth_value") > 0
              and row.get("spread_ticks") is not None and row.get("spread_ticks") > 0]
    if not usable:
        return None
    spreads = [float(row["spread_bps"]) for row in usable]
    ticks = [float(row["spread_ticks"]) for row in usable]
    depths = [float(row["total_depth_value"]) for row in usable]
    profile = metadata.get(symbol, {})
    reference_price = profile.get("latest_close")
    board = profile.get("board")
    tick = tick_size(float(reference_price), board or "HOSE") if reference_price is not None else None
    snapshot_count = sum(max(0, int(row.get("n_snapshots") or 0)) for row in usable)
    last = usable[-1]
    median_spread = median(spreads)
    median_depth = median(depths)
    return {
        "symbol": symbol, "board": board, "stock_type": profile.get("stock_type"),
        "reference_price": reference_price, "latest_close": reference_price,
        "tick_size": tick, "snapshot_count": snapshot_count,
        "sample_count": len(usable), "bar_count": len(usable), "sample_kind": "bars_1m",
        "median_spread_bps": median_spread, "median_spread_ticks": median(ticks),
        "spread_ticks": median(ticks), "median_total_depth_value": median_depth,
        "latest_spread_bps": last.get("spread_bps"),
        "latest_total_depth_value": last.get("total_depth_value"),
        "spread_expansion_bps": float(last["spread_bps"]) - median_spread,
        "depth_change_pct": (float(last["total_depth_value"]) / median_depth - 1) * 100,
        "window_bar_count": len(usable),
    }


def rank_rows(rows, minimum_bars):
    eligible = [row for row in rows if (row.get("bar_count", 0) if row.get("sample_kind") == "bars_1m"
                                        or "bar_count" in row and row.get("sample_count") is None
                                        else row.get("sample_count", 0)) >= minimum_bars]
    low_spread = sorted(eligible, key=lambda row: (
        row.get("spread_ticks", row.get("median_spread_ticks")) if row.get("spread_ticks", row.get("median_spread_ticks")) is not None else float("inf"),
        row["median_spread_bps"], -row["median_total_depth_value"], row["symbol"]))
    high_depth = sorted(eligible, key=lambda row: (-row["median_total_depth_value"], row["symbol"]))
    widening = sorted(eligible, key=lambda row: (-row["spread_expansion_bps"], row["symbol"]))
    falling = sorted(eligible, key=lambda row: (row["depth_change_pct"], row["symbol"]))
    one_tick = sum(row.get("spread_ticks", row.get("median_spread_ticks")) is not None
                   and abs(row.get("spread_ticks", row.get("median_spread_ticks")) - 1) < 1e-9 for row in eligible)
    ties = {}
    for row in eligible:
        ticks = row.get("spread_ticks", row.get("median_spread_ticks"))
        if ticks is None:
            continue
        key = round(ticks, 8)
        ties[key] = ties.get(key, 0) + 1
    for row in low_spread:
        ticks = row.get("spread_ticks", row.get("median_spread_ticks"))
        row["one_tick_tie"] = ticks is not None and abs(ticks - 1) < 1e-9 and ties.get(1.0, 0) > 1
    return {"eligible": eligible, "lowest_spread": low_spread, "highest_depth": high_depth,
            "spread_widening": widening, "depth_falling": falling,
            "one_tick_count": one_tick, "one_tick_share": one_tick / len(eligible) if eligible else None}

def attach_rank_movement(current_rows, previous_rows):
    """Attach 1-based ranks and movement versus an ordered historical ranking."""
    previous_rank={row["symbol"]:index+1 for index,row in enumerate(previous_rows)}
    result=[]
    for index,row in enumerate(current_rows):
        item=dict(row)
        current_rank=index+1
        prior=previous_rank.get(item["symbol"])
        item["rank"]=current_rank
        item["previous_rank"]=prior
        item["rank_change"]=(prior-current_rank) if prior is not None else None
        result.append(item)
    return result
