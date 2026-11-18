"""Normalize archived SSI quote/trade files into the application's canonical DB.

Quote archives only retain one market timestamp, so replay uses that timestamp as
the historical event minute. It never presents replayed rows as current data.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.sli import config, database, market_rules
from src.sli.analytics import number_or_none, validate_observation


def parse_timestamp(value: str | None, session_date: str | None = None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        try:
            stamp = datetime.fromisoformat(f"{session_date}T{text}")
        except (TypeError, ValueError):
            return None
    return stamp.replace(tzinfo=config.TIMEZONE) if stamp.tzinfo is None else stamp.astimezone(config.TIMEZONE)


def make_observation(row, session_date, board):
    stamp = parse_timestamp(row["ts"], session_date)
    if stamp is None or stamp.date().isoformat() != session_date:
        return None
    phase = market_rules.session_phase(stamp, board)
    if phase not in {"CONTINUOUS_AM", "CONTINUOUS_PM"}:
        return None
    observation = {
        "symbol": str(row["symbol"]).upper(),
        "source_timestamp": stamp.isoformat(timespec="milliseconds"),
        "source_ts_vn": stamp.isoformat(timespec="milliseconds"),
        "source_delay_seconds": None,
        # The archive does not retain a separate receive timestamp. Keep the
        # exchange/archive event time for historical minute grouping.
        "received_at_vn": stamp.isoformat(timespec="milliseconds"),
        "received_at_utc": stamp.astimezone(timezone.utc).isoformat(timespec="milliseconds"),
        "market": board,
        "session_phase": phase,
        "ref_price": None,
        "ceiling_price": None,
        "floor_price": None,
        "last_price": number_or_none(row["last_price"]),
        "total_trading_value": number_or_none(row["total_val"]),
        "total_trading_volume": number_or_none(row["total_vol"]),
        "raw_payload": {"archive_source_label": row["src"], "archive_session_date": session_date},
    }
    for side in ("bid", "ask"):
        for level in range(1, 4):
            observation[f"{side}_price_{level}"] = number_or_none(row[f"{side}_price{level}"])
            observation[f"{side}_volume_{level}"] = number_or_none(row[f"{side}_vol{level}"])
    result = validate_observation(observation)
    observation["validation_status"] = result["status"]
    observation["validation_reason"] = result["reason"]
    observation["flags"] = result.get("flags", [])
    return observation


def make_trade(row, session_date):
    received = parse_timestamp(row["ts"], session_date)
    event = parse_timestamp(row["t"], session_date)
    if received is None or received.date().isoformat() != session_date:
        return None
    return {
        "symbol": str(row["symbol"]).upper(),
        "source_timestamp": row["t"],
        "source_ts_vn": event.isoformat(timespec="milliseconds") if event else None,
        "received_at_vn": received.isoformat(timespec="milliseconds"),
        "received_at_utc": received.astimezone(timezone.utc).isoformat(timespec="milliseconds"),
        "price": number_or_none(row["price"]),
        "quantity": number_or_none(row["qty"]),
        "side": row["side"],
        "total_volume": number_or_none(row["total_vol"]),
        "validation_status": "valid" if event else "missing_source_timestamp",
    }


def import_archive(path: Path, catalog: dict[str, dict], dry_run: bool):
    session_date = path.stem
    archive = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    archive.row_factory = sqlite3.Row
    labels = [row[0] for row in archive.execute("SELECT DISTINCT src FROM quote ORDER BY src")]
    symbols = {str(row[0]).upper() for row in archive.execute("SELECT DISTINCT symbol FROM quote WHERE src!='eod'")}
    mapped_symbols = symbols.intersection(catalog)
    boards = {board: sum(catalog[symbol].get("board") == board for symbol in mapped_symbols)
              for board in ("HOSE", "HNX", "UPCOM")}
    with database.connect() as db:
        existing_bars = db.execute(
            "SELECT COUNT(*) FROM bars_1m WHERE source='ssi' AND session_date=?", (session_date,)
        ).fetchone()[0]
        manifest_exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_archive_imports'"
        ).fetchone()
        already_imported = bool(manifest_exists and db.execute(
            "SELECT 1 FROM daily_archive_imports WHERE session_date=?", (session_date,)
        ).fetchone())
    quote_count = archive.execute("SELECT COUNT(*) FROM quote WHERE src!='eod'").fetchone()[0]
    trade_count = archive.execute("SELECT COUNT(*) FROM trade").fetchone()[0] if archive.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade'").fetchone() else 0
    if dry_run:
        print(json.dumps({"date": session_date, "source_labels": labels, "archive_quote_rows": quote_count,
                          "archive_symbols": len(symbols), "catalog_symbols": len(mapped_symbols),
                          "symbols_by_board": boards, "existing_bar_rows": existing_bars,
                          "quote_replay": "skip_existing_bars" if existing_bars else "eligible",
                          "archive_trade_rows": trade_count}, ensure_ascii=False))
        archive.close()
        return
    if already_imported:
        print(json.dumps({"date": session_date, "status": "already_imported", "archive_file": path.name},
                         ensure_ascii=False), flush=True)
        archive.close()
        return

    inserted_quotes = duplicate_quotes = invalid_quotes = 0
    if not existing_bars:
        cursor = archive.execute("SELECT * FROM quote WHERE src!='eod' ORDER BY ts_ms,id")
        batch = []
        scanned = 0
        for row in cursor:
            scanned += 1
            symbol = str(row["symbol"]).upper()
            profile = catalog.get(symbol)
            if not profile:
                continue
            observation = make_observation(row, session_date, profile.get("board") or "HOSE")
            if observation is None:
                continue
            batch.append(observation)
            if len(batch) >= 500:
                counts = database.insert_observation_batch(batch, "ssi", evaluate_alerts=False)
                inserted_quotes += counts["inserted"]
                duplicate_quotes += counts["duplicate"]
                invalid_quotes += counts["invalid"]
                batch.clear()
                if scanned % 25000 < 500:
                    print(f"{session_date}: đã đọc {scanned:,}/{quote_count:,} quote; chuẩn hóa {inserted_quotes:,}", flush=True)
        if batch:
            counts = database.insert_observation_batch(batch, "ssi", evaluate_alerts=False)
            inserted_quotes += counts["inserted"]
            duplicate_quotes += counts["duplicate"]
            invalid_quotes += counts["invalid"]
        database.record_session_close(session_date, "ssi")

    inserted_trades = 0
    if trade_count:
        cursor = archive.execute("SELECT * FROM trade ORDER BY ts_ms,id")
        batch = []
        scanned_trades = 0
        for row in cursor:
            scanned_trades += 1
            if str(row["symbol"]).upper() not in catalog:
                continue
            trade = make_trade(row, session_date)
            if trade is None:
                continue
            batch.append(trade)
            if len(batch) >= 1000:
                inserted_trades += database.insert_trade_batch(batch, "ssi")["inserted"]
                batch.clear()
                if scanned_trades % 50000 < 1000:
                    print(f"{session_date}: đã đọc {scanned_trades:,}/{trade_count:,} giao dịch; chuẩn hóa {inserted_trades:,}", flush=True)
        if batch:
            inserted_trades += database.insert_trade_batch(batch, "ssi")["inserted"]

    with database.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS daily_archive_imports (
            session_date TEXT PRIMARY KEY, archive_file TEXT NOT NULL, source_labels TEXT NOT NULL,
            archive_quote_rows INTEGER NOT NULL, catalog_symbols INTEGER NOT NULL,
            symbols_by_board TEXT NOT NULL, inserted_observations INTEGER NOT NULL,
            duplicate_observations INTEGER NOT NULL, invalid_observations INTEGER NOT NULL,
            inserted_trades INTEGER NOT NULL, timestamp_basis TEXT NOT NULL, imported_at TEXT NOT NULL)""")
        db.execute("""INSERT INTO daily_archive_imports VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_date) DO UPDATE SET archive_file=excluded.archive_file,
            source_labels=excluded.source_labels,archive_quote_rows=excluded.archive_quote_rows,
            catalog_symbols=excluded.catalog_symbols,symbols_by_board=excluded.symbols_by_board,
            inserted_observations=excluded.inserted_observations,duplicate_observations=excluded.duplicate_observations,
            invalid_observations=excluded.invalid_observations,inserted_trades=excluded.inserted_trades,
            timestamp_basis=excluded.timestamp_basis,imported_at=excluded.imported_at""",
            (session_date, path.name, json.dumps(labels, ensure_ascii=False), quote_count,
             len(mapped_symbols), json.dumps(boards, ensure_ascii=False), inserted_quotes,
             duplicate_quotes, invalid_quotes, inserted_trades,
             "SSI/archive event time; quote archive has no independent receive timestamp",
             datetime.now(config.TIMEZONE).isoformat(timespec="seconds")))
    archive.close()
    print(json.dumps({"date": session_date, "source_labels": labels, "symbols_by_board": boards,
                      "inserted_observations": inserted_quotes, "duplicates": duplicate_quotes,
                      "invalid": invalid_quotes, "inserted_trades": inserted_trades,
                      "skipped_quote_replay_existing_bars": bool(existing_bars)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Chỉ báo cáo độ phủ, không ghi database")
    parser.add_argument("--date", action="append", help="Chỉ nhập các ngày YYYY-MM-DD; có thể truyền nhiều lần")
    args = parser.parse_args()
    database.initialize()
    catalog = database.fetch_security_metadata("ssi")
    catalog = {symbol: profile for symbol, profile in catalog.items()
               if str(profile.get("stock_type") or "").lower() == "stock"}
    paths = sorted(config.DB_PATH.parent.glob("20??-??-??.sqlite"))
    if args.date:
        selected = set(args.date)
        paths = [path for path in paths if path.stem in selected]
    for path in paths:
        import_archive(path, catalog, args.dry_run)


if __name__ == "__main__":
    main()
