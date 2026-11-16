import json
import math
import sqlite3
from functools import lru_cache
from datetime import datetime, timedelta, timezone

from . import config
from . import market_rules
from .analytics import calculate_metrics, payload_hash, quantile, sanitize_payload

OBS_FIELDS = (
    "bid_price_1", "bid_volume_1", "bid_price_2", "bid_volume_2",
    "bid_price_3", "bid_volume_3", "ask_price_1", "ask_volume_1",
    "ask_price_2", "ask_volume_2", "ask_price_3", "ask_volume_3",
)
METRIC_FIELDS = (
    "mid_price", "spread_abs", "spread_bps", "spread_ticks", "tick_size", "bid_depth_qty", "ask_depth_qty",
    "total_depth_qty", "bid_depth_value", "ask_depth_value",
    "total_depth_value", "queue_imbalance", "value_imbalance",
    "depth_levels_used", "calculated_at",
)


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def start_collector_run(worker_id=None, process_id=None):
    now = datetime.now(config.TIMEZONE).isoformat(timespec="seconds")
    with connect() as db:
        cursor = db.execute("INSERT INTO collector_run_details(started_at,status,worker_id,process_id,heartbeat_at) VALUES (?, 'connecting',?,?,?)",
                            (now, worker_id, process_id, now))
        return cursor.lastrowid


def set_collector_run_status(run_id, status):
    if not run_id:
        return
    with connect() as db:
        db.execute("UPDATE collector_run_details SET status=? WHERE id=?", (status, run_id))


def finish_collector_run(run_id, status, state):
    if not run_id:
        return
    with connect() as db:
        db.execute("""UPDATE collector_run_details SET ended_at=?,heartbeat_at=?,status=?,connection_status=?,messages_received=?,rows_valid=?,
            rows_invalid=?,duplicate_messages=?,reconnect_count=?,queue_dropped=?,last_message_at=?,last_error=? WHERE id=?""",
            (datetime.now(config.TIMEZONE).isoformat(timespec="seconds"), datetime.now(config.TIMEZONE).isoformat(timespec="seconds"), status,
             state.get("connection_status"),
             state.get("messages_received", 0), state.get("rows_valid", 0), state.get("rows_invalid", 0),
             state.get("duplicate_messages", 0), state.get("reconnect_count", 0), state.get("queue_dropped", 0),
             state.get("last_message_at"), state.get("last_error"), run_id))


def update_collector_heartbeat(run_id, state):
    if not run_id:
        return
    now = datetime.now(config.TIMEZONE).isoformat(timespec="seconds")
    with connect() as db:
        db.execute("""UPDATE collector_run_details SET heartbeat_at=?,connection_status=?,messages_received=?,rows_valid=?,
            rows_invalid=?,duplicate_messages=?,reconnect_count=?,queue_dropped=?,last_message_at=?,last_error=?
            WHERE id=? AND ended_at IS NULL""",
            (now, state.get("connection_status"), state.get("messages_received", 0), state.get("rows_valid", 0),
             state.get("rows_invalid", 0), state.get("duplicate_messages", 0),
             state.get("reconnect_count", 0), state.get("queue_dropped", 0),
             state.get("last_message_at"), state.get("last_error"), run_id))


def close_abandoned_collector_runs():
    now = datetime.now(config.TIMEZONE).isoformat(timespec="seconds")
    with connect() as db:
        cursor = db.execute("""UPDATE collector_run_details SET ended_at=?,heartbeat_at=?,status='abandoned',
            last_error=COALESCE(last_error,'Worker trước đã dừng mà không đóng phiên thu thập.')
            WHERE ended_at IS NULL""", (now, now))
        return cursor.rowcount


def latest_collector_run():
    with connect() as db:
        row = db.execute("SELECT * FROM collector_run_details ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def request_collector_stop(worker_id):
    if not worker_id:
        return False
    with connect() as db:
        db.execute("""INSERT INTO collector_worker_control(worker_id,command,requested_at)
            VALUES (?,'stop',?) ON CONFLICT(worker_id) DO UPDATE SET command='stop',requested_at=excluded.requested_at""",
            (worker_id, datetime.now(config.TIMEZONE).isoformat(timespec="seconds")))
    return True


def collector_stop_requested(worker_id):
    with connect() as db:
        row = db.execute("SELECT command FROM collector_worker_control WHERE worker_id=?", (worker_id,)).fetchone()
    return bool(row and row["command"] == "stop")


def fetch_bars(source, symbol=None, limit=1000, start_at=None, end_at=None, bucket=None):
    query = "SELECT * FROM bars_1m WHERE source=?"
    args = [source]
    if symbol:
        query += " AND symbol=?"
        args.append(symbol.upper())
    if start_at:
        query += " AND datetime(minute_vn)>=datetime(?)"
        args.append(start_at)
    if end_at:
        query += " AND datetime(minute_vn)<=datetime(?)"
        args.append(end_at)
    if bucket:
        query += " AND baseline_bucket=?"
        args.append(bucket.upper())
    query += " ORDER BY minute_vn DESC LIMIT ?"
    args.append(max(1, min(int(limit), 50000)))
    with connect() as db:
        rows = db.execute(query, args).fetchall()
    return [dict(row) for row in reversed(rows)]


def is_session_open(session_date, market, db=None):
    owns = db is None
    db = db or connect()
    try:
        row = db.execute("SELECT is_open FROM session_calendar WHERE session_date=? AND market IN (?, 'ALL') ORDER BY market=? DESC LIMIT 1",
                         (str(session_date), market.upper(), market.upper())).fetchone()
        if row is not None:
            return bool(row[0])
        if str(session_date) in config.MARKET_HOLIDAYS:
            return False
        return datetime.fromisoformat(str(session_date)).weekday() < 5
    finally:
        if owns:
            db.close()


def session_calendar_overrides(session_date, through_date=None):
    """Explicit session-calendar rows keyed by date; absent dates use defaults."""
    with connect() as db:
        if through_date:
            rows=db.execute("SELECT session_date,market,is_open FROM session_calendar WHERE session_date BETWEEN ? AND ?",
                            (str(session_date),str(through_date))).fetchall()
        else:
            rows=db.execute("SELECT session_date,market,is_open FROM session_calendar WHERE session_date=?",(str(session_date),)).fetchall()
    result={}
    for row in rows:
        result.setdefault(str(row["session_date"]),{})[str(row["market"]).upper()]=bool(row["is_open"])
    return result


def set_session_day(session_date, market, is_open, note=None):
    with connect() as db:
        db.execute("INSERT INTO session_calendar(session_date,market,is_open,note) VALUES (?,?,?,?) "
                   "ON CONFLICT(session_date,market) DO UPDATE SET is_open=excluded.is_open,note=excluded.note",
                   (str(session_date), market.upper(), int(is_open), note))


def recent_collector_runs(limit=20):
    with connect() as db:
        rows = db.execute("SELECT * FROM collector_run_details ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
    return [dict(row) for row in rows]


def log_ingestion_error(reason, payload=None, symbol=None, source="live"):
    clean = sanitize_payload(payload) if payload is not None else None
    with connect() as db:
        db.execute("INSERT INTO ingestion_errors(source,received_at_vn,symbol,reason,sanitized_payload) VALUES (?,?,?,?,?)",
                   (source, datetime.now(config.TIMEZONE).isoformat(timespec="milliseconds"), symbol,
                    str(reason)[:240], json.dumps(clean, ensure_ascii=False) if clean is not None else None))


def data_quality_summary(source):
    with connect() as db:
        total = db.execute("SELECT COUNT(*) FROM observations WHERE source=?", (source,)).fetchone()[0]
        valid = db.execute("SELECT COUNT(*) FROM observations WHERE source=? AND validation_status='valid'", (source,)).fetchone()[0]
        no_source_time = db.execute("SELECT COUNT(*) FROM observations WHERE source=? AND source_ts_vn IS NULL", (source,)).fetchone()[0]
        delays = db.execute("SELECT AVG(source_delay_seconds),MAX(source_delay_seconds) FROM observations WHERE source=? AND source_delay_seconds IS NOT NULL", (source,)).fetchone()
        errors = db.execute("SELECT COUNT(*) FROM ingestion_errors WHERE source=?", (source,)).fetchone()[0]
        symbols = db.execute("SELECT symbol,COUNT(*) AS n FROM observations WHERE source=? GROUP BY symbol ORDER BY symbol", (source,)).fetchall()
        flag_rows = db.execute("SELECT flags,validation_reason FROM observations WHERE source=? AND validation_status!='valid'", (source,)).fetchall()
        flag_rows_all = db.execute("SELECT j.value,COUNT(*) AS n FROM observations o,json_each(o.flags) j WHERE o.source=? GROUP BY j.value", (source,)).fetchall()
    flag_counts = {}
    for row in flag_rows_all:flag_counts[row["value"]]=row["n"]
    reason_counts={"missing_bid_ask":0,"crossed_book":0,"auction_phase":0,"one_sided_limit":0,"off_tick":0,"other_invalid":0}
    for row in flag_rows:
        flags=set(json.loads(row[0] or "[]"));reason=str(row[1] or "")
        missing_quote=reason=="missing_best_quote_or_depth" or reason in {"bid1_not_positive","ask1_not_positive"}
        if missing_quote:
            reason_counts["missing_bid_ask"]+=1
        for flag,key in (("crossed_book","crossed_book"),("auction_phase","auction_phase"),
                         ("one_sided_limit","one_sided_limit"),("off_tick","off_tick")):
            if flag in flags or flag in reason:reason_counts[key]+=1
        if not flags and not missing_quote:reason_counts["other_invalid"]+=1
    return {"source": source, "observations": total, "valid_observations": valid,
            "valid_rate": valid / total if total else None, "missing_source_timestamp": no_source_time,
            "ingestion_errors": errors, "flags": flag_counts,
            "late_messages": flag_counts.get("late_message", 0),
            "future_source_timestamps": flag_counts.get("future_source_timestamp", 0),
            "invalid_source_timestamps": flag_counts.get("invalid_source_timestamp", 0),
            "average_event_delay_seconds": delays[0], "maximum_event_delay_seconds": delays[1],
            "quality_reasons":reason_counts,
            "quality_reason_note":"Các nhóm kiểm tra có thể chồng lấp; một snapshot có thể có nhiều lý do.",
            "symbols": [{"symbol": row["symbol"], "observations": row["n"]} for row in symbols]}


def previous_bar_session(source, session_date):
    """Return the latest stored bar session strictly before session_date."""
    with connect() as db:
        row=db.execute("SELECT MAX(session_date) FROM bars_1m WHERE source=? AND session_date<?",
                       (source,str(session_date)[:10])).fetchone()
    return row[0] if row and row[0] else None


def fetch_bars_window_by_symbol(source, start_at, end_before, max_minutes=390):
    """Fetch completed minute bars for the same wall-clock window across symbols."""
    max_minutes=max(1,min(int(max_minutes),390))
    sql="""WITH window_rows AS (
        SELECT * FROM bars_1m WHERE source=? AND minute_vn>=? AND minute_vn<?
    ), ranked AS (
        SELECT *,ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY minute_vn DESC) rn
        FROM window_rows
    ) SELECT * FROM ranked WHERE rn<=? ORDER BY symbol,minute_vn"""
    with connect() as db:rows=db.execute(sql,(source,start_at,end_before,max_minutes)).fetchall()
    grouped={}
    for row in rows:grouped.setdefault(row["symbol"],[]).append(dict(row))
    return grouped


def fetch_latest_quote_details(source, symbols):
    """Read the latest persisted quote/trade values for selected map symbols."""
    selected=sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    if not selected:
        return {}
    requested=','.join('( ? )' for _ in selected)
    sql=f"""WITH requested(symbol) AS (VALUES {requested}),
    quote_ids AS (
      SELECT r.symbol,(SELECT o.id FROM observations o
        WHERE o.source=? AND o.validation_status='valid' AND o.symbol=r.symbol
        ORDER BY o.received_at_vn DESC,o.id DESC LIMIT 1) AS observation_id
      FROM requested r
    ), trade_ids AS (
      SELECT r.symbol,(SELECT t.id FROM trade_ticks t
        WHERE t.source=? AND t.validation_status='valid' AND t.symbol=r.symbol
        ORDER BY t.received_at_vn DESC,t.id DESC LIMIT 1) AS trade_id
      FROM requested r
    ) SELECT r.symbol,COALESCE(o.last_price,t.price) AS last_price,m.tick_size,
        CASE WHEN o.last_price IS NOT NULL THEN COALESCE(o.source_ts_vn,o.received_at_vn,o.received_at_utc)
             ELSE COALESCE(t.source_ts_vn,t.received_at_vn,t.received_at_utc) END AS price_as_of,
        CASE WHEN o.last_price IS NOT NULL THEN 'quote' WHEN t.price IS NOT NULL THEN 'trade' END AS price_source
      FROM requested r
      LEFT JOIN quote_ids q ON q.symbol=r.symbol
      LEFT JOIN observations o ON o.id=q.observation_id
      LEFT JOIN metrics m ON m.observation_id=o.id
      LEFT JOIN trade_ids ti ON ti.symbol=r.symbol
      LEFT JOIN trade_ticks t ON t.id=ti.trade_id"""
    params=(*selected,source,source)
    with connect() as db:
        rows=db.execute(sql,params).fetchall()
    return {row['symbol']:dict(row) for row in rows}

def latest_bar_minute(source):
    with connect() as db:
        row=db.execute("SELECT MAX(minute_vn) FROM bars_1m WHERE source=?",(source,)).fetchone()
    return row[0] if row and row[0] else None


def latest_historical_date(source="ssi"):
    # Ask the indexed per-symbol date range for each catalogued common stock.
    # MAX(replace(trading_date,...)) forced a full scan of millions of OHLC rows.
    with connect() as db:
        row=db.execute("""SELECT MAX(last_date) FROM (
            SELECT (SELECT MAX(h.trading_date) FROM historical_prices h
                    WHERE h.source=c.source AND h.symbol=c.symbol AND h.timeframe='1d') AS last_date
            FROM securities_catalog c
            WHERE c.source=? AND lower(trim(c.stock_type))='stock'
        )""",(source,)).fetchone()
    return str(row[0]).replace("/","-") if row and row[0] else None


def historical_date_bounds(source="ssi"):
    """Earliest and latest stored daily OHLC date for ordinary listed stocks."""
    with connect() as db:
        row=db.execute("""SELECT MIN(first_date) AS earliest_date,MAX(last_date) AS latest_date
            FROM (
              SELECT
                (SELECT MIN(h.trading_date) FROM historical_prices h
                 WHERE h.source=c.source AND h.symbol=c.symbol AND h.timeframe='1d') AS first_date,
                (SELECT MAX(h.trading_date) FROM historical_prices h
                 WHERE h.source=c.source AND h.symbol=c.symbol AND h.timeframe='1d') AS last_date
              FROM securities_catalog c
              WHERE c.source=? AND lower(trim(c.stock_type))='stock'
            )""",(source,)).fetchone()
    return {"earliest_date":str(row["earliest_date"]).replace("/","-") if row and row["earliest_date"] else None,
            "latest_date":str(row["latest_date"]).replace("/","-") if row and row["latest_date"] else None}


def latest_data_as_of(source="ssi"):
    latest_day=latest_historical_date(source)
    latest_day_db=latest_day.replace("-","/") if latest_day else None
    with connect() as db:
        row=db.execute("""SELECT MAX(stamp) FROM (
            SELECT MAX(received_at_vn) stamp FROM observations WHERE source=?
            UNION ALL SELECT MAX(minute_vn) FROM bars_1m WHERE source=?
            UNION ALL SELECT MAX(h.fetched_at) FROM securities_catalog c CROSS JOIN historical_prices h
                WHERE c.source=? AND lower(trim(c.stock_type))='stock'
                  AND h.source=c.source AND h.symbol=c.symbol AND h.timeframe='1d' AND h.trading_date=?
        )""",(source,source,source,latest_day_db)).fetchone()
    return row[0] if row and row[0] else None


def count_baseline_qualified_symbols(source, before_session=None):
    return len(fetch_baseline_qualified_symbols(source,before_session))


def fetch_baseline_qualified_symbols(source, before_session=None):
    before_session=before_session or datetime.now(config.TIMEZONE).date().isoformat()
    return _cached_baseline_qualified_symbols(source,before_session)


@lru_cache(maxsize=8)
def _cached_baseline_qualified_symbols(source,before_session):
    with connect() as db:
        rows=db.execute("""WITH prior_sessions AS (
              SELECT session_date FROM bars_1m WHERE source=? AND session_date<?
              GROUP BY session_date ORDER BY session_date DESC LIMIT ?
            ), qualified AS (
              SELECT b.symbol,b.baseline_bucket,COUNT(*) AS n FROM bars_1m b
              JOIN prior_sessions s ON s.session_date=b.session_date
              WHERE b.source=? AND b.baseline_bucket IS NOT NULL AND b.coverage_status='ok'
              GROUP BY b.symbol,b.baseline_bucket HAVING COUNT(b.spread_bps)>=?
                AND COUNT(b.total_depth_value)>=?
                AND COUNT(DISTINCT CASE WHEN b.spread_bps IS NOT NULL AND b.total_depth_value IS NOT NULL
                                        THEN b.session_date END)>=?
            ) SELECT DISTINCT symbol FROM qualified""",
            (source,before_session,config.BASELINE_SESSIONS,source,config.BASELINE_MIN_BARS,
             config.BASELINE_MIN_BARS,config.BASELINE_SESSIONS)).fetchall()
    return frozenset(row[0] for row in rows)


def fetch_security_metadata(source="ssi"):
    with connect() as db:
        rows=db.execute("""WITH latest AS (
          SELECT symbol,close_price,ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY trading_date DESC) rn
          FROM historical_prices WHERE source=? AND timeframe='1d'
        ) SELECT c.symbol,c.board,c.name_vi,c.stock_type,l.close_price AS latest_close
          FROM securities_catalog c LEFT JOIN latest l ON l.symbol=c.symbol AND l.rn=1
          WHERE c.source=?""",(source,source)).fetchall()
    return {row["symbol"]:dict(row) for row in rows}


def fetch_company_metadata(source, symbol):
    with connect() as db:
        row=db.execute("""SELECT c.symbol,c.board,c.name_vi,c.name_en,c.stock_type,
          (SELECT h.close_price FROM historical_prices h
           WHERE h.source=c.source AND h.symbol=c.symbol AND h.timeframe='1d'
           ORDER BY h.trading_date DESC LIMIT 1) AS latest_close
          FROM securities_catalog c WHERE c.source=? AND c.symbol=?""",
          (source,symbol.upper())).fetchone()
    return dict(row) if row else {}


def update_stale_alert(source, stale, threshold_seconds, latest_data_at=None):
    now = datetime.now(config.TIMEZONE).isoformat(timespec="seconds")
    with connect() as db:
        active = db.execute("SELECT id FROM system_alerts WHERE source=? AND alert_type='stale_data' AND status='active' ORDER BY id DESC LIMIT 1",
                            (source,)).fetchone()
        if stale and not active:
            db.execute("INSERT INTO system_alerts(source,alert_type,severity,triggered_at,status,threshold_value,last_data_at,explanation) "
                       "VALUES (?,?,?,?,?,?,?,?)",
                       (source, "stale_data", "system", now, "active", threshold_seconds, latest_data_at,
                        "Không nhận snapshot mới trong phiên liên tục; dữ liệu lịch sử không được xem là LIVE."))
        elif not stale and active:
            db.execute("UPDATE system_alerts SET status='resolved',ended_at=? WHERE id=?", (now, active["id"]))


def connect():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.DB_PATH, timeout=30, factory=_ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA synchronous = NORMAL")
    return db


def initialize():
    with connect() as db:
        # WAL is a database-level setting. Set it during initialization only;
        # repeating this pragma on every request connection can serialize
        # otherwise independent reads behind a writer.
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, source_timestamp TEXT, received_at_utc TEXT NOT NULL,
            source_delay_seconds REAL,
            bid_price_1 REAL, bid_volume_1 REAL, bid_price_2 REAL, bid_volume_2 REAL,
            bid_price_3 REAL, bid_volume_3 REAL, ask_price_1 REAL, ask_volume_1 REAL,
            ask_price_2 REAL, ask_volume_2 REAL, ask_price_3 REAL, ask_volume_3 REAL,
            raw_payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
            validation_status TEXT NOT NULL, validation_reason TEXT,
            source TEXT NOT NULL CHECK(source IN ('demo','live','ssi')),
            UNIQUE(source, payload_hash)
        );
        CREATE INDEX IF NOT EXISTS ix_observations_symbol_time
            ON observations(source, symbol, received_at_utc);
        CREATE INDEX IF NOT EXISTS ix_observations_received
            ON observations(source, received_at_utc);
        CREATE TABLE IF NOT EXISTS metrics (
            observation_id INTEGER PRIMARY KEY REFERENCES observations(id) ON DELETE CASCADE,
            mid_price REAL, spread_bps REAL, bid_depth_qty REAL, ask_depth_qty REAL,
            total_depth_qty REAL, bid_depth_value REAL, ask_depth_value REAL,
            total_depth_value REAL, queue_imbalance REAL, value_imbalance REAL,
            depth_levels_used INTEGER, calculated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_metrics_observation ON metrics(observation_id);
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL, alert_type TEXT NOT NULL, severity TEXT NOT NULL,
            metric_value REAL, threshold_value REAL, baseline_window INTEGER,
            triggered_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
            explanation TEXT NOT NULL, source TEXT NOT NULL,
            UNIQUE(observation_id, alert_type)
        );
        CREATE INDEX IF NOT EXISTS ix_alerts_symbol_time
            ON alerts(source, symbol, triggered_at);
        CREATE TABLE IF NOT EXISTS collector_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
            ended_at TEXT, status TEXT NOT NULL, messages_received INTEGER NOT NULL DEFAULT 0,
            rows_valid INTEGER NOT NULL DEFAULT 0, rows_invalid INTEGER NOT NULL DEFAULT 0,
            reconnect_count INTEGER NOT NULL DEFAULT 0, last_message_at TEXT
        );
        CREATE TABLE IF NOT EXISTS app_config (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bars_1m (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, symbol TEXT NOT NULL,
            minute_vn TEXT NOT NULL, session_date TEXT NOT NULL, market TEXT NOT NULL,
            session_phase TEXT NOT NULL, baseline_bucket TEXT,
            spread_bps REAL, spread_ticks REAL, bid_depth_qty REAL, ask_depth_qty REAL,
            bid_depth_value REAL, ask_depth_value REAL, total_depth_value REAL,
            queue_imbalance REAL, n_snapshots INTEGER NOT NULL,
            coverage_status TEXT NOT NULL, UNIQUE(source,symbol,minute_vn)
        );
        CREATE INDEX IF NOT EXISTS ix_bars_symbol_minute ON bars_1m(source,symbol,minute_vn);
        CREATE INDEX IF NOT EXISTS ix_bars_minute_symbol ON bars_1m(source,minute_vn,symbol);
        CREATE INDEX IF NOT EXISTS ix_bars_session_baseline ON bars_1m(source,session_date,symbol,baseline_bucket);
        CREATE INDEX IF NOT EXISTS ix_bars_symbol_session_baseline ON bars_1m(source,symbol,session_date,baseline_bucket);
        CREATE TABLE IF NOT EXISTS historical_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL DEFAULT 'ssi', symbol TEXT NOT NULL,
            trading_date TEXT NOT NULL, timeframe TEXT NOT NULL DEFAULT '1d',
            open_price REAL, high_price REAL, low_price REAL, close_price REAL,
            volume REAL, value REAL, fetched_at TEXT NOT NULL,
            UNIQUE(source,symbol,timeframe,trading_date)
        );
        CREATE INDEX IF NOT EXISTS ix_historical_prices_symbol_date
            ON historical_prices(source,symbol,timeframe,trading_date);
        CREATE TABLE IF NOT EXISTS historical_backfill_state (
            source TEXT NOT NULL, symbol TEXT NOT NULL, requested_from TEXT NOT NULL,
            requested_to TEXT NOT NULL, status TEXT NOT NULL, pages_fetched INTEGER NOT NULL DEFAULT 0,
            rows_fetched INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
            PRIMARY KEY(source,symbol)
        );
        CREATE TABLE IF NOT EXISTS trade_ticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL, symbol TEXT NOT NULL,
            source_timestamp TEXT, source_ts_vn TEXT,
            received_at_utc TEXT NOT NULL, received_at_vn TEXT NOT NULL,
            price REAL NOT NULL, quantity REAL NOT NULL, side TEXT,
            total_volume REAL, open_price REAL, high_price REAL, low_price REAL, average_price REAL,
            source_delay_seconds REAL, validation_status TEXT NOT NULL DEFAULT 'valid',
            payload_hash TEXT NOT NULL, UNIQUE(source,payload_hash)
        );
        CREATE INDEX IF NOT EXISTS ix_trade_ticks_symbol_time
            ON trade_ticks(source,symbol,received_at_vn);
        CREATE INDEX IF NOT EXISTS ix_trade_ticks_time
            ON trade_ticks(source,received_at_vn);
        CREATE TABLE IF NOT EXISTS securities_catalog (
            source TEXT NOT NULL DEFAULT 'ssi', symbol TEXT NOT NULL,
            board TEXT, name_vi TEXT, name_en TEXT, stock_type TEXT,
            listed_shares REAL, first_trading_date TEXT, last_trading_date TEXT,
            updated_at TEXT NOT NULL, PRIMARY KEY(source,symbol)
        );
        CREATE INDEX IF NOT EXISTS ix_securities_catalog_name ON securities_catalog(source,name_vi);
        CREATE TABLE IF NOT EXISTS session_calendar (
            session_date TEXT NOT NULL, market TEXT NOT NULL, is_open INTEGER NOT NULL DEFAULT 1,
            note TEXT, PRIMARY KEY(session_date,market)
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_date TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'ssi', status TEXT NOT NULL,
            bar_count INTEGER NOT NULL DEFAULT 0, symbol_count INTEGER NOT NULL DEFAULT 0,
            expected_symbols INTEGER NOT NULL DEFAULT 0, coverage_ratio REAL NOT NULL DEFAULT 0,
            closed_at TEXT, ohlc_symbols INTEGER NOT NULL DEFAULT 0,
            ohlc_expected INTEGER NOT NULL DEFAULT 0, ohlc_synced_at TEXT,
            PRIMARY KEY(session_date,source)
        );
        CREATE TABLE IF NOT EXISTS collector_run_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, ended_at TEXT,
            status TEXT NOT NULL, messages_received INTEGER NOT NULL DEFAULT 0,
            rows_valid INTEGER NOT NULL DEFAULT 0, rows_invalid INTEGER NOT NULL DEFAULT 0,
            duplicate_messages INTEGER NOT NULL DEFAULT 0, reconnect_count INTEGER NOT NULL DEFAULT 0,
            queue_dropped INTEGER NOT NULL DEFAULT 0, last_message_at TEXT, last_error TEXT,
            worker_id TEXT, process_id INTEGER, heartbeat_at TEXT, connection_status TEXT
        );
        CREATE TABLE IF NOT EXISTS collector_worker_control (
            worker_id TEXT PRIMARY KEY, command TEXT NOT NULL, requested_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ingestion_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
            received_at_vn TEXT NOT NULL, symbol TEXT, reason TEXT NOT NULL,
            sanitized_payload TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_ingestion_errors_time ON ingestion_errors(source,received_at_vn);
        CREATE TABLE IF NOT EXISTS system_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, alert_type TEXT NOT NULL,
            severity TEXT NOT NULL, triggered_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL,
            threshold_value REAL, last_data_at TEXT, explanation TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_system_alerts_status ON system_alerts(source,status,triggered_at);
        -- Single local profile: no account or user data is written to the repo.
        CREATE TABLE IF NOT EXISTS user_settings (
            profile_id TEXT PRIMARY KEY CHECK(profile_id='local'),
            standard_order_shares INTEGER NOT NULL DEFAULT 10000,
            favorable_spread_pctl REAL NOT NULL DEFAULT 25,
            stress_spread_pctl REAL NOT NULL DEFAULT 90,
            favorable_depth_pctl REAL NOT NULL DEFAULT 75,
            stress_depth_pctl REAL NOT NULL DEFAULT 10,
            quiet_start TEXT, quiet_end TEXT,
            notification_channel TEXT NOT NULL DEFAULT 'in_app',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS watchlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS watchlist_items (
            watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL,
            PRIMARY KEY(watchlist_id,symbol)
        );
        CREATE INDEX IF NOT EXISTS ix_watchlist_items_order ON watchlist_items(watchlist_id,position,symbol);
        """)
        db.execute("INSERT OR IGNORE INTO user_settings(profile_id,updated_at) VALUES('local',?)",
                   (datetime.now(config.TIMEZONE).isoformat(timespec='seconds'),))
        db.execute("""UPDATE user_settings SET favorable_spread_pctl=?,stress_spread_pctl=?,
                      favorable_depth_pctl=?,stress_depth_pctl=?""",
                   (config.FAVORABLE_SPREAD_PERCENTILE,config.STRESS_SPREAD_PERCENTILE,
                    config.FAVORABLE_DEPTH_PERCENTILE,config.STRESS_DEPTH_PERCENTILE))
        schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='observations'").fetchone()
        if schema and "'ssi'" not in (schema[0] or ""):
            db.execute("PRAGMA foreign_keys=OFF")
            columns = [row[1] for row in db.execute("PRAGMA table_info(observations)")]
            db.execute("""CREATE TABLE observations_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
                source_timestamp TEXT, received_at_utc TEXT NOT NULL, source_delay_seconds REAL,
                bid_price_1 REAL, bid_volume_1 REAL, bid_price_2 REAL, bid_volume_2 REAL,
                bid_price_3 REAL, bid_volume_3 REAL, ask_price_1 REAL, ask_volume_1 REAL,
                ask_price_2 REAL, ask_volume_2 REAL, ask_price_3 REAL, ask_volume_3 REAL,
                raw_payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
                validation_status TEXT NOT NULL, validation_reason TEXT,
                source TEXT NOT NULL CHECK(source IN ('demo','live','ssi')),
                source_ts_vn TEXT, received_at_vn TEXT, market TEXT NOT NULL DEFAULT 'HOSE',
                session_phase TEXT NOT NULL DEFAULT 'UNKNOWN', flags TEXT NOT NULL DEFAULT '[]',
                ref_price REAL, ceiling_price REAL, floor_price REAL, last_price REAL,
                total_trading_value REAL, total_trading_volume REAL, UNIQUE(source,payload_hash))""")
            names = ",".join(columns)
            db.execute(f"INSERT INTO observations_new ({names}) SELECT {names} FROM observations")
            db.execute("DROP TABLE observations")
            db.execute("ALTER TABLE observations_new RENAME TO observations")
            db.execute("CREATE INDEX IF NOT EXISTS ix_observations_symbol_time ON observations(source,symbol,received_at_utc)")
            db.execute("CREATE INDEX IF NOT EXISTS ix_observations_received ON observations(source,received_at_utc)")
            db.execute("PRAGMA foreign_keys=ON")
        _migrate_schema(db)
        # _refresh_bar filters by received_at_vn for each (source, symbol, minute).
        # The UTC-only indexes made every bar refresh scan the full observations table.
        db.execute("CREATE INDEX IF NOT EXISTS ix_observations_symbol_received_vn "
                   "ON observations(source,symbol,received_at_vn)")
    # Synthetic demo data is disabled; use SSI data only.


def _migrate_schema(db):
    additions = {
        "observations": {
            "source_ts_vn": "TEXT", "received_at_vn": "TEXT", "source_delay_seconds": "REAL",
            "market": "TEXT NOT NULL DEFAULT 'HOSE'",
            "session_phase": "TEXT NOT NULL DEFAULT 'UNKNOWN'", "flags": "TEXT NOT NULL DEFAULT '[]'",
            "ref_price": "REAL", "ceiling_price": "REAL", "floor_price": "REAL",
            "last_price": "REAL", "total_trading_value": "REAL", "total_trading_volume": "REAL",
        },
        "metrics": {"spread_abs": "REAL", "spread_ticks": "REAL", "tick_size": "REAL"},
        "bars_1m": {"bid_depth_value": "REAL", "ask_depth_value": "REAL"},
        "alerts": {"bucket": "TEXT", "baseline_mode": "TEXT", "consecutive_bars": "INTEGER NOT NULL DEFAULT 1"},
        "collector_run_details": {"queue_dropped": "INTEGER NOT NULL DEFAULT 0", "worker_id": "TEXT",
                                   "process_id": "INTEGER", "heartbeat_at": "TEXT", "connection_status": "TEXT"},
    }
    for table, fields in additions.items():
        existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        for field, declaration in fields.items():
            if field not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {field} {declaration}")


def get_local_settings():
    with connect() as db:
        row=db.execute("SELECT * FROM user_settings WHERE profile_id='local'").fetchone()
    return dict(row) if row else {}


def update_local_settings(values):
    allowed={"standard_order_shares","quiet_start","quiet_end","notification_channel"}
    updates={key:value for key,value in values.items() if key in allowed}
    if not updates:return get_local_settings()
    updates["updated_at"]=datetime.now(config.TIMEZONE).isoformat(timespec='seconds')
    fields=",".join(f"{key}=?" for key in updates)
    with connect() as db:
        db.execute(f"UPDATE user_settings SET {fields} WHERE profile_id='local'",list(updates.values()))
        row=db.execute("SELECT * FROM user_settings WHERE profile_id='local'").fetchone()
    return dict(row) if row else {}


def fetch_local_watchlists():
    with connect() as db:
        lists=db.execute("SELECT id,name,created_at,updated_at FROM watchlists ORDER BY id").fetchall()
        items=db.execute("SELECT watchlist_id,symbol,position,note,added_at FROM watchlist_items ORDER BY watchlist_id,position,symbol").fetchall()
    grouped={int(row["id"]):[] for row in lists}
    for row in items:grouped[int(row["watchlist_id"])].append(dict(row))
    return [{**dict(row),"items":grouped[int(row["id"])],"item_count":len(grouped[int(row["id"])])} for row in lists]


def create_local_watchlist(name):
    now=datetime.now(config.TIMEZONE).isoformat(timespec='seconds')
    with connect() as db:
        cursor=db.execute("INSERT INTO watchlists(name,created_at,updated_at) VALUES(?,?,?)",(name,now,now))
        return int(cursor.lastrowid)


def rename_local_watchlist(watchlist_id,name):
    with connect() as db:
        cursor=db.execute("UPDATE watchlists SET name=?,updated_at=? WHERE id=?",
                          (name,datetime.now(config.TIMEZONE).isoformat(timespec='seconds'),watchlist_id))
        return cursor.rowcount>0


def delete_local_watchlist(watchlist_id):
    with connect() as db:
        cursor=db.execute("DELETE FROM watchlists WHERE id=?",(watchlist_id,))
        return cursor.rowcount>0


def add_local_watchlist_item(watchlist_id,symbol,note=''):
    now=datetime.now(config.TIMEZONE).isoformat(timespec='seconds')
    with connect() as db:
        count=db.execute("SELECT COUNT(*) FROM watchlist_items WHERE watchlist_id=?",(watchlist_id,)).fetchone()[0]
        if count>=50:raise ValueError("Mỗi danh sách tối đa 50 mã cổ phiếu.")
        position=db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM watchlist_items WHERE watchlist_id=?",(watchlist_id,)).fetchone()[0]
        db.execute("INSERT INTO watchlist_items(watchlist_id,symbol,position,note,added_at) VALUES(?,?,?,?,?)",
                   (watchlist_id,symbol,position,note,now))
        db.execute("UPDATE watchlists SET updated_at=? WHERE id=?",(now,watchlist_id))


def remove_local_watchlist_item(watchlist_id,symbol):
    with connect() as db:
        cursor=db.execute("DELETE FROM watchlist_items WHERE watchlist_id=? AND symbol=?",(watchlist_id,symbol.upper()))
        return cursor.rowcount>0


def update_local_watchlist_item_note(watchlist_id,symbol,note):
    with connect() as db:
        cursor=db.execute("UPDATE watchlist_items SET note=? WHERE watchlist_id=? AND symbol=?",
                          (note,watchlist_id,symbol.upper()))
        return cursor.rowcount>0


def reorder_local_watchlist(watchlist_id,symbols):
    now=datetime.now(config.TIMEZONE).isoformat(timespec='seconds')
    with connect() as db:
        current={row[0] for row in db.execute("SELECT symbol FROM watchlist_items WHERE watchlist_id=?",(watchlist_id,))}
        if set(symbols)!=current or len(symbols)!=len(current):raise ValueError("Thứ tự phải chứa đúng các mã đang có trong danh sách.")
        db.executemany("UPDATE watchlist_items SET position=? WHERE watchlist_id=? AND symbol=?",
                       [(index,watchlist_id,symbol) for index,symbol in enumerate(symbols)])
        db.execute("UPDATE watchlists SET updated_at=? WHERE id=?",(now,watchlist_id))


def replace_local_profile(settings,watchlists):
    now=datetime.now(config.TIMEZONE).isoformat(timespec='seconds')
    with connect() as db:
        db.execute("SAVEPOINT import_local_profile")
        try:
            allowed={"standard_order_shares","quiet_start","quiet_end","notification_channel"}
            clean={key:value for key,value in settings.items() if key in allowed}
            if clean:
                assignments=",".join(f"{key}=?" for key in clean)
                db.execute(f"UPDATE user_settings SET {assignments},updated_at=? WHERE profile_id='local'",[*clean.values(),now])
            db.execute("DELETE FROM watchlists")
            for item in watchlists:
                cursor=db.execute("INSERT INTO watchlists(name,created_at,updated_at) VALUES(?,?,?)",(item["name"],now,now))
                list_id=cursor.lastrowid
                for position,entry in enumerate(item["items"]):
                    db.execute("INSERT INTO watchlist_items(watchlist_id,symbol,position,note,added_at) VALUES(?,?,?,?,?)",
                               (list_id,entry["symbol"],position,entry.get("note",''),now))
            db.execute("RELEASE SAVEPOINT import_local_profile")
        except Exception:
            db.execute("ROLLBACK TO SAVEPOINT import_local_profile")
            db.execute("RELEASE SAVEPOINT import_local_profile")
            raise


def backup_local_profile_json():
    """Save only the local user profile before an import replaces it."""
    import json
    from pathlib import Path
    settings=get_local_settings()
    allowed={"standard_order_shares","quiet_start","quiet_end","notification_channel"}
    payload={"format":"sli-local-profile-v1","settings":{key:settings[key] for key in allowed},
             "watchlists":[{"name":row["name"],"items":[{"symbol":item["symbol"],"note":item["note"]} for item in row["items"]]}
                           for row in fetch_local_watchlists()]}
    folder=Path(config.DB_PATH).parent
    folder.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(config.TIMEZONE).strftime("%Y%m%d-%H%M%S")
    target=folder/f"local-profile-backup-{stamp}.json"
    target.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    return target.name


def _insert(db, observation, source, refresh_bar=True):
    metrics = calculate_metrics(observation)
    raw = sanitize_payload(observation.get("raw_payload") or {})
    received_vn = observation.get("received_at_vn") or _to_vietnam_time(observation.get("received_at_utc"))
    event_time = (observation.get("source_ts_vn") or observation.get("source_timestamp")
                  or observation.get("received_at_utc") or received_vn)
    book_state = {field: observation.get(field) for field in OBS_FIELDS}
    # Same book state at a later source event is a new observation; only a repeated
    # payload at the same event time is treated as a duplicate.
    digest = payload_hash({"symbol": observation["symbol"], "market": observation.get("market", "HOSE"),
                           "event_time": event_time, "book": book_state})
    # Keep only invalid payloads and a deterministic ~1% valid sample for audit.
    raw_text = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    keep_raw = observation.get("validation_status") != "valid" or int(digest[:4], 16) < 656
    stored_raw = raw_text if keep_raw else "{}"
    columns = [
        "symbol", "source_timestamp", "source_ts_vn", "source_delay_seconds", "received_at_utc", "received_at_vn",
        "market", "session_phase", "flags", "ref_price", "ceiling_price", "floor_price",
        "last_price", "total_trading_value", "total_trading_volume", *OBS_FIELDS,
        "raw_payload", "payload_hash", "validation_status", "validation_reason", "source",
    ]
    values = [
        observation["symbol"], observation.get("source_timestamp"), observation.get("source_ts_vn"),
        observation.get("source_delay_seconds"), observation.get("received_at_utc"), received_vn,
        observation.get("market", "HOSE"), observation.get("session_phase", "UNKNOWN"),
        json.dumps(observation.get("flags", [])), observation.get("ref_price"),
        observation.get("ceiling_price"), observation.get("floor_price"), observation.get("last_price"),
        observation.get("total_trading_value"), observation.get("total_trading_volume"),
        *[observation.get(field) for field in OBS_FIELDS], stored_raw, digest,
        observation.get("validation_status", "valid"),
        observation.get("validation_reason"), source,
    ]
    cursor = db.execute(
        f"INSERT OR IGNORE INTO observations ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        values,
    )
    if cursor.rowcount == 0:
        return None
    observation_id = cursor.lastrowid
    db.execute(
        f"INSERT INTO metrics (observation_id,{','.join(METRIC_FIELDS)}) VALUES ({','.join('?' for _ in range(len(METRIC_FIELDS)+1))})",
        [observation_id, *[metrics.get(field) for field in METRIC_FIELDS]],
    )
    if refresh_bar:
        _refresh_bar(db, observation, source, metrics, observation_id)
    return observation_id


def _to_vietnam_time(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(config.TIMEZONE).isoformat(timespec="milliseconds")
    except ValueError:
        return None


def insert_observation(observation, source="live"):
    with connect() as db:
        observation_id = _insert(db, observation, source)
        return {"inserted": observation_id is not None, "duplicate": observation_id is None, "id": observation_id}


def _refresh_bar(db, observation, source, metrics, observation_id):
    received = observation.get("received_at_vn") or observation.get("received_at_utc")
    try:
        stamp = datetime.fromisoformat(received.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=config.TIMEZONE)
        stamp = stamp.astimezone(config.TIMEZONE)
    except (AttributeError, ValueError):
        return
    # Bars are grouped by the receive minute, so classify them against that same
    # timestamp and the exchange schedule. Persisted phase labels can come from
    # an older collector process and must not truncate otherwise valid sessions.
    phase = market_rules.session_phase(stamp, observation.get("market", "HOSE"))
    if phase not in {"CONTINUOUS_AM", "CONTINUOUS_PM"}:
        return
    if not is_session_open(stamp.date().isoformat(), observation.get("market", "HOSE"), db):
        return
    minute = stamp.replace(second=0, microsecond=0).isoformat(timespec="minutes")
    rows = db.execute("""
        SELECT o.flags, o.validation_status, m.spread_bps, m.spread_ticks,
               m.bid_depth_qty, m.ask_depth_qty, m.bid_depth_value, m.ask_depth_value,
               m.total_depth_value, m.queue_imbalance
        FROM observations o JOIN metrics m ON m.observation_id=o.id
        WHERE o.source=? AND o.symbol=? AND o.received_at_vn>=? AND o.received_at_vn<?
    """, (source, observation["symbol"], minute,
          (stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)).isoformat(timespec="minutes"))).fetchall()
    eligible = []
    for row in rows:
        flags = set(json.loads(row["flags"] or "[]"))
        if row["validation_status"] == "valid" and not flags.intersection({"one_sided_limit", "auction_phase", "off_tick", "crossed_book", "late_message", "future_source_timestamp", "invalid_source_timestamp"}):
            eligible.append(row)
    if not eligible:
        return
    def med(field):
        values = [row[field] for row in eligible if row[field] is not None]
        return quantile(values, 50) if values else None
    bucket = market_rules.baseline_bucket(stamp, observation.get("market", "HOSE"))
    db.execute("""
        INSERT INTO bars_1m (source,symbol,minute_vn,session_date,market,session_phase,
          baseline_bucket,spread_bps,spread_ticks,bid_depth_qty,ask_depth_qty,bid_depth_value,ask_depth_value,
          total_depth_value,queue_imbalance,n_snapshots,coverage_status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source,symbol,minute_vn) DO UPDATE SET
          session_date=excluded.session_date,market=excluded.market,
          session_phase=excluded.session_phase,baseline_bucket=excluded.baseline_bucket,
          spread_bps=excluded.spread_bps,spread_ticks=excluded.spread_ticks,
          bid_depth_qty=excluded.bid_depth_qty,ask_depth_qty=excluded.ask_depth_qty,
          bid_depth_value=excluded.bid_depth_value,ask_depth_value=excluded.ask_depth_value,
          total_depth_value=excluded.total_depth_value,queue_imbalance=excluded.queue_imbalance,
          n_snapshots=excluded.n_snapshots,coverage_status=excluded.coverage_status
    """, (source, observation["symbol"], minute, stamp.date().isoformat(),
          observation.get("market", "HOSE"), phase, bucket, med("spread_bps"), med("spread_ticks"),
          med("bid_depth_qty"), med("ask_depth_qty"), med("bid_depth_value"), med("ask_depth_value"),
          med("total_depth_value"), med("queue_imbalance"), len(eligible),
          "ok" if len(eligible) >= config.MIN_BAR_SNAPSHOTS else "sparse"))
    bar = db.execute("SELECT * FROM bars_1m WHERE source=? AND symbol=? AND minute_vn=?",
                     (source, observation["symbol"], minute)).fetchone()
    _evaluate_bar_alerts(db, bar, observation_id)


def _baseline_bars(db, bar):
    prior_sessions = [row[0] for row in db.execute(
        "SELECT DISTINCT session_date FROM bars_1m WHERE source=? AND symbol=? AND session_date<? ORDER BY session_date DESC LIMIT ?",
        (bar["source"], bar["symbol"], bar["session_date"], config.BASELINE_SESSIONS),
    ).fetchall()]
    if not prior_sessions:
        return [], "insufficient"
    placeholders = ",".join("?" for _ in prior_sessions)
    args = [bar["source"], bar["symbol"], *prior_sessions]
    bucket_rows = db.execute(f"SELECT * FROM bars_1m WHERE source=? AND symbol=? AND market=? AND coverage_status='ok' AND session_date IN ({placeholders}) AND baseline_bucket=? ORDER BY minute_vn",
                             (bar["source"],bar["symbol"],bar["market"],*prior_sessions,bar["baseline_bucket"])).fetchall() if bar["baseline_bucket"] else []
    # Require enough bars across distinct prior sessions for this symbol, board and
    # intraday phase; many minutes from only a few sessions are not independent days.
    spread_rows=[row for row in bucket_rows if row["spread_bps"] is not None]
    depth_rows=[row for row in bucket_rows if row["total_depth_value"] is not None]
    spread_sessions={row["session_date"] for row in spread_rows}
    depth_sessions={row["session_date"] for row in depth_rows}
    sessions_used=len(spread_sessions & depth_sessions)
    if (len(spread_rows)>=config.BASELINE_MIN_BARS
            and len(depth_rows)>=config.BASELINE_MIN_BARS
            and sessions_used>=config.BASELINE_SESSIONS):
        return bucket_rows, "bucket"
    return [], "insufficient"


def _evaluate_bar_alerts(db, bar, observation_id):
    if bar["coverage_status"] != "ok" or bar["baseline_bucket"] is None:
        return
    baseline, mode = _baseline_bars(db, bar)
    if not baseline:
        return
    spread_values = [r["spread_bps"] for r in baseline if r["spread_bps"] is not None]
    depth_values = [r["total_depth_value"] for r in baseline if r["total_depth_value"] is not None]
    spread_limit = quantile(spread_values, config.SPREAD_PERCENTILE)
    depth_limit = quantile(depth_values, config.DEPTH_PERCENTILE)
    k = max(1, config.ALERT_CONSECUTIVE_BARS)
    recent = db.execute("SELECT * FROM bars_1m WHERE source=? AND symbol=? AND baseline_bucket=? AND minute_vn<=? ORDER BY minute_vn DESC LIMIT ?",
                        (bar["source"], bar["symbol"], bar["baseline_bucket"], bar["minute_vn"], k)).fetchall()
    recent = list(reversed(recent))
    spread_hit = spread_limit is not None and len(recent) == k and all(r["spread_bps"] is not None and r["spread_bps"] >= spread_limit for r in recent)
    depth_hit = depth_limit is not None and len(recent) == k and all(r["total_depth_value"] is not None and r["total_depth_value"] <= depth_limit for r in recent)
    imb_hit = len(recent) == k and all(r["queue_imbalance"] is not None and abs(r["queue_imbalance"]) >= config.IMBALANCE_THRESHOLD for r in recent)
    triggers = []
    if spread_hit:
        triggers.append(("spread_widening", "medium", bar["spread_bps"], spread_limit, "Spread bar chạm hoặc vượt P90 baseline cùng mã/sàn/pha trong ba bar hợp lệ liên tiếp."))
    if depth_hit:
        triggers.append(("depth_drop", "medium", bar["total_depth_value"], depth_limit, "Depth top 3 chạm hoặc thấp hơn P10 baseline cùng mã/sàn/pha trong ba bar hợp lệ liên tiếp."))
    if spread_hit and depth_hit:
        triggers.append(("combined_liquidity_stress", "high", None, None, "Spread rộng và độ sâu mỏng đồng thời."))
    if imb_hit:
        triggers.append(("strong_queue_imbalance", "medium", bar["queue_imbalance"], config.IMBALANCE_THRESHOLD, "Queue imbalance nghiêng mạnh liên tiếp; không phải dự báo giá."))
    now = datetime.fromisoformat(bar["minute_vn"])
    cooldown_since = (now - timedelta(minutes=config.ALERT_COOLDOWN_MIN)).isoformat(timespec="minutes")
    for alert_type, severity, value, threshold, explanation in triggers:
        exists = db.execute("SELECT 1 FROM alerts WHERE source=? AND symbol=? AND alert_type=? AND triggered_at>=? LIMIT 1",
                            (bar["source"], bar["symbol"], alert_type, cooldown_since)).fetchone()
        if exists:
            continue
        db.execute("""INSERT INTO alerts (observation_id,symbol,alert_type,severity,metric_value,threshold_value,
            baseline_window,triggered_at,status,explanation,source,bucket,baseline_mode,consecutive_bars)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (observation_id, bar["symbol"], alert_type, severity, value, threshold, len(baseline),
             bar["minute_vn"], "active", explanation, bar["source"], bar["baseline_bucket"], mode, k))
    if len(recent) == k:
        safe = {
            "spread_widening": spread_limit is not None and all(r["spread_bps"] is not None and r["spread_bps"] <= spread_limit for r in recent),
            "depth_drop": depth_limit is not None and all(r["total_depth_value"] is not None and r["total_depth_value"] >= depth_limit for r in recent),
            "strong_queue_imbalance": all(r["queue_imbalance"] is not None and abs(r["queue_imbalance"]) < config.IMBALANCE_THRESHOLD for r in recent),
        }
        safe["combined_liquidity_stress"] = safe["spread_widening"] and safe["depth_drop"]
        for alert_type, is_safe in safe.items():
            if is_safe and alert_type not in {trigger[0] for trigger in triggers}:
                db.execute("UPDATE alerts SET status='resolved' WHERE source=? AND symbol=? AND alert_type=? AND status='active'",
                           (bar["source"], bar["symbol"], alert_type))


def seed_demo_if_empty():
    with connect() as db:
        if db.execute("SELECT COUNT(*) FROM observations WHERE source='demo'").fetchone()[0]:
            return
        bases = {"FPT": 128000.0, "HPG": 27500.0, "VPB": 20500.0}
        now = datetime.now(timezone.utc)
        for symbol, base in bases.items():
            for index in range(120):
                drift = math.sin(index / 8) * base * 0.001
                bid1 = round(base + drift - (index % 4) * 10, 2)
                spread = max(10.0, base * (0.0004 + (index % 7) * 0.00003))
                ask1 = round(bid1 + spread, 2)
                bid_vols = [800 + (index * 37) % 1400, 1200 + (index * 29) % 1800, 1600 + (index * 19) % 2200]
                ask_vols = [700 + (index * 31) % 1500, 1100 + (index * 23) % 1900, 1500 + (index * 17) % 2300]
                received = (now - timedelta(minutes=119-index)).isoformat()
                raw = {
                    "symbol": symbol, "demo_sample": index,
                    "bidPrice1": bid1, "bidVol1": bid_vols[0],
                    "bidPrice2": round(bid1 - spread * 0.5, 2), "bidVol2": bid_vols[1],
                    "bidPrice3": round(bid1 - spread, 2), "bidVol3": bid_vols[2],
                    "askPrice1": ask1, "askVol1": ask_vols[0],
                    "askPrice2": round(ask1 + spread * 0.5, 2), "askVol2": ask_vols[1],
                    "askPrice3": round(ask1 + spread, 2), "askVol3": ask_vols[2],
                }
                observation = {
                    "symbol": symbol, "source_timestamp": None,
                    "received_at_utc": received, "validation_status": "valid",
                    "validation_reason": None, "raw_payload": raw,
                }
                for level in range(1, 4):
                    observation[f"bid_price_{level}"] = raw[f"bidPrice{level}"]
                    observation[f"bid_volume_{level}"] = raw[f"bidVol{level}"]
                    observation[f"ask_price_{level}"] = raw[f"askPrice{level}"]
                    observation[f"ask_volume_{level}"] = raw[f"askVol{level}"]
                _insert(db, observation, "demo")


def fetch_observations(source, symbol=None, limit=500, start_at=None, end_at=None, phase=None):
    limit = max(1, min(int(limit), 5000))
    query = """
        SELECT o.*, m.mid_price, m.spread_abs, m.spread_bps, m.spread_ticks, m.tick_size,
               m.bid_depth_qty, m.ask_depth_qty,
               m.total_depth_qty, m.bid_depth_value, m.ask_depth_value,
               m.total_depth_value, m.queue_imbalance, m.value_imbalance,
               m.depth_levels_used, m.calculated_at
        FROM observations o JOIN metrics m ON m.observation_id=o.id WHERE o.source=?
    """
    args = [source]
    if symbol:
        query += " AND o.symbol=?"
        args.append(symbol.upper())
    if start_at:
        query += " AND datetime(COALESCE(o.received_at_vn,o.received_at_utc))>=datetime(?)"
        args.append(start_at)
    if end_at:
        query += " AND datetime(COALESCE(o.received_at_vn,o.received_at_utc))<=datetime(?)"
        args.append(end_at)
    if phase:
        query += " AND o.session_phase=?"
        args.append(phase.upper())
    query += " ORDER BY o.received_at_utc DESC LIMIT ?"
    args.append(limit)
    with connect() as db:
        rows = db.execute(query, args).fetchall()
    return [dict(row) for row in rows]



def fetch_recent_observations_by_symbol(source, limit=240, start_at=None, end_at=None, phase=None):
    """Fetch recent observations per symbol via the source/symbol/time index."""
    limit=max(1,min(int(limit),5000))
    query="""SELECT o.id,o.symbol,o.received_at_utc,o.received_at_vn,o.source_ts_vn,
      o.market,o.session_phase,o.validation_status,o.validation_reason,o.last_price,
      o.bid_price_1,o.bid_volume_1,o.ask_price_1,o.ask_volume_1,
      m.mid_price,m.spread_bps,m.spread_ticks,m.bid_depth_qty,m.ask_depth_qty,
      m.bid_depth_value,m.ask_depth_value,m.total_depth_value,m.queue_imbalance
      FROM observations o JOIN metrics m ON m.observation_id=o.id WHERE o.source=? AND o.symbol=?"""
    with connect() as db:
        symbols=[row[0] for row in db.execute("SELECT DISTINCT symbol FROM observations WHERE source=? ORDER BY symbol",(source,))]
        grouped={}
        for ticker in symbols:
            sql=query; args=[source,ticker]
            if start_at: sql+=" AND datetime(COALESCE(o.received_at_vn,o.received_at_utc))>=datetime(?)"; args.append(start_at)
            if end_at: sql+=" AND datetime(COALESCE(o.received_at_vn,o.received_at_utc))<=datetime(?)"; args.append(end_at)
            if phase: sql+=" AND o.session_phase=?"; args.append(phase.upper())
            sql+=" ORDER BY o.received_at_utc DESC,o.id DESC LIMIT ?"; args.append(limit)
            rows=db.execute(sql,args).fetchall()
            if rows: grouped[ticker]=[dict(row) for row in reversed(rows)]
    return grouped

def fetch_recent_bars_by_symbol(source, limit=500, start_at=None, end_at=None, bucket=None):
    """Fetch a bounded recent bar window per symbol in one query."""
    limit=max(1,min(int(limit),10000))
    where=["source=?"]; args=[source]
    if start_at: where.append("datetime(minute_vn)>=datetime(?)"); args.append(start_at)
    if end_at: where.append("datetime(minute_vn)<=datetime(?)"); args.append(end_at)
    if bucket: where.append("baseline_bucket=?"); args.append(bucket.upper())
    args.append(limit)
    sql=f"""WITH ranked AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY minute_vn DESC) rn
      FROM bars_1m WHERE {' AND '.join(where)}) SELECT * FROM ranked WHERE rn<=? ORDER BY symbol,minute_vn"""
    with connect() as db: rows=db.execute(sql,args).fetchall()
    grouped={}
    for row in rows: grouped.setdefault(row['symbol'],[]).append(dict(row))
    return grouped

def fetch_detail_default_symbol(source="ssi"):
    """Choose the catalogued common stock with the broadest saved minute-bar coverage."""
    with connect() as db:
        rows=db.execute("""SELECT b.symbol,COUNT(*) AS n,c.stock_type
          FROM bars_1m b JOIN securities_catalog c ON c.source='ssi' AND c.symbol=b.symbol
          WHERE b.source=? GROUP BY b.symbol,c.stock_type ORDER BY n DESC,b.symbol ASC""",(source,)).fetchall()
    from .ranking import is_rankable_stock
    return next((row["symbol"] for row in rows if is_rankable_stock(row["symbol"],row["stock_type"])),None)


def count_historical_symbols(source='ssi'):
    with connect() as db:
        return db.execute("SELECT COUNT(DISTINCT symbol) FROM historical_prices WHERE source=? AND timeframe='1d'",(source,)).fetchone()[0]

def historical_backfill_states(source="ssi", requested_from=None):
    with connect() as db:
        rows=db.execute("SELECT symbol,requested_to,status FROM historical_backfill_state WHERE source=? AND requested_from=?",
                        (source,requested_from or config.HISTORY_START_DATE)).fetchall()
    return {row["symbol"]:dict(row) for row in rows}

def set_historical_backfill_state(symbol, requested_from, requested_to, status, pages_fetched, rows_fetched, source="ssi"):
    with connect() as db:
        db.execute("""INSERT INTO historical_backfill_state(
            source,symbol,requested_from,requested_to,status,pages_fetched,rows_fetched,updated_at)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source,symbol) DO UPDATE SET
            requested_from=excluded.requested_from,requested_to=excluded.requested_to,status=excluded.status,
            pages_fetched=excluded.pages_fetched,rows_fetched=excluded.rows_fetched,updated_at=excluded.updated_at""",
            (source,symbol.upper(),requested_from,requested_to,status,int(pages_fetched),int(rows_fetched),
             datetime.now(config.TIMEZONE).isoformat(timespec="seconds")))

def upsert_historical_prices(symbol, bars, source="ssi"):
    fetched_at = datetime.now(config.TIMEZONE).isoformat(timespec="seconds")
    saved = 0
    with connect() as db:
        for bar in bars:
            trading_date = str(getattr(bar, "trading_date", "") or "").replace("-", "/")[:10]
            if not trading_date:
                continue
            db.execute("""INSERT INTO historical_prices(
                source,symbol,trading_date,timeframe,open_price,high_price,low_price,
                close_price,volume,value,fetched_at) VALUES (?,?,?,'1d',?,?,?,?,?,?,?)
                ON CONFLICT(source,symbol,timeframe,trading_date) DO UPDATE SET
                open_price=excluded.open_price,high_price=excluded.high_price,
                low_price=excluded.low_price,close_price=excluded.close_price,
                volume=excluded.volume,value=excluded.value,fetched_at=excluded.fetched_at""",
                (source,symbol.upper(),trading_date,
                 getattr(bar,"open_price",None),getattr(bar,"high_price",None),
                 getattr(bar,"low_price",None),getattr(bar,"close_price",None),
                 getattr(bar,"volume",None),getattr(bar,"value",None),fetched_at))
            saved += 1
    return saved


def fetch_historical_prices(symbol=None, limit=90, source="ssi", exclude_date=None, start_date=None, end_date=None):
    query="SELECT symbol,trading_date,timeframe,open_price,high_price,low_price,close_price,volume,value,fetched_at FROM historical_prices WHERE source=? AND timeframe='1d'"
    args=[source]
    if symbol:
        query += " AND symbol=?"
        args.append(symbol.upper())
    if start_date:
        query += " AND replace(trading_date,'/','-')>=?"
        args.append(str(start_date).replace("/", "-")[:10])
    if end_date:
        query += " AND replace(trading_date,'/','-')<=?"
        args.append(str(end_date).replace("/", "-")[:10])
    if exclude_date:
        query += " AND trading_date<>?"
        args.append(str(exclude_date).replace("-","/")[:10])
    query += " ORDER BY trading_date DESC LIMIT ?"
    args.append(max(1,min(int(limit),10000)))
    with connect() as db:
        rows=db.execute(query,args).fetchall()
    return list(reversed([dict(row) for row in rows]))


def list_historical_symbols(source="ssi"):
    with connect() as db:
        rows=db.execute("SELECT DISTINCT symbol FROM historical_prices WHERE source=? ORDER BY symbol",(source,)).fetchall()
    return [row["symbol"] for row in rows]


def list_symbols(source):
    with connect() as db:
        rows = db.execute(
            "SELECT DISTINCT symbol FROM observations WHERE source=? ORDER BY symbol", (source,)
        ).fetchall()
    return [row["symbol"] for row in rows]


def fetch_alerts(source, limit=100):
    with connect() as db:
        rows = db.execute("""
            SELECT id,symbol,alert_type,severity,metric_value,threshold_value,
                   baseline_window,triggered_at,status,explanation,bucket,baseline_mode,consecutive_bars
            FROM alerts WHERE source=? ORDER BY triggered_at DESC LIMIT ?
        """, (source, max(1, min(int(limit), 500)))).fetchall()
        system_rows = db.execute("""SELECT id, 'ALL' AS symbol, alert_type, severity,
            NULL AS metric_value, threshold_value, NULL AS baseline_window, triggered_at,
            status, explanation, NULL AS bucket, NULL AS baseline_mode, NULL AS consecutive_bars
            FROM system_alerts WHERE source=? ORDER BY triggered_at DESC LIMIT ?""",
            (source, max(1, min(int(limit), 500)))).fetchall()
    combined = [dict(row) for row in rows] + [dict(row) for row in system_rows]
    return sorted(combined, key=lambda row: row["triggered_at"], reverse=True)[:max(1, min(int(limit), 500))]


def count_active_alerts_by_symbol(source, symbols):
    clean=sorted({str(symbol).upper() for symbol in symbols if symbol})
    if not clean:return {}
    placeholders=",".join("?" for _ in clean)
    with connect() as db:
        rows=db.execute(f"SELECT symbol,COUNT(*) AS n FROM alerts WHERE source=? AND status='active' AND symbol IN ({placeholders}) GROUP BY symbol",
                         [source,*clean]).fetchall()
    return {row["symbol"]:int(row["n"]) for row in rows}
def add_demo_tick():
    """Append one clearly synthetic snapshot per default demo symbol."""
    import random

    bases = {"FPT": 128000.0, "HPG": 27500.0, "VPB": 20500.0}
    now = datetime.now(timezone.utc)
    inserted = 0
    with connect() as db:
        for symbol, base in bases.items():
            previous = db.execute("""
                SELECT bid_price_1 FROM observations
                WHERE source='demo' AND symbol=?
                ORDER BY received_at_utc DESC LIMIT 1
            """, (symbol,)).fetchone()
            last_bid = previous["bid_price_1"] if previous else base
            bid1 = round(max(1, last_bid + random.randint(-5, 5) * max(1, base * 0.0001)), 2)
            spread = max(1.0, round(base * random.uniform(0.00025, 0.0014), 2))
            ask1 = round(bid1 + spread, 2)
            bid_vols = [random.randint(100, 6000) for _ in range(3)]
            ask_vols = [random.randint(100, 6000) for _ in range(3)]
            received = now.isoformat()
            raw = {
                "symbol": symbol,
                "bidPrice1": bid1, "bidVol1": bid_vols[0],
                "bidPrice2": round(bid1 - spread * 0.5, 2), "bidVol2": bid_vols[1],
                "bidPrice3": round(bid1 - spread, 2), "bidVol3": bid_vols[2],
                "askPrice1": ask1, "askVol1": ask_vols[0],
                "askPrice2": round(ask1 + spread * 0.5, 2), "askVol2": ask_vols[1],
                "askPrice3": round(ask1 + spread, 2), "askVol3": ask_vols[2],
                "demo_generated_at": received,
            }
            observation = {
                "symbol": symbol,
                "source_timestamp": None,
                "received_at_utc": received,
                "validation_status": "valid",
                "validation_reason": None,
                "raw_payload": raw,
            }
            for level in range(1, 4):
                observation[f"bid_price_{level}"] = raw[f"bidPrice{level}"]
                observation[f"bid_volume_{level}"] = raw[f"bidVol{level}"]
                observation[f"ask_price_{level}"] = raw[f"askPrice{level}"]
                observation[f"ask_volume_{level}"] = raw[f"askVol{level}"]
            if _insert(db, observation, "demo") is not None:
                inserted += 1
    return inserted


def upsert_securities_catalog(records, source="ssi"):
    now = datetime.now(config.TIMEZONE).isoformat(timespec="seconds")
    saved = 0
    with connect() as db:
        for board, item in records:
            get = (lambda key, default=None: item.get(key, default)) if isinstance(item, dict) else (lambda key, default=None: getattr(item, key, default))
            symbol = str(get("symbol", "") or "").strip().upper()
            stock_type = str(get("stock_type", "") or "").strip()
            if not symbol or stock_type.lower() != "stock":
                continue
            db.execute("""INSERT INTO securities_catalog(source,symbol,board,name_vi,name_en,stock_type,listed_shares,first_trading_date,last_trading_date,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,symbol) DO UPDATE SET board=excluded.board,
                name_vi=excluded.name_vi,name_en=excluded.name_en,stock_type=excluded.stock_type,
                listed_shares=excluded.listed_shares,first_trading_date=excluded.first_trading_date,
                last_trading_date=excluded.last_trading_date,updated_at=excluded.updated_at""",
                (source,symbol,board,str(get("symbol_name_vi", "") or ""),
                 str(get("symbol_name_en", "") or ""),stock_type,
                 get("listed_shares"),str(get("first_trading_date", "") or ""),
                 str(get("last_trading_date", "") or ""),now))
            saved += 1
    return saved


def list_catalog_symbols(source="ssi"):
    with connect() as db:
        rows=db.execute("SELECT symbol FROM securities_catalog WHERE source=? ORDER BY symbol",(source,)).fetchall()
    return [row["symbol"] for row in rows]


def fetch_latest_historical_page(search=None, page=1, page_size=50, source="ssi", exclude_date=None):
    page=max(1,int(page)); page_size=max(1,min(int(page_size),200)); offset=(page-1)*page_size
    where=" WHERE c.source=?"; args=[source]
    if search:
        where += " AND (c.symbol LIKE ? OR c.name_vi LIKE ? OR c.name_en LIKE ?)"
        term=f"%{search.strip()}%"; args.extend([term,term,term])
    history_filter=" AND h.trading_date<>?" if exclude_date else ""
    history_args=([str(exclude_date).replace("-","/")[:10]] if exclude_date else [])
    with connect() as db:
        total=db.execute("SELECT COUNT(*) FROM securities_catalog c"+where,args).fetchone()[0]
        rows=db.execute("""WITH latest AS (
            SELECT h.*,ROW_NUMBER() OVER(PARTITION BY h.symbol ORDER BY h.trading_date DESC) AS rn
            FROM historical_prices h WHERE h.source=? AND h.timeframe='1d'"""+history_filter+"""
        ) SELECT c.symbol,c.name_vi,c.name_en,c.board,l.trading_date,l.close_price,l.volume,l.fetched_at
          FROM securities_catalog c LEFT JOIN latest l ON l.symbol=c.symbol AND l.rn=1"""+where+
          " ORDER BY c.symbol LIMIT ? OFFSET ?",[source,*history_args,*args, page_size,offset]).fetchall()
    return {"total":total,"page":page,"page_size":page_size,"rows":[dict(row) for row in rows]}


def insert_observation_batch(observations, source="ssi"):
    counts={"inserted":0,"duplicate":0,"valid":0,"invalid":0,"last_persisted_at":None}
    if not observations:return counts
    refresh_groups={}
    with connect() as db:
        for observation in observations:
            observation_id=_insert(db,observation,source,refresh_bar=False)
            if observation_id is None: counts["duplicate"]+=1
            else:
                counts["inserted"]+=1
                counts["valid" if observation.get("validation_status")=="valid" else "invalid"]+=1
                stamp=observation.get("received_at_vn") or observation.get("received_at_utc") or ""
                try: minute=datetime.fromisoformat(stamp.replace("Z","+00:00")).astimezone(config.TIMEZONE).replace(second=0,microsecond=0).isoformat(timespec="minutes")
                except (ValueError,TypeError): minute=None
                if minute: refresh_groups[(observation["symbol"],minute)]=(observation,observation_id)
                counts["last_persisted_at"]=observation.get("received_at_utc") or counts["last_persisted_at"]
        for observation,observation_id in refresh_groups.values():
            _refresh_bar(db,observation,source,calculate_metrics(observation),observation_id)
    return counts


def insert_trade_batch(trades, source="ssi"):
    counts={"inserted":0,"duplicate":0,"invalid":0,"last_persisted_at":None}
    if not trades:return counts
    with connect() as db:
        for trade in trades:
            symbol=str(trade.get("symbol") or "").strip().upper()
            price=trade.get("price"); quantity=trade.get("quantity")
            if not symbol or price is None or quantity is None or float(price)<=0 or float(quantity)<=0:
                counts["invalid"]+=1
                continue
            digest=payload_hash({key:trade.get(key) for key in (
                "symbol","source_timestamp","price","quantity","side","total_volume"
            )})
            cursor=db.execute("""INSERT OR IGNORE INTO trade_ticks (
                source,symbol,source_timestamp,source_ts_vn,received_at_utc,received_at_vn,
                price,quantity,side,total_volume,open_price,high_price,low_price,average_price,
                source_delay_seconds,validation_status,payload_hash
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
                source,symbol,trade.get("source_timestamp"),trade.get("source_ts_vn"),
                trade["received_at_utc"],trade["received_at_vn"],float(price),float(quantity),
                trade.get("side"),trade.get("total_volume"),trade.get("open_price"),
                trade.get("high_price"),trade.get("low_price"),trade.get("average_price"),
                trade.get("source_delay_seconds"),trade.get("validation_status","valid"),digest
            ))
            if cursor.rowcount:
                counts["inserted"]+=1
                counts["last_persisted_at"]=trade["received_at_utc"]
            else:
                counts["duplicate"]+=1
    return counts


def fetch_recent_trades(source="ssi", symbol=None, limit=100):
    limit=max(1,min(int(limit),1000))
    sql="""SELECT symbol,source_timestamp,source_ts_vn,received_at_utc,received_at_vn,
        price,quantity,side,total_volume,open_price,high_price,low_price,average_price,
        source_delay_seconds,validation_status
        FROM trade_ticks WHERE source=?"""
    args=[source]
    if symbol:
        sql+=" AND symbol=?";args.append(symbol.upper())
    sql+=" ORDER BY received_at_vn DESC,id DESC LIMIT ?";args.append(limit)
    with connect() as db:
        rows=db.execute(sql,args).fetchall()
    return [dict(row) for row in reversed(rows)]


def list_catalog_boards(source="ssi"):
    with connect() as db:
        rows=db.execute("SELECT DISTINCT board FROM securities_catalog WHERE source=? AND board IS NOT NULL ORDER BY board",(source,)).fetchall()
    return [row["board"] for row in rows]


def fetch_market_breadth(limit=90, source="ssi", start_date=None, end_date=None):
    limit=max(2,min(int(limit),10000))
    with connect() as db:
        rows=db.execute("""WITH daily AS (
            SELECT symbol,trading_date,close_price,volume,
              LAG(close_price) OVER(PARTITION BY symbol ORDER BY trading_date) AS prior_close
            FROM historical_prices WHERE source=? AND timeframe='1d' AND close_price IS NOT NULL
        ), breadth AS (
            SELECT trading_date,
              SUM(CASE WHEN close_price>prior_close THEN 1 ELSE 0 END) AS advancing,
              SUM(CASE WHEN close_price<prior_close THEN 1 ELSE 0 END) AS declining,
              SUM(CASE WHEN close_price=prior_close THEN 1 ELSE 0 END) AS unchanged,
              SUM(COALESCE(volume,0)) AS total_volume
            FROM daily WHERE prior_close IS NOT NULL GROUP BY trading_date
        ) SELECT * FROM breadth WHERE (? IS NULL OR replace(trading_date,'/','-')>=?)
          AND (? IS NULL OR replace(trading_date,'/','-')<=?) ORDER BY trading_date DESC LIMIT ?""",
          (source,start_date,start_date,end_date,end_date,limit)).fetchall()
    return list(reversed([dict(row) for row in rows]))


def fetch_market_ohlc_sessions(limit=60, source="ssi", start_date=None, end_date=None):
    """Aggregate common-stock OHLC using indexed per-symbol date ranges."""
    limit=max(1,min(int(limit),10000))
    # SSI dates are zero-padded YYYY/MM/DD strings; normalize input once to
    # preserve index use on (source,symbol,timeframe,trading_date).
    start_db=str(start_date).replace("-","/")[:10] if start_date else None
    end_db=str(end_date).replace("-","/")[:10] if end_date else None
    bounded_filters=[]
    range_params=[]
    if start_db:
        bounded_filters.append("AND h.trading_date>=?")
        range_params.append(start_db)
    if end_db:
        bounded_filters.append("AND h.trading_date<=?")
        range_params.append(end_db)
    bounded_filter=" ".join(bounded_filters)
    final_filters=[]
    final_params=[]
    if start_db:
        final_filters.append("trading_date>=?")
        final_params.append(start_db)
    if end_db:
        final_filters.append("trading_date<=?")
        final_params.append(end_db)
    final_filter=("WHERE "+" AND ".join(final_filters)) if final_filters else ""
    if start_db:
        prior_cte="""prior AS (
          SELECT h.symbol,h.trading_date,h.value,h.volume,h.fetched_at,h.close_price
          FROM securities s CROSS JOIN historical_prices h
          WHERE h.source=? AND h.symbol=s.symbol AND h.timeframe='1d'
            AND h.trading_date=(
              SELECT MAX(p.trading_date) FROM historical_prices p
              WHERE p.source=? AND p.symbol=s.symbol AND p.timeframe='1d'
                AND p.trading_date<?
            )
        )"""
        prior_params=[source,source,start_db]
    else:
        prior_cte="prior AS (SELECT symbol,trading_date,value,volume,fetched_at,close_price FROM historical_prices WHERE 0)"
        prior_params=[]
    query=f"""WITH securities AS (
          SELECT symbol FROM securities_catalog
          WHERE source=? AND lower(trim(stock_type))='stock'
        ), bounded AS (
          SELECT h.symbol,h.trading_date,h.value,h.volume,h.fetched_at,h.close_price
          FROM securities s CROSS JOIN historical_prices h
          WHERE h.source=? AND h.symbol=s.symbol AND h.timeframe='1d' {bounded_filter}
        ), {prior_cte}, selected_daily AS (
          SELECT * FROM bounded UNION ALL SELECT * FROM prior
        ), common_daily AS (
          SELECT symbol,trading_date,value,volume,fetched_at,close_price,
              LAG(close_price) OVER(PARTITION BY symbol ORDER BY trading_date) AS prior_close
          FROM selected_daily
        ), sessions AS (
          SELECT trading_date,
              SUM(value) AS total_value, SUM(volume) AS total_volume,
              SUM(CASE WHEN close_price>prior_close THEN 1 ELSE 0 END) AS advancing,
              SUM(CASE WHEN close_price<prior_close THEN 1 ELSE 0 END) AS declining,
              SUM(CASE WHEN close_price=prior_close THEN 1 ELSE 0 END) AS unchanged,
              COUNT(DISTINCT CASE WHEN value IS NOT NULL THEN symbol END) AS value_symbols,
              COUNT(DISTINCT CASE WHEN volume IS NOT NULL THEN symbol END) AS volume_symbols,
              COUNT(DISTINCT symbol) AS symbol_count, MAX(fetched_at) AS data_as_of
          FROM common_daily GROUP BY trading_date
        )
        SELECT * FROM sessions {final_filter}
        ORDER BY trading_date DESC LIMIT ?"""
    params=(source,source,*range_params,*prior_params,*final_params,limit)
    with connect() as db:
        rows=db.execute(query,params).fetchall()
    return list(reversed([dict(row) for row in rows]))


def historical_symbols_fetched_on(day, source="ssi"):
    prefix=str(day)+"%"
    with connect() as db:
        rows=db.execute("SELECT DISTINCT symbol FROM historical_prices WHERE source=? AND fetched_at LIKE ?",(source,prefix)).fetchall()
    return {row["symbol"] for row in rows}


def historical_symbols_with_date(trading_date, source="ssi"):
    """Return symbols with a daily OHLC row for the requested trading date."""
    day=str(trading_date).replace("-", "/")[:10]
    with connect() as db:
        rows=db.execute("SELECT DISTINCT symbol FROM historical_prices WHERE source=? AND timeframe='1d' AND trading_date=?",
                        (source,day)).fetchall()
    return {row["symbol"] for row in rows}


def historical_session_coverage(trading_date, source="ssi"):
    day=str(trading_date).replace("-","/")[:10]
    with connect() as db:
        row=db.execute("""SELECT COUNT(DISTINCT h.symbol) present,
            (SELECT COUNT(*) FROM securities_catalog c WHERE c.source=? AND lower(c.stock_type)='stock') expected
            FROM historical_prices h JOIN securities_catalog c ON c.source=h.source AND c.symbol=h.symbol
            WHERE h.source=? AND h.timeframe='1d' AND h.trading_date=? AND lower(c.stock_type)='stock'""",
            (source,source,day)).fetchone()
    return {"present":int(row["present"] or 0),"expected":int(row["expected"] or 0)}


def record_session_close(session_date, source="ssi"):
    with connect() as db:
        stats=db.execute("""SELECT COUNT(*) bars,COUNT(DISTINCT b.symbol) symbols
            FROM bars_1m b JOIN securities_catalog c ON c.source=b.source AND c.symbol=b.symbol
            WHERE b.source=? AND b.session_date=? AND lower(c.stock_type)='stock'""",(source,session_date)).fetchone()
        expected=db.execute("SELECT COUNT(*) FROM securities_catalog WHERE source=? AND lower(stock_type)='stock'",(source,)).fetchone()[0]
        db.execute("""INSERT INTO sessions(session_date,source,status,bar_count,symbol_count,expected_symbols,coverage_ratio,closed_at)
            VALUES(?,?,'closed_ohlc_pending',?,?,?,?,?)
            ON CONFLICT(session_date,source) DO UPDATE SET status='closed_ohlc_pending',bar_count=excluded.bar_count,
            symbol_count=excluded.symbol_count,expected_symbols=excluded.expected_symbols,coverage_ratio=excluded.coverage_ratio,closed_at=excluded.closed_at""",
            (session_date,source,int(stats["bars"] or 0),int(stats["symbols"] or 0),int(expected or 0),
             (int(stats["symbols"] or 0)/int(expected)) if expected else 0,datetime.now(config.TIMEZONE).isoformat(timespec="seconds")))


def update_session_ohlc_status(session_date, source="ssi"):
    coverage=historical_session_coverage(session_date,source)
    complete=coverage["expected"]>0 and coverage["present"]>=coverage["expected"]
    with connect() as db:
        db.execute("""INSERT INTO sessions(session_date,source,status,ohlc_symbols,ohlc_expected,ohlc_synced_at)
            VALUES(?,?, ?,?,?,?) ON CONFLICT(session_date,source) DO UPDATE SET status=excluded.status,
            ohlc_symbols=excluded.ohlc_symbols,ohlc_expected=excluded.ohlc_expected,ohlc_synced_at=excluded.ohlc_synced_at""",
            (session_date,source,"complete" if complete else "ohlc_incomplete",coverage["present"],coverage["expected"],
             datetime.now(config.TIMEZONE).isoformat(timespec="seconds")))
    return {**coverage,"complete":complete}


def get_session_record(session_date, source="ssi"):
    with connect() as db:
        row=db.execute("SELECT * FROM sessions WHERE session_date=? AND source=?",(session_date,source)).fetchone()
    return dict(row) if row else None


def count_historical_prices(source="ssi"):
    with connect() as db:
        return db.execute("SELECT COUNT(*) FROM historical_prices WHERE source=?",(source,)).fetchone()[0]


def fetch_market_leaders(limit=20, source="ssi", as_of=None):
    limit=max(5,min(int(limit),100))
    as_of_clause=" AND trading_date<=?" if as_of else ""
    params=(source,as_of,source,limit) if as_of else (source,source,limit)
    with connect() as db:
        rows=db.execute(f"""WITH ranked AS (
            SELECT symbol,trading_date,close_price,volume,value,
              ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY trading_date DESC) AS rn
            FROM historical_prices WHERE source=? AND timeframe='1d' AND close_price IS NOT NULL{as_of_clause}
        ), paired AS (
            SELECT symbol,
              MAX(CASE WHEN rn=1 THEN trading_date END) AS trading_date,
              MAX(CASE WHEN rn=1 THEN close_price END) AS close_price,
              MAX(CASE WHEN rn=2 THEN close_price END) AS prior_close,
              MAX(CASE WHEN rn=1 THEN volume END) AS volume,
              MAX(CASE WHEN rn=1 THEN value END) AS traded_value
            FROM ranked WHERE rn<=2 GROUP BY symbol HAVING MAX(CASE WHEN rn=2 THEN close_price END) IS NOT NULL
        ) SELECT c.symbol,c.name_vi,c.board,p.trading_date,p.close_price,p.prior_close,p.volume,
            COALESCE(p.traded_value,p.close_price*p.volume) AS traded_value,
            CASE WHEN p.prior_close>0 THEN (p.close_price-p.prior_close)*100.0/p.prior_close END AS change_pct
          FROM paired p LEFT JOIN securities_catalog c ON c.source=? AND c.symbol=p.symbol
          ORDER BY traded_value DESC LIMIT ?""",params).fetchall()
    return [dict(row) for row in rows]


