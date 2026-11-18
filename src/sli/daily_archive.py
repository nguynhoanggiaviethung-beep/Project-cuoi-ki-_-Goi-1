"""Append the live SSI quote/trade stream to one SQLite file per trading day.

The consolidated ``sli.sqlite3`` remains the application's query database. These
files are an additional, date-partitioned raw archive of the same accepted
collector batches.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime

from . import config


_LOCK = threading.RLock()
_CONNECTIONS: dict[str, sqlite3.Connection] = {}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quote (
    id INTEGER PRIMARY KEY, ts_ms INTEGER NOT NULL, ts TEXT NOT NULL,
    src TEXT NOT NULL, symbol TEXT NOT NULL, session TEXT,
    bid_price1 INTEGER, bid_text1 TEXT, bid_vol1 INTEGER,
    bid_price2 INTEGER, bid_vol2 INTEGER, bid_price3 INTEGER, bid_vol3 INTEGER,
    ask_price1 INTEGER, ask_text1 TEXT, ask_vol1 INTEGER,
    ask_price2 INTEGER, ask_vol2 INTEGER, ask_price3 INTEGER, ask_vol3 INTEGER,
    last_price INTEGER, last_vol INTEGER, total_vol INTEGER, total_val INTEGER,
    avg_price REAL, open_price INTEGER, high_price INTEGER, low_price INTEGER,
    total_bid_qtty INTEGER, total_ask_qtty INTEGER, f_buy_vol INTEGER,
    f_sell_vol INTEGER, f_buy_val INTEGER, f_sell_val INTEGER,
    f_net_val INTEGER, f_remain INTEGER, avg_check INTEGER,
    partial INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_quote_symbol_ts ON quote(symbol, ts_ms);
CREATE TABLE IF NOT EXISTS trade (
    id INTEGER PRIMARY KEY, ts_ms INTEGER NOT NULL, ts TEXT NOT NULL,
    src TEXT NOT NULL, symbol TEXT NOT NULL, t TEXT,
    price INTEGER, qty INTEGER, side TEXT, total_vol INTEGER
);
CREATE INDEX IF NOT EXISTS ix_trade_symbol_ts ON trade(symbol, ts_ms);
CREATE TABLE IF NOT EXISTS raw (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, src TEXT NOT NULL,
    channel TEXT, item_key TEXT, reason TEXT, payload TEXT NOT NULL
);
"""


def _as_local_datetime(value: str | None) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=config.TIMEZONE)
            return parsed.astimezone(config.TIMEZONE)
        except (TypeError, ValueError):
            pass
    return datetime.now(config.TIMEZONE)


def _ts_ms(value: str | None) -> int:
    return int(_as_local_datetime(value).timestamp() * 1000)


def _payload_text(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"), default=str)


def _text_or_none(value):
    if value is None:
        return None
    return value if isinstance(value, str) else _payload_text(value)


def _connection(session_date: str) -> sqlite3.Connection:
    connection = _CONNECTIONS.get(session_date)
    if connection is not None:
        return connection
    path = config.DB_PATH.parent / f"{session_date}.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(_SCHEMA)
    connection.executemany(
        "INSERT OR IGNORE INTO meta(key,value) VALUES (?,?)",
        (("schema_version", "1"), ("trading_date", session_date)),
    )
    connection.commit()
    _CONNECTIONS[session_date] = connection
    return connection


def persist_quote_batch(rows: list[dict]) -> list[str]:
    """Persist exactly the quote rows passed to the primary DB writer."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        received = _as_local_datetime(row.get("received_at_vn"))
        grouped.setdefault(received.date().isoformat(), []).append(row)
    paths: list[str] = []
    with _LOCK:
        for session_date, batch in grouped.items():
            db = _connection(session_date)
            db.executemany(
                """INSERT INTO quote(
                    ts_ms,ts,src,symbol,session,
                    bid_price1,bid_vol1,bid_price2,bid_vol2,bid_price3,bid_vol3,
                    ask_price1,ask_vol1,ask_price2,ask_vol2,ask_price3,ask_vol3,
                    last_price,total_vol,total_val,avg_check,partial
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(
                    _ts_ms(row.get("source_ts_vn") or row.get("received_at_vn")),
                    row.get("source_ts_vn") or row.get("received_at_vn") or "",
                    f"ssi_{str(row.get('market') or 'unknown').lower()}",
                    row.get("symbol"), _text_or_none(row.get("session_phase")),
                    row.get("bid_price_1"), row.get("bid_volume_1"),
                    row.get("bid_price_2"), row.get("bid_volume_2"),
                    row.get("bid_price_3"), row.get("bid_volume_3"),
                    row.get("ask_price_1"), row.get("ask_volume_1"),
                    row.get("ask_price_2"), row.get("ask_volume_2"),
                    row.get("ask_price_3"), row.get("ask_volume_3"),
                    row.get("last_price"), row.get("total_trading_volume"),
                    row.get("total_trading_value"),
                    int(row.get("last_price") is not None),
                    int(row.get("validation_status") != "valid"),
                ) for row in batch],
            )
            invalid = [row for row in batch if row.get("validation_status") != "valid"]
            if invalid:
                db.executemany(
                    "INSERT INTO raw(ts,src,channel,item_key,reason,payload) VALUES (?,?,?,?,?,?)",
                    [(
                        row.get("received_at_vn") or "", "ssi", "quote",
                        row.get("symbol"), row.get("validation_reason") or row.get("validation_status"),
                        _payload_text(row.get("raw_payload")),
                    ) for row in invalid],
                )
            db.commit()
            paths.append(str(config.DB_PATH.parent / f"{session_date}.sqlite"))
    return paths


def persist_trade_batch(rows: list[dict]) -> list[str]:
    """Persist validated trade ticks into their local trading-date files."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        received = _as_local_datetime(row.get("received_at_vn"))
        grouped.setdefault(received.date().isoformat(), []).append(row)
    paths: list[str] = []
    with _LOCK:
        for session_date, batch in grouped.items():
            db = _connection(session_date)
            db.executemany(
                "INSERT INTO trade(ts_ms,ts,src,symbol,t,price,qty,side,total_vol) VALUES (?,?,?,?,?,?,?,?,?)",
                [(
                    _ts_ms(row.get("received_at_vn")), row.get("received_at_vn") or "",
                    "ssi", row.get("symbol"), row.get("source_ts_vn"),
                    row.get("price"), row.get("quantity"), row.get("side"),
                    row.get("total_volume"),
                ) for row in batch],
            )
            db.commit()
            paths.append(str(config.DB_PATH.parent / f"{session_date}.sqlite"))
    return paths


def close_all() -> None:
    with _LOCK:
        for connection in _CONNECTIONS.values():
            try:
                connection.close()
            except sqlite3.Error:
                pass
        _CONNECTIONS.clear()
