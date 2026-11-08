"""Shared market context calculations from stored SSI daily OHLC only."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from . import config, database


def build_turnover_series(rows: Iterable[dict], metric: str = "value", *,
                          spike_multiplier: float | None = None) -> dict:
    """Build causal 20-session statistics; no value is inferred from price/volume."""
    if metric not in {"value", "volume"}:
        raise ValueError("metric must be 'value' or 'volume'")
    factor = config.MARKET_TURNOVER_SPIKE_MULTIPLIER if spike_multiplier is None else float(spike_multiplier)
    raw_key = "total_value" if metric == "value" else "total_volume"
    scale = 1e12 if metric == "value" else 1e6
    unit = "nghìn tỷ đồng" if metric == "value" else "triệu cổ phiếu"
    normalized = []
    for source_row in rows:
        row = dict(source_row)
        raw = row.get(raw_key)
        value = float(raw) / scale if raw is not None else None
        prior = [item["value"] for item in normalized[-19:] if item["value"] is not None]
        ma20 = (sum(prior + [value]) / 20) if value is not None and len(prior) == 19 else None
        normalized.append({**row, "value": value, "ma20": ma20,
                           "ratio_to_ma20_pct": value / ma20 * 100 if value is not None and ma20 else None,
                           "is_spike": bool(ma20 and value is not None and value >= factor * ma20),
                           "unit": unit})

    usable = [row for row in normalized if row["value"] is not None]
    latest = usable[-1] if usable else None
    latest_ma = latest["ma20"] if latest else None
    lower = sum(1 for row in usable[:-1] if latest and row["value"] < latest["value"])
    compared = len(usable) - 1
    percentile = (lower / compared * 100) if compared else None
    return {
        "metric": metric,
        "unit": unit,
        "rows": normalized,
        "latest": latest,
        "kpis": {
            "latest_value": latest["value"] if latest else None,
            "latest_date": latest["trading_date"] if latest else None,
            "average20": latest_ma,
            "ratio_to_average20_pct": latest["value"] / latest_ma * 100 if latest and latest_ma else None,
            "higher_than_pct": percentile,
            "sessions_in_window": len(usable),
        },
    }


def get_market_turnover(*, limit: int = 60, metric: str = "value", market_meta: dict | None = None,
                        start_date: str | None = None, end_date: str | None = None) -> dict:
    """Return SSI-only market context and mark any unsynced current session."""
    rows = database.fetch_market_ohlc_sessions(limit=limit, source="ssi", start_date=start_date, end_date=end_date)
    meta = market_meta or {}
    sync_status = (meta.get("session_sync") or {}).get("status")
    pending_date = meta.get("session_date") if (
        meta.get("state") in {"IN_SESSION", "LUNCH_BREAK"}
        or (meta.get("state") == "CLOSED" and meta.get("closed_reason") == "after_close")
        or (meta.get("state") == "CLOSED" and sync_status not in {None, "complete", "not_started"})
    ) else None
    if meta.get("state") == "CLOSED" and pending_date and sync_status != "complete":
        rows = [row for row in rows
                if str(row.get("trading_date", "")).replace("/", "-") != pending_date.replace("/", "-")]
    result = build_turnover_series(rows, metric)
    metric_key = "total_value" if metric == "value" else "total_volume"
    has_current = bool(pending_date and any(
        str(row.get("trading_date", "")).replace("-", "/") == pending_date.replace("-", "/")
        and row.get(metric_key) is not None for row in rows
    ))
    in_progress = meta.get("state") in {"IN_SESSION", "LUNCH_BREAK"}
    # An in-session OHLC row is provisional only while the shared clock says open.
    for row in result["rows"]:
        row["is_incomplete"] = bool(in_progress and has_current and str(row.get("trading_date", "")).replace("-", "/") == pending_date.replace("-", "/"))
    latest = result["latest"]
    as_of_values = [row.get("data_as_of") for row in rows if row.get("data_as_of")]
    try:
        as_of = max(as_of_values, key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))) if as_of_values else None
    except (ValueError, TypeError):
        as_of = max(as_of_values) if as_of_values else None
    expected_missing = bool(pending_date and not has_current)
    return {
        **result,
        "source": "ssi",
        "as_of": as_of,
        "session_date": latest.get("trading_date") if latest else None,
        "pending_session_date": pending_date,
        "pending_ohlc_missing": expected_missing,
        "note": (f"Dữ liệu OHLC ngày {pending_date} chưa được đồng bộ từ SSI." if expected_missing
                 else "Dữ liệu giá theo ngày từ OHLC SSI, không phải thanh khoản sổ lệnh."),
    }
