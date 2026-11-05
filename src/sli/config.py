import os
from pathlib import Path
from datetime import timedelta, timezone

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT_DIR / "database" / "sli.sqlite3")))
WATCHLIST = [item.strip().upper() for item in os.getenv("WATCHLIST", "FPT,HPG,VPB").split(",") if item.strip()]
SYMBOL_MARKETS = {
    item.split(":", 1)[0].strip().upper(): item.split(":", 1)[1].strip().upper()
    for item in os.getenv("SYMBOL_MARKETS", "").split(",")
    if ":" in item and item.split(":", 1)[0].strip() and item.split(":", 1)[1].strip()
}
# Vietnam is UTC+07:00 year-round; fixed offset avoids a platform tzdata dependency.
TIMEZONE = timezone(timedelta(hours=7), name="Asia/Ho_Chi_Minh")
STALE_AFTER_SECONDS = int(os.getenv("STALE_AFTER_SECONDS", "120"))
BASELINE_MIN_OBSERVATIONS = int(os.getenv("BASELINE_MIN_OBSERVATIONS", "100"))
BASELINE_MIN_BARS = int(os.getenv("BASELINE_MIN_BARS", "60"))
BASELINE_SESSIONS = int(os.getenv("BASELINE_SESSIONS", "10"))
MIN_BAR_SNAPSHOTS = int(os.getenv("MIN_BAR_SNAPSHOTS", "1"))
ALERT_CONSECUTIVE_BARS = int(os.getenv("ALERT_CONSECUTIVE_BARS", "3"))
ALERT_COOLDOWN_MIN = int(os.getenv("ALERT_COOLDOWN_MIN", "15"))
RANKING_MIN_BARS = int(os.getenv("RANKING_MIN_BARS", "30"))
RANKING_MIN_SNAPSHOTS = int(os.getenv("RANKING_MIN_SNAPSHOTS", "30"))
SPREAD_PERCENTILE = float(os.getenv("SPREAD_PERCENTILE", "90"))
DEPTH_PERCENTILE = float(os.getenv("DEPTH_PERCENTILE", "10"))
IMBALANCE_THRESHOLD = float(os.getenv("IMBALANCE_THRESHOLD", "0.60"))
RECONNECT_MAX_SECONDS = int(os.getenv("RECONNECT_MAX_SECONDS", "30"))
