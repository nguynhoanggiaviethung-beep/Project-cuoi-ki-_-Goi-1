import json
import math
import sqlite3
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


def start_collector_run():
    now = datetime.now(config.TIMEZONE).isoformat(timespec="seconds")
    with connect() as db:
        cursor = db.execute("INSERT INTO collector_run_details(started_at,status) VALUES (?, 'connecting')", (now,))
        return cursor.lastrowid


def finish_collector_run(run_id, status, state):
    if not run_id:
        return
    with connect() as db:
        db.execute("""UPDATE collector_run_details SET ended_at=?,status=?,messages_received=?,rows_valid=?,
            rows_invalid=?,duplicate_messages=?,reconnect_count=?,last_message_at=?,last_error=? WHERE id=?""",
            (datetime.now(config.TIMEZONE).isoformat(timespec="seconds"), status,
             state.get("messages_received", 0), state.get("rows_valid", 0), state.get("rows_invalid", 0),
             state.get("duplicate_messages", 0), state.get("reconnect_count", 0),
             state.get("last_message_at"), state.get("last_error"), run_id))


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
    args.append(max(1, min(int(limit), 10000)))
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
        return datetime.fromisoformat(str(session_date)).weekday() < 5
    finally:
        if owns:
            db.close()


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
        errors = db.execute("SELECT COUNT(*) FROM ingestion_errors WHERE source=?", (source,)).fetchone()[0]
        symbols = db.execute("SELECT symbol,COUNT(*) AS n FROM observations WHERE source=? GROUP BY symbol ORDER BY symbol", (source,)).fetchall()
        flag_rows = db.execute("SELECT flags FROM observations WHERE source=?", (source,)).fetchall()
    flag_counts = {}
    for row in flag_rows:
        for flag in json.loads(row[0] or "[]"):
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
    return {"source": source, "observations": total, "valid_observations": valid,
            "valid_rate": valid / total if total else None, "missing_source_timestamp": no_source_time,
            "ingestion_errors": errors, "flags": flag_counts,
            "symbols": [{"symbol": row["symbol"], "observations": row["n"]} for row in symbols]}


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
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA synchronous = NORMAL")
    return db


def initialize():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, source_timestamp TEXT, received_at_utc TEXT NOT NULL,
            bid_price_1 REAL, bid_volume_1 REAL, bid_price_2 REAL, bid_volume_2 REAL,
            bid_price_3 REAL, bid_volume_3 REAL, ask_price_1 REAL, ask_volume_1 REAL,
            ask_price_2 REAL, ask_volume_2 REAL, ask_price_3 REAL, ask_volume_3 REAL,
            raw_payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
            validation_status TEXT NOT NULL, validation_reason TEXT,
            source TEXT NOT NULL CHECK(source IN ('demo','live')),
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
            total_depth_value REAL, queue_imbalance REAL, n_snapshots INTEGER NOT NULL,
            coverage_status TEXT NOT NULL, UNIQUE(source,symbol,minute_vn)
        );
        CREATE INDEX IF NOT EXISTS ix_bars_symbol_minute ON bars_1m(source,symbol,minute_vn);
        CREATE TABLE IF NOT EXISTS session_calendar (
            session_date TEXT NOT NULL, market TEXT NOT NULL, is_open INTEGER NOT NULL DEFAULT 1,
            note TEXT, PRIMARY KEY(session_date,market)
        );
        CREATE TABLE IF NOT EXISTS collector_run_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, ended_at TEXT,
            status TEXT NOT NULL, messages_received INTEGER NOT NULL DEFAULT 0,
            rows_valid INTEGER NOT NULL DEFAULT 0, rows_invalid INTEGER NOT NULL DEFAULT 0,
            duplicate_messages INTEGER NOT NULL DEFAULT 0, reconnect_count INTEGER NOT NULL DEFAULT 0,
            last_message_at TEXT, last_error TEXT
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
        """)
        _migrate_schema(db)
    seed_demo_if_empty()


def _migrate_schema(db):
    additions = {
        "observations": {
            "source_ts_vn": "TEXT", "received_at_vn": "TEXT", "market": "TEXT NOT NULL DEFAULT 'HOSE'",
            "session_phase": "TEXT NOT NULL DEFAULT 'UNKNOWN'", "flags": "TEXT NOT NULL DEFAULT '[]'",
            "ref_price": "REAL", "ceiling_price": "REAL", "floor_price": "REAL",
            "last_price": "REAL", "total_trading_value": "REAL", "total_trading_volume": "REAL",
        },
        "metrics": {"spread_abs": "REAL", "spread_ticks": "REAL", "tick_size": "REAL"},
        "alerts": {"bucket": "TEXT", "baseline_mode": "TEXT", "consecutive_bars": "INTEGER NOT NULL DEFAULT 1"},
    }
    for table, fields in additions.items():
        existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        for field, declaration in fields.items():
            if field not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {field} {declaration}")


def _insert(db, observation, source):
    metrics = calculate_metrics(observation)
    raw = sanitize_payload(observation.get("raw_payload") or {})
    received_vn = observation.get("received_at_vn") or _to_vietnam_time(observation.get("received_at_utc"))
    event_day = (received_vn or "")[:10]
    book_state = {field: observation.get(field) for field in OBS_FIELDS}
    digest = payload_hash({"symbol": observation["symbol"], "market": observation.get("market", "HOSE"),
                           "session_date": event_day, "book": book_state})
    # Keep only invalid payloads and a deterministic ~1% valid sample for audit.
    raw_text = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    keep_raw = observation.get("validation_status") != "valid" or int(digest[:4], 16) < 656
    stored_raw = raw_text if keep_raw else "{}"
    columns = [
        "symbol", "source_timestamp", "source_ts_vn", "received_at_utc", "received_at_vn",
        "market", "session_phase", "flags", "ref_price", "ceiling_price", "floor_price",
        "last_price", "total_trading_value", "total_trading_volume", *OBS_FIELDS,
        "raw_payload", "payload_hash", "validation_status", "validation_reason", "source",
    ]
    values = [
        observation["symbol"], observation.get("source_timestamp"), observation.get("source_ts_vn"),
        observation.get("received_at_utc"), received_vn,
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
    phase = observation.get("session_phase") or market_rules.session_phase(stamp, observation.get("market", "HOSE"))
    if phase not in {"CONTINUOUS_AM", "CONTINUOUS_PM"}:
        return
    if not is_session_open(stamp.date().isoformat(), observation.get("market", "HOSE"), db):
        return
    minute = stamp.replace(second=0, microsecond=0).isoformat(timespec="minutes")
    rows = db.execute("""
        SELECT o.flags, o.validation_status, m.spread_bps, m.spread_ticks,
               m.bid_depth_qty, m.ask_depth_qty, m.total_depth_value, m.queue_imbalance
        FROM observations o JOIN metrics m ON m.observation_id=o.id
        WHERE o.source=? AND o.symbol=? AND o.received_at_vn>=? AND o.received_at_vn<?
    """, (source, observation["symbol"], minute,
          (stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)).isoformat(timespec="minutes"))).fetchall()
    eligible = []
    for row in rows:
        flags = set(json.loads(row["flags"] or "[]"))
        if row["validation_status"] == "valid" and not flags.intersection({"one_sided_limit", "auction_phase", "off_tick", "crossed_book"}):
            eligible.append(row)
    if not eligible:
        return
    def med(field):
        values = [row[field] for row in eligible if row[field] is not None]
        return quantile(values, 50) if values else None
    bucket = market_rules.baseline_bucket(stamp, observation.get("market", "HOSE"))
    db.execute("""
        INSERT INTO bars_1m (source,symbol,minute_vn,session_date,market,session_phase,
          baseline_bucket,spread_bps,spread_ticks,bid_depth_qty,ask_depth_qty,total_depth_value,
          queue_imbalance,n_snapshots,coverage_status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source,symbol,minute_vn) DO UPDATE SET
          spread_bps=excluded.spread_bps,spread_ticks=excluded.spread_ticks,
          bid_depth_qty=excluded.bid_depth_qty,ask_depth_qty=excluded.ask_depth_qty,
          total_depth_value=excluded.total_depth_value,queue_imbalance=excluded.queue_imbalance,
          n_snapshots=excluded.n_snapshots,coverage_status=excluded.coverage_status
    """, (source, observation["symbol"], minute, stamp.date().isoformat(),
          observation.get("market", "HOSE"), phase, bucket, med("spread_bps"), med("spread_ticks"),
          med("bid_depth_qty"), med("ask_depth_qty"), med("total_depth_value"), med("queue_imbalance"),
          len(eligible), "ok" if len(eligible) >= config.MIN_BAR_SNAPSHOTS else "sparse"))
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
    bucket_rows = db.execute(f"SELECT * FROM bars_1m WHERE source=? AND symbol=? AND session_date IN ({placeholders}) AND baseline_bucket=? ORDER BY minute_vn",
                             (*args, bar["baseline_bucket"])).fetchall() if bar["baseline_bucket"] else []
    if len(bucket_rows) >= config.BASELINE_MIN_BARS:
        return bucket_rows, "bucket"
    pooled = db.execute(f"SELECT * FROM bars_1m WHERE source=? AND symbol=? AND session_date IN ({placeholders}) ORDER BY minute_vn", args).fetchall()
    if len(pooled) >= config.BASELINE_MIN_BARS:
        return pooled, "pooled"
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
    spread_hit = spread_limit is not None and len(recent) == k and all(r["spread_bps"] is not None and r["spread_bps"] > spread_limit for r in recent)
    depth_hit = depth_limit is not None and len(recent) == k and all(r["total_depth_value"] is not None and r["total_depth_value"] < depth_limit for r in recent)
    imb_hit = len(recent) == k and all(r["queue_imbalance"] is not None and abs(r["queue_imbalance"]) >= config.IMBALANCE_THRESHOLD for r in recent)
    triggers = []
    if spread_hit:
        triggers.append(("spread_widening", "medium", bar["spread_bps"], spread_limit, "Spread bar vượt P90 baseline cùng mã/cùng pha phiên liên tiếp."))
    if depth_hit:
        triggers.append(("depth_drop", "medium", bar["total_depth_value"], depth_limit, "Depth top 3 thấp hơn P10 baseline cùng mã/cùng pha phiên liên tiếp."))
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
