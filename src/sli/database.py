import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

from . import config
from .analytics import calculate_metrics, payload_hash, quantile

OBS_FIELDS = (
    "bid_price_1", "bid_volume_1", "bid_price_2", "bid_volume_2",
    "bid_price_3", "bid_volume_3", "ask_price_1", "ask_volume_1",
    "ask_price_2", "ask_volume_2", "ask_price_3", "ask_volume_3",
)
METRIC_FIELDS = (
    "mid_price", "spread_bps", "bid_depth_qty", "ask_depth_qty",
    "total_depth_qty", "bid_depth_value", "ask_depth_value",
    "total_depth_value", "queue_imbalance", "value_imbalance",
    "depth_levels_used", "calculated_at",
)


def connect():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
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
        """)
    seed_demo_if_empty()


def _insert(db, observation, source):
    metrics = calculate_metrics(observation)
    raw = observation.get("raw_payload") or {}
    digest = payload_hash(raw)
    columns = [
        "symbol", "source_timestamp", "received_at_utc", *OBS_FIELDS,
        "raw_payload", "payload_hash", "validation_status", "validation_reason", "source",
    ]
    values = [
        observation["symbol"], observation.get("source_timestamp"),
        observation["received_at_utc"], *[observation.get(field) for field in OBS_FIELDS],
        json.dumps(raw, ensure_ascii=False, separators=(",", ":")), digest,
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
    _persist_alerts(db, observation_id, observation, source, metrics)
    return observation_id


def insert_observation(observation, source="live"):
    with connect() as db:
        observation_id = _insert(db, observation, source)
        return {"inserted": observation_id is not None, "duplicate": observation_id is None, "id": observation_id}


def _persist_alerts(db, observation_id, observation, source, metrics):
    history = db.execute("""
        SELECT m.spread_bps, m.total_depth_value
        FROM observations o JOIN metrics m ON m.observation_id=o.id
        WHERE o.source=? AND o.symbol=? AND o.id<>?
        ORDER BY o.received_at_utc DESC LIMIT ?
    """, (source, observation["symbol"], observation_id, config.BASELINE_MIN_OBSERVATIONS)).fetchall()
    triggers = []
    if len(history) >= config.BASELINE_MIN_OBSERVATIONS:
        spreads = [row["spread_bps"] for row in history if row["spread_bps"] is not None]
        depths = [row["total_depth_value"] for row in history if row["total_depth_value"] is not None]
        if len(spreads) >= config.BASELINE_MIN_OBSERVATIONS and metrics["spread_bps"] is not None:
            threshold = quantile(spreads, config.SPREAD_PERCENTILE)
            if threshold is not None and metrics["spread_bps"] > threshold:
                triggers.append(("spread_widening", metrics["spread_bps"], threshold,
                                 f"Spread vượt percentile {config.SPREAD_PERCENTILE:g} của lịch sử mã."))
        if len(depths) >= config.BASELINE_MIN_OBSERVATIONS and metrics["total_depth_value"] is not None:
            threshold = quantile(depths, config.DEPTH_PERCENTILE)
            if threshold is not None and metrics["total_depth_value"] < threshold:
                triggers.append(("depth_drop", metrics["total_depth_value"], threshold,
                                 f"Depth thấp hơn percentile {config.DEPTH_PERCENTILE:g} của lịch sử mã."))
    imbalance = metrics["queue_imbalance"]
    if imbalance is not None and abs(imbalance) >= config.IMBALANCE_THRESHOLD:
        triggers.append(("strong_queue_imbalance", imbalance, config.IMBALANCE_THRESHOLD,
                         "Queue imbalance top 3 vượt ngưỡng; đây không phải dự báo giá."))
    for alert_type, value, threshold, explanation in triggers:
        db.execute("""
            INSERT OR IGNORE INTO alerts
            (observation_id,symbol,alert_type,severity,metric_value,threshold_value,
             baseline_window,triggered_at,status,explanation,source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (observation_id, observation["symbol"], alert_type, "medium", value,
              threshold, len(history), observation["received_at_utc"],
              "active", explanation, source))


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


def fetch_observations(source, symbol=None, limit=500):
    limit = max(1, min(int(limit), 5000))
    query = """
        SELECT o.*, m.mid_price, m.spread_bps, m.bid_depth_qty, m.ask_depth_qty,
               m.total_depth_qty, m.bid_depth_value, m.ask_depth_value,
               m.total_depth_value, m.queue_imbalance, m.value_imbalance,
               m.depth_levels_used, m.calculated_at
        FROM observations o JOIN metrics m ON m.observation_id=o.id WHERE o.source=?
    """
    args = [source]
    if symbol:
        query += " AND o.symbol=?"
        args.append(symbol.upper())
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
                   baseline_window,triggered_at,status,explanation
            FROM alerts WHERE source=? ORDER BY triggered_at DESC LIMIT ?
        """, (source, max(1, min(int(limit), 500)))).fetchall()
    return [dict(row) for row in rows]
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