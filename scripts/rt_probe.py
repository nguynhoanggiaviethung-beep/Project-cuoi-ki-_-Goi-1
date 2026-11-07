#!/usr/bin/env python3
"""Read-only real-time/SQLite probe for SLI.

The DB is opened immutable+read-only: this script never updates records or starts
the web app/collector. Output contains aggregate counts and redacted log metadata.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "database" / "sli.sqlite3"
DEFAULT_LOGS = [ROOT / "logs" / "backend.stdout.log",
                ROOT / "logs" / "backend.stderr.log",
                ROOT / "database" / "server-error.log"]


def connect_readonly(path: Path) -> sqlite3.Connection:
    # immutable avoids lock/WAL side effects during this forensic read.
    uri = "file:" + path.resolve().as_posix() + "?mode=ro&immutable=1"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def table_names(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def table_storage(db, tables):
    result = {}
    try:
        rows = db.execute(
            "SELECT name, SUM(pgsize) AS bytes FROM dbstat GROUP BY name"
        ).fetchall()
        for row in rows:
            if row["name"] in tables:
                result[row["name"]] = int(row["bytes"] or 0)
        return {"method": "dbstat", "tables": result}
    except sqlite3.OperationalError:
        # dbstat is an optional SQLite compile-time extension.
        return {"method": "unavailable; use logical_payload_bytes per table",
                "tables": result}


def logical_table_bytes(db, tables):
    # Rendered value bytes are comparable estimates, not allocated SQLite pages.
    result = {}
    for table in sorted(t for t in tables if not t.startswith("sqlite_")):
        columns = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
        if not columns:
            continue
        terms = "+".join(f'COALESCE(length(CAST("{c}" AS BLOB)),0)' for c in columns)
        result[table] = int(db.execute(
            f'SELECT COALESCE(SUM({terms}),0) FROM "{table}"'
        ).fetchone()[0] or 0)
    return result


def process_alive(pid: int) -> bool | None:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return None
    try:
        p = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, timeout=5, check=False)
        return f'"{pid}"' in p.stdout
    except Exception:
        return None


def read_logs(paths):
    summary = []
    pids = set()
    all_lines = []
    for path in paths:
        if not path.exists():
            summary.append({"file": str(path.relative_to(ROOT)), "exists": False})
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        counts = Counter()
        times = []
        for line in lines:
            low = line.lower()
            if "authentication successful" in low:
                counts["auth_success"] += 1
            if "websocket connected" in low:
                counts["websocket_connected"] += 1
                m = re.search(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?)", line)
                if m:
                    times.append(m.group(1))
            if "websocket" in low and any(x in low for x in ("error", "failed", "closed", "disconnect")):
                counts["websocket_error_or_close"] += 1
            if re.search(r"started server process\s*\[(\d+)\]", low):
                counts["server_process_start"] += 1
                pids.add(int(re.search(r"started server process\s*\[(\d+)\]", low).group(1)))
            if "application shutdown complete" in low:
                counts["server_shutdown_complete"] += 1
            if "error:" in low or "traceback" in low or "exception" in low:
                counts["error_or_exception_lines"] += 1
        all_lines.extend((path.name, line) for line in lines)
        summary.append({"file": str(path.relative_to(ROOT)), "exists": True,
                        "bytes": path.stat().st_size, "line_count": len(lines),
                        "event_counts": dict(counts), "websocket_connected_times": times})
    pid_status = [{"pid": pid, "currently_exists": process_alive(pid)} for pid in sorted(pids)]
    # No raw log lines are returned; only event metadata to avoid leaking credentials.
    return {"files": summary, "server_pids_seen": pid_status,
            "note": "Log contents are summarized; raw lines and credentials are never emitted."}


def run_probe(db_path: Path, log_paths, session_date: str):
    db = connect_readonly(db_path)
    tables = table_names(db)
    page_size = db.execute("PRAGMA page_size").fetchone()[0]
    page_count = db.execute("PRAGMA page_count").fetchone()[0]
    freelist = db.execute("PRAGMA freelist_count").fetchone()[0]
    out = {
        "read_only": True,
        "database_file_bytes": db_path.stat().st_size,
        "sqlite_pages": {"page_size": page_size, "page_count": page_count,
                         "allocated_page_bytes": page_size * page_count,
                         "freelist_pages": freelist},
        "quick_check": db.execute("PRAGMA quick_check").fetchone()[0],
        "table_storage": table_storage(db, tables),
        "logical_table_value_bytes": logical_table_bytes(db, tables),
        "table_rows": {},
        "logical_payload_bytes": {},
    }

    payload_columns = {
        "observations": "raw_payload",
        "metrics": None,
        "bars_1m": None,
        "historical_prices": None,
        "trade_ticks": None,
        "securities_catalog": None,
        "alerts": "explanation",
        "collector_run_details": "last_error",
        "ingestion_errors": "reason",
    }
    for table, col in payload_columns.items():
        if table not in tables:
            continue
        out["table_rows"][table] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if col:
            out["logical_payload_bytes"][table] = db.execute(
                f"SELECT COALESCE(SUM(length({col})),0) FROM {table}"
            ).fetchone()[0]
    if "observations" in tables:
        o = db.execute("""
            SELECT COUNT(*) n,
              SUM(CASE WHEN raw_payload IS NULL OR raw_payload='' THEN 1 ELSE 0 END) missing,
              SUM(CASE WHEN raw_payload='{}' THEN 1 ELSE 0 END) placeholder_empty_object,
              SUM(CASE WHEN raw_payload IS NOT NULL AND raw_payload NOT IN ('','{}') THEN 1 ELSE 0 END) full_payload_rows,
              SUM(COALESCE(length(raw_payload),0)) bytes,
              AVG(length(raw_payload)) avg_bytes,
              MAX(length(raw_payload)) max_bytes
            FROM observations WHERE source='ssi'
        """).fetchone()
        out["ssi_raw_payload"] = {k: o[k] for k in o.keys()}
        out["ssi_observation_rows_by_day"] = [dict(r) for r in db.execute("""
            SELECT substr(received_at_vn,1,10) AS day, COUNT(*) AS rows,
                   MIN(received_at_vn) AS first_received, MAX(received_at_vn) AS last_received
            FROM observations WHERE source='ssi' GROUP BY day ORDER BY day
        """)]

    if "securities_catalog" in tables:
        out["catalog_type_counts"] = [dict(r) for r in db.execute("""
            SELECT COALESCE(stock_type,'(null)') stock_type, COUNT(*) n
            FROM securities_catalog WHERE source='ssi' GROUP BY stock_type ORDER BY n DESC
        """)]

    # Number of populated levels, split by exchange, without assuming quote units.
    out["observed_book_levels_by_board"] = [dict(r) for r in db.execute("""
        SELECT COALESCE(o.market,'(null)') board, COUNT(*) observations,
          SUM(CASE WHEN bid_price_1>0 AND bid_volume_1>0 THEN 1 ELSE 0 END) bid_l1_active,
          SUM(CASE WHEN bid_price_2>0 AND bid_volume_2>0 THEN 1 ELSE 0 END) bid_l2_active,
          SUM(CASE WHEN bid_price_3>0 AND bid_volume_3>0 THEN 1 ELSE 0 END) bid_l3_active,
          SUM(CASE WHEN ask_price_1>0 AND ask_volume_1>0 THEN 1 ELSE 0 END) ask_l1_active,
          SUM(CASE WHEN ask_price_2>0 AND ask_volume_2>0 THEN 1 ELSE 0 END) ask_l2_active,
          SUM(CASE WHEN ask_price_3>0 AND ask_volume_3>0 THEN 1 ELSE 0 END) ask_l3_active
        FROM observations o WHERE source='ssi' GROUP BY o.market ORDER BY board
    """)]

    # Off-tick counts by board, stored-price bucket and catalog security type.
    out["off_tick_by_status"] = [dict(r) for r in db.execute("""
        SELECT validation_status, COUNT(*) n
        FROM observations o
        WHERE source='ssi' AND EXISTS (
          SELECT 1 FROM json_each(COALESCE(o.flags,'[]')) f WHERE f.value='off_tick'
        )
        GROUP BY validation_status ORDER BY n DESC
    """)]
    out["off_tick_by_board_type_price_band"] = [dict(r) for r in db.execute("""
        SELECT COALESCE(o.market,'(null)') board,
               COALESCE(c.stock_type,'unmapped') stock_type,
               CASE
                 WHEN px IS NULL THEN 'unknown'
                 WHEN px < 10000 THEN '<10,000 (stored units)'
                 WHEN px < 20000 THEN '10,000-<20,000 (stored units)'
                 WHEN px < 50000 THEN '20,000-<50,000 (stored units)'
                 WHEN px < 100000 THEN '50,000-<100,000 (stored units)'
                 ELSE '>=100,000 (stored units)'
               END price_band,
               COUNT(*) n
        FROM (
          SELECT o.*,
                 CASE WHEN bid_price_1>0 AND ask_price_1>0
                      THEN (bid_price_1+ask_price_1)/2
                      ELSE COALESCE(bid_price_1,ask_price_1) END px
          FROM observations o
          WHERE source='ssi' AND EXISTS (
            SELECT 1 FROM json_each(COALESCE(o.flags,'[]')) f WHERE f.value='off_tick'
          )
        ) o
        LEFT JOIN securities_catalog c ON c.source=o.source AND c.symbol=o.symbol
        GROUP BY 1,2,3 ORDER BY 1,2,3
    """)]
    out["off_tick_price_unit_note"] = (
        "Price bands use numeric values as stored in SQLite; they are exploratory bins, "
        "not a claim about SSI price units."
    )
    out["off_tick_top_symbols"] = [dict(r) for r in db.execute("""
        SELECT symbol,COUNT(*) AS rows,MIN(bid_price_1) AS min_bid,MAX(bid_price_1) AS max_bid
        FROM observations o WHERE source='ssi' AND EXISTS (
          SELECT 1 FROM json_each(COALESCE(o.flags,'[]')) f WHERE f.value='off_tick'
        ) GROUP BY symbol ORDER BY rows DESC,symbol LIMIT 20
    """)]

    if "bars_1m" in tables:
        out["bar_minute_timestamp_format"] = [dict(r) for r in db.execute("""
            SELECT CASE WHEN instr(minute_vn,'+')>0 OR instr(minute_vn,'Z')>0
                        THEN 'offset_present' ELSE 'offset_missing' END AS form,
                   COUNT(*) AS bars
            FROM bars_1m WHERE source='ssi' AND session_date=? GROUP BY form
        """, (session_date,))]
        out["bar_session_summary"] = [dict(r) for r in db.execute("""
            WITH per_minute AS (
              SELECT market,session_phase,substr(minute_vn,1,16) AS minute_key,
                     COUNT(*) AS symbols_per_minute
              FROM bars_1m WHERE source='ssi' AND session_date=?
              GROUP BY market,session_phase,minute_key
            )
            SELECT p.market,p.session_phase,
                   (SELECT COUNT(DISTINCT b.symbol) FROM bars_1m b
                    WHERE b.source='ssi' AND b.session_date=? AND b.market=p.market
                      AND b.session_phase=p.session_phase) AS symbols,
                   SUM(p.symbols_per_minute) AS bars,COUNT(*) AS minutes,
                   MIN(p.minute_key) AS first_minute,MAX(p.minute_key) AS last_minute,
                   MIN(p.symbols_per_minute) AS min_symbols_per_minute,
                   MAX(p.symbols_per_minute) AS max_symbols_per_minute
            FROM per_minute p GROUP BY p.market,p.session_phase
        """, (session_date, session_date))]
        out["minute_coverage"] = [dict(r) for r in db.execute("""
            SELECT substr(minute_vn,1,16) AS minute_vn,market,session_phase,COUNT(*) symbols,COUNT(*) bars,
                   SUM(n_snapshots) source_snapshots
            FROM bars_1m WHERE source='ssi' AND session_date=?
            GROUP BY substr(minute_vn,1,16),market,session_phase ORDER BY minute_vn,market
        """, (session_date,))]
        # Compare to the project's configured continuous-session windows only;
        # this does not assert that those exchange schedules are current.
        schedules = {
            ("HOSE", "CONTINUOUS_AM"): (time(9, 15), time(11, 30)),
            ("HNX", "CONTINUOUS_AM"): (time(9, 0), time(11, 30)),
            ("UPCOM", "CONTINUOUS_AM"): (time(9, 0), time(11, 30)),
            ("HOSE", "CONTINUOUS_PM"): (time(13, 0), time(14, 30)),
            ("HNX", "CONTINUOUS_PM"): (time(13, 0), time(14, 30)),
            ("UPCOM", "CONTINUOUS_PM"): (time(13, 0), time(15, 0)),
        }
        observed = defaultdict(set)
        symbols_per_minute = defaultdict(list)
        for row in out["minute_coverage"]:
            key = (row["market"], row["session_phase"])
            observed[key].add(row["minute_vn"])
            symbols_per_minute[key].append(row["symbols"])
        d = datetime.strptime(session_date, "%Y-%m-%d").date()
        out["configured_session_coverage"] = []
        for key, (start_t, end_t) in schedules.items():
            start = datetime.combine(d, start_t)
            end = datetime.combine(d, end_t)
            expected = set()
            cur = start
            while cur < end:
                expected.add(cur.strftime("%Y-%m-%dT%H:%M"))
                cur += timedelta(minutes=1)
            have = observed.get(key, set())
            out["configured_session_coverage"].append({
                "market": key[0], "session_phase": key[1],
                "expected_minutes_from_current_code_schedule": len(expected),
                "minutes_with_any_bar": len(have),
                "expected_minutes_with_any_bar": len(expected & have),
                "missing_expected_minutes": len(expected - have),
                "observed_outside_configured_window": sorted(have - expected),
                "symbols_min_per_observed_minute": min(symbols_per_minute[key]) if symbols_per_minute[key] else None,
                "symbols_max_per_observed_minute": max(symbols_per_minute[key]) if symbols_per_minute[key] else None,
            })
        out["bar_symbols_security_type"] = [dict(r) for r in db.execute("""
            SELECT COALESCE(c.stock_type,'unmapped') stock_type,COUNT(DISTINCT b.symbol) symbols,
                   COUNT(*) bars
            FROM bars_1m b LEFT JOIN securities_catalog c
              ON c.source='ssi' AND c.symbol=b.symbol
            WHERE b.source='ssi' AND b.session_date=?
            GROUP BY stock_type ORDER BY symbols DESC
        """, (session_date,))]
        out["bar_symbols_unmapped_sample"] = [r[0] for r in db.execute("""
            SELECT DISTINCT b.symbol FROM bars_1m b
            LEFT JOIN securities_catalog c ON c.source='ssi' AND c.symbol=b.symbol
            WHERE b.source='ssi' AND b.session_date=? AND c.symbol IS NULL
            ORDER BY b.symbol LIMIT 30
        """, (session_date,))]

    if "collector_run_details" in tables:
        out["collector_runs"] = [dict(r) for r in db.execute("""
            SELECT id,started_at,ended_at,status,messages_received,rows_valid,rows_invalid,
                   duplicate_messages,reconnect_count,queue_dropped,last_message_at,last_error
            FROM collector_run_details ORDER BY id
        """)]
        out["collector_run_counts"] = [dict(r) for r in db.execute("""
            SELECT status,COUNT(*) runs,SUM(messages_received) messages,
                   SUM(rows_valid) valid_rows,SUM(rows_invalid) invalid_rows,
                   MIN(started_at) first_started,MAX(started_at) last_started
            FROM collector_run_details GROUP BY status
        """)]

    out["logs"] = read_logs(log_paths)
    db.close()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--session-date", default="2026-11-05", help="VN session date, YYYY-MM-DD")
    ap.add_argument("--log", type=Path, action="append", help="repeat for each log; defaults to known app logs")
    args = ap.parse_args()
    logs = args.log if args.log else DEFAULT_LOGS
    result = run_probe(args.db, logs, args.session_date)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
