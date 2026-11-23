#!/usr/bin/env python3
"""Read-only SLI performance baseline probe.

Does not write to SQLite or stop/restart the collector. During a live session,
expensive endpoint sampling is automatically skipped if the quote writer has a
large backlog; pass --allow-live-load only when an operator explicitly accepts
that additional read load.
"""
from __future__ import annotations

import argparse
import gzip
import inspect
import json
import math
import re
import sqlite3
import statistics
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    from src.sli import config
    DB_PATH = config.DB_PATH
except Exception:
    DB_PATH = ROOT / "database" / "sli.sqlite3"

TARGETS = {
    "overview": "/api/overview?window_minutes=60",
    "intraday-profile": "/api/intraday-profile?window_minutes=60&source=ssi",
    "rankings": "/api/rankings?window_minutes=60&limit=5",
    "market-depth": "/api/market-depth?window_minutes=60&source=ssi",
    "trades": "/api/trades?limit=25",
    "market-breadth": "/api/market-breadth?limit=10000",
    "market-turnover": "/api/market-turnover?limit=10000&metric=value",
    "market-liquidity-intraday": "/api/market-liquidity-intraday?board=HOSE",
    "liquidity-map": "/api/liquidity-map?window_minutes=60",
    "symbols": "/api/symbols?source=ssi",
    "status": "/api/status",
    "market-clock": "/api/market-clock",
}
FRONTEND = (ROOT / "frontend")


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(p * len(ordered)) - 1))]


def request_once(base: str, path: str, timeout: float) -> dict:
    request = urllib.request.Request(base.rstrip("/") + path,
                                     headers={"Accept": "application/json", "Accept-Encoding": "gzip"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            wire = response.read()
            elapsed = time.perf_counter() - started
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(wire)
                compressed = len(wire)
            else:
                body = wire
                compressed = len(gzip.compress(body, compresslevel=6))
            return {"seconds": elapsed, "status": response.status,
                    "raw_bytes": len(body), "compressed_bytes": compressed, "error": None}
    except Exception as exc:  # report failures as data; never alter service state
        return {"seconds": time.perf_counter() - started, "status": None,
                "raw_bytes": 0, "compressed_bytes": 0,
                "error": f"{type(exc).__name__}: {str(exc)[:180]}"}


def read_live_status(base: str, timeout: float) -> dict | None:
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/api/status", timeout=timeout) as response:
            return json.loads(response.read())
    except Exception:
        return None


def endpoint_measurements(base: str, repeat: int, timeout: float, allow_live_load: bool) -> dict:
    live = read_live_status(base, min(timeout, 8))
    queue = int((live or {}).get("queue_depth") or 0)
    lag = float((live or {}).get("persistence_lag_seconds") or 0)
    session = bool((live or {}).get("market_session_expected"))
    guarded = session and (queue >= 5000 or lag >= 120) and not allow_live_load
    selected = list(TARGETS) if not guarded else ["status"]
    result = {"guarded_expensive_requests": guarded, "guard_reason": None,
              "repeat": repeat, "endpoints": {}, "precheck": {
                  "market_session_expected": session, "queue_depth": queue,
                  "persistence_lag_seconds": lag,
                  "collector_status": (live or {}).get("connection_status")}}
    if guarded:
        result["guard_reason"] = "Đang trong phiên và collector có backlog/độ trễ lớn; chỉ đo endpoint nhẹ để tránh tăng tải đọc."
    for name in selected:
        samples = [request_once(base, TARGETS[name], timeout) for _ in range(repeat)]
        times = [sample["seconds"] for sample in samples if sample["status"] == 200]
        result["endpoints"][name] = {
            "successes": len(times), "failures": [s["error"] for s in samples if s["error"]],
            "p50_ms": round(statistics.median(times) * 1000, 1) if times else None,
            "p95_ms": round(percentile(times, .95) * 1000, 1) if times else None,
            "mean_raw_bytes": round(statistics.mean(s["raw_bytes"] for s in samples)) if samples else 0,
            "mean_compressed_bytes": round(statistics.mean(s["compressed_bytes"] for s in samples)) if samples else 0,
        }
    result["not_measured_due_to_live_guard"] = [name for name in TARGETS if name not in selected]
    return result


def sqlite_inventory() -> dict:
    result = {"path": str(DB_PATH), "exists": DB_PATH.exists()}
    if not DB_PATH.exists():
        return result
    result["database_bytes"] = DB_PATH.stat().st_size
    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    # Match the application's current sqlite connection policy (timeout=30s,
    # synchronous=NORMAL) while keeping this probe connection query-only.
    db = sqlite3.connect(uri, uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA synchronous=NORMAL")
    result["pragmas"] = {}
    for name in ("journal_mode", "busy_timeout", "synchronous", "cache_size", "page_size"):
        try:
            result["pragmas"][name] = db.execute(f"PRAGMA {name}").fetchone()[0]
        except sqlite3.Error as exc:
            result["pragmas"][name] = f"ERROR: {exc}"
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    result["tables"] = {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        try:
            count = db.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0]
        except sqlite3.Error as exc:
            count = f"ERROR: {exc}"
        result["tables"][table] = {"rows": count, "indexes": [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? ORDER BY name", (table,))]}
    try:
        result["dbstat_bytes"] = {r[0]: r[1] for r in db.execute(
            "SELECT name,sum(pgsize) FROM dbstat GROUP BY name ORDER BY sum(pgsize) DESC")}
    except sqlite3.Error as exc:
        result["dbstat_bytes"] = {"unsupported": str(exc)}
    # Representative plans for the large time-series tables, with scanned-row
    # estimates based on the relevant table's actual row count.
    probes = {
        "bars_1m_window": ("SELECT symbol,minute_vn,spread_bps FROM bars_1m WHERE source=? AND minute_vn>=? AND minute_vn<? ORDER BY minute_vn,symbol", ("ssi", "2026-11-23T09:00:00+07:00", "2026-11-23T15:00:00+07:00")),
        "observations_recent": ("SELECT id,symbol,received_at_utc FROM observations WHERE source=? ORDER BY received_at_utc DESC LIMIT 100", ("ssi",)),
        "trade_ticks_window": ("SELECT symbol,received_at_vn,price FROM trade_ticks WHERE source=? AND received_at_vn>=? AND received_at_vn<? ORDER BY received_at_vn,id", ("ssi", "2026-11-23T09:00:00", "2026-11-23T15:00:00")),
    }
    result["query_plans"] = {}
    for name, (sql, params) in probes.items():
        try:
            result["query_plans"][name] = [dict(r) for r in db.execute("EXPLAIN QUERY PLAN " + sql, params)]
        except sqlite3.Error as exc:
            result["query_plans"][name] = {"error": str(exc)}
    db.close()
    return result


class SqlTrace:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, sql: str, params, elapsed: float, returned: int):
        self.rows.append({"sql": sql.strip(), "params": params, "elapsed_ms": elapsed * 1000,
                          "rows_returned": returned})


SQL_TRACE = SqlTrace()


class TracedCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        self._probe_sql = str(sql)
        self._probe_params = tuple(parameters) if not isinstance(parameters, dict) else dict(parameters)
        self._probe_start = time.perf_counter()
        self._probe_rows = 0
        self._probe_recorded = False
        super().execute(sql, parameters)
        self._probe_add_elapsed = 0.0
        return self

    def _tick(self, count: int):
        self._probe_add_elapsed += time.perf_counter() - self._probe_start
        self._probe_rows += count
        self._probe_start = time.perf_counter()

    def _finish(self):
        if not getattr(self, "_probe_recorded", True):
            self._probe_add_elapsed += time.perf_counter() - self._probe_start
            SQL_TRACE.add(self._probe_sql, self._probe_params, self._probe_add_elapsed, self._probe_rows)
            self._probe_recorded = True

    def fetchone(self):
        row = super().fetchone()
        self._tick(1 if row is not None else 0)
        if row is None:
            self._finish()
        return row

    def fetchall(self):
        rows = super().fetchall()
        self._tick(len(rows))
        self._finish()
        return rows

    def fetchmany(self, size=None):
        rows = super().fetchmany() if size is None else super().fetchmany(size)
        self._tick(len(rows))
        if len(rows) < (size or self.arraysize):
            self._finish()
        return rows

    def __iter__(self):
        return self

    def __next__(self):
        try:
            row = super().__next__()
        except StopIteration:
            self._finish()
            raise
        self._tick(1)
        return row

    def close(self):
        self._finish()
        return super().close()


class TracedConnection(sqlite3.Connection):
    def cursor(self, *args, **kwargs):
        kwargs["factory"] = TracedCursor
        return super().cursor(*args, **kwargs)

    def execute(self, sql, parameters=()):
        cursor = self.cursor()
        return cursor.execute(sql, parameters)


def sql_profile() -> dict:
    """Trace safe GET handlers in this probe process, using query-only SQLite."""
    import sys
    sys.path.insert(0, str(ROOT))
    from src.sli import api, database

    original_connect = database.connect

    def readonly_connect(timeout=30):
        connection = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=min(float(timeout), 30), factory=TracedConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    database.connect = readonly_connect
    routes = {
        "overview": (api.overview, {"symbol": None, "limit": 240, "window_minutes": 60, "phase": None}),
        "intraday-profile": (api.intraday_profile, {"symbol": None, "window_minutes": 60, "source": "ssi"}),
        "rankings": (api.rankings, {"limit": 5, "window_minutes": 60}),
        "market-depth": (api.market_depth, {"window_minutes": 60}),
        "trades": (api.trades, {"limit": 25}),
        "market-breadth": (api.market_breadth, {"limit": 10000}),
        "market-turnover": (api.market_turnover, {"limit": 10000, "metric": "value"}),
        "market-liquidity-intraday": (api.market_liquidity_intraday, {"board": "HOSE"}),
        "liquidity-map": (api.liquidity_map, {"window_minutes": 60, "source": "ssi"}),
        "symbols": (api.symbols, {}),
    }
    failures = {}
    started = time.perf_counter()
    try:
        for name, (handler, kwargs) in routes.items():
            try:
                handler(**kwargs)
            except Exception as exc:
                failures[name] = f"{type(exc).__name__}: {str(exc)[:160]}"
    finally:
        database.connect = original_connect
    slow = [row for row in SQL_TRACE.rows if row["elapsed_ms"] >= 100]
    table_rows = {}
    for name, value in sqlite_inventory().get("tables", {}).items():
        if isinstance(value.get("rows"), int):
            table_rows[name] = value["rows"]
    for row in slow:
        try:
            with sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True, timeout=30) as plan_db:
                binds = row["params"]
                plan = [tuple(item) for item in plan_db.execute("EXPLAIN QUERY PLAN " + row["sql"], binds)]
            row["explain_query_plan"] = plan
            scans = []
            for item in plan:
                detail = item[-1]
                match = re.search(r"\bSCAN\s+(?:TABLE\s+)?([\w]+)", detail, re.I)
                if match:
                    scans.append({"table": match.group(1), "estimated_rows_full_table": table_rows.get(match.group(1))})
            row["full_scan_estimates"] = scans
        except Exception as exc:
            row["explain_query_plan"] = f"{type(exc).__name__}: {str(exc)[:180]}"
        row.pop("params", None)
    return {"direct_safe_get_handlers_elapsed_seconds": round(time.perf_counter() - started, 2),
            "sql_statement_count": len(SQL_TRACE.rows), "slow_sql_over_100ms": slow,
            "route_failures": failures,
            "note": "SQL được trace trong process riêng với connection query-only; số dòng là dòng trả về. Ước lượng full scan lấy tổng dòng bảng nếu EXPLAIN báo SCAN; SQLite bản này không cung cấp scanstatus chính xác."}


def frontend_inventory() -> dict:
    report = {}
    path_re = re.compile(r"['\"`](/api/[A-Za-z0-9_/${}.?=&-]+)")
    for file in sorted(FRONTEND.glob("*.js")):
        text = file.read_text(encoding="utf-8", errors="replace")
        endpoints = Counter(path_re.findall(text))
        intervals = len(re.findall(r"\bsetInterval\s*\(", text))
        timeouts = len(re.findall(r"\bsetTimeout\s*\(", text))
        sse = len(re.findall(r"new\s+EventSource\s*\(", text))
        hidden_guard = "document.hidden" in text
        route_guard = "sliRoute" in text
        report[file.name] = {"api_paths_static": dict(endpoints), "fetch_calls_static": sum(endpoints.values()),
                             "setInterval": intervals, "setTimeout": timeouts,
                             "EventSource": sse, "gated_by_hidden_tab": hidden_guard,
                             "gated_by_active_route": route_guard,
                             "estimated_requests_per_60s": "dynamic; inspect timers/SSE and route guards"}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--repeat", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--allow-live-load", action="store_true",
                        help="Cho phép benchmark cả endpoint nặng trong phiên live dù DB đang backlog.")
    parser.add_argument("--http-only", action="store_true",
                        help="Chỉ lặp lại HTTP đo độ trễ; bỏ qua quét thống kê DB để giảm I/O.")
    parser.add_argument("--skip-sql-profile", action="store_true",
                        help="Bỏ trace các GET handler trong process riêng.")
    args = parser.parse_args()
    report = {"probe": "read-only", "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "measurements": endpoint_measurements(args.base, max(1, args.repeat), args.timeout, args.allow_live_load)}
    if not args.http_only:
        report["sqlite"] = sqlite_inventory()
        report["frontend_polling"] = frontend_inventory()
        if not args.skip_sql_profile:
            # Do not replay expensive SQL while the live writer is already behind.
            precheck = report["measurements"]["precheck"]
            if report["measurements"]["guarded_expensive_requests"]:
                report["sql_profile"] = {"skipped": True, "reason": report["measurements"]["guard_reason"]}
            else:
                report["sql_profile"] = sql_profile()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
