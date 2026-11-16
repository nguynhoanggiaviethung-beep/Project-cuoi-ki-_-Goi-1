import os
from pathlib import Path
from datetime import timedelta, timezone, time

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
# Fixed, shared liquidity classification thresholds. These are project screening
# conventions based on each symbol's own historical distribution, not exchange rules.
FAVORABLE_SPREAD_PERCENTILE = 25.0
STRESS_SPREAD_PERCENTILE = 90.0
FAVORABLE_DEPTH_PERCENTILE = 75.0
STRESS_DEPTH_PERCENTILE = 10.0
# P90/P10 tail estimates need enough independent prior sessions in the same phase.
BASELINE_MIN_OBSERVATIONS = 120
BASELINE_MIN_BARS = 120
BASELINE_SESSIONS = 120
MIN_BAR_SNAPSHOTS = int(os.getenv("MIN_BAR_SNAPSHOTS", "1"))
ALERT_CONSECUTIVE_BARS = int(os.getenv("ALERT_CONSECUTIVE_BARS", "3"))
ALERT_COOLDOWN_MIN = int(os.getenv("ALERT_COOLDOWN_MIN", "15"))
MARKET_TURNOVER_SPIKE_MULTIPLIER = max(1.0, float(os.getenv("MARKET_TURNOVER_SPIKE_MULTIPLIER", "1.4")))
MIN_BARS_FOR_RANKING = max(1, int(os.getenv("MIN_BARS_FOR_RANKING", os.getenv("RANKING_MIN_BARS", "30"))))
# Backward-compatible name retained for existing callers.
RANKING_MIN_BARS = MIN_BARS_FOR_RANKING
RANKING_MIN_SNAPSHOTS = max(200, int(os.getenv("RANKING_MIN_SNAPSHOTS", "200")))
# Alert thresholds alias the same fixed classification thresholds used by watchlists.
SPREAD_PERCENTILE = STRESS_SPREAD_PERCENTILE
DEPTH_PERCENTILE = STRESS_DEPTH_PERCENTILE
IMBALANCE_THRESHOLD = float(os.getenv("IMBALANCE_THRESHOLD", "0.60"))
RECONNECT_MAX_SECONDS = int(os.getenv("RECONNECT_MAX_SECONDS", "30"))
MAX_EVENT_DELAY_SECONDS = max(1.0, float(os.getenv("MAX_EVENT_DELAY_SECONDS", "30")))
CLOCK_SKEW_TOLERANCE_SECONDS = max(0.0, float(os.getenv("CLOCK_SKEW_TOLERANCE_SECONDS", "5")))
HISTORY_REST_MIN_INTERVAL_SECONDS = max(0.0, float(os.getenv("HISTORY_REST_MIN_INTERVAL_SECONDS", "0.5")))
HISTORY_REST_MAX_RETRIES = max(0, min(8, int(os.getenv("HISTORY_REST_MAX_RETRIES", "4"))))
HISTORY_REST_MAX_BACKOFF_SECONDS = max(1.0, float(os.getenv("HISTORY_REST_MAX_BACKOFF_SECONDS", "30")))
HISTORY_START_DATE = os.getenv("HISTORY_START_DATE", "2000/01/01").strip() or "2000/01/01"
HISTORY_REST_PAGE_SIZE = max(10, min(1000, int(os.getenv("HISTORY_REST_PAGE_SIZE", "1000"))))
HISTORY_REST_MAX_PAGES = max(1, min(10, int(os.getenv("HISTORY_REST_MAX_PAGES", "10"))))
MARKET_HOLIDAYS = {item.strip() for item in os.getenv("MARKET_HOLIDAYS", "").split(",") if item.strip()}
MARKET_CLOCK_OVERRIDE = os.getenv("MARKET_CLOCK_OVERRIDE", "").strip()
MARKET_CLOCK_SPEED = max(0.1, float(os.getenv("MARKET_CLOCK_SPEED", "1")))
MARKET_REFRESH_SECONDS = max(5, min(10, int(os.getenv("MARKET_REFRESH_SECONDS", "5"))))
# Schedules follow SSI's stock trading rules and HNX's market FAQ; intervals are half-open.
# https://www.ssi.com.vn/khach-hang-ca-nhan/quy-dinh-giao-dich/quy-dinh-giao-dich-co-phieu
# https://upcom.hnx.vn/vi-vn/hoi-dap.html
MARKET_SESSION_SCHEDULES = {
    "HOSE": ((time(9,0),time(9,15),"IN_SESSION","ATO"),(time(9,15),time(11,30),"IN_SESSION","CONTINUOUS"),
             (time(11,30),time(13,0),"LUNCH_BREAK",None),(time(13,0),time(14,30),"IN_SESSION","CONTINUOUS"),
             (time(14,30),time(14,45),"IN_SESSION","ATC")),
    "HNX": ((time(9,0),time(11,30),"IN_SESSION","CONTINUOUS"),(time(11,30),time(13,0),"LUNCH_BREAK",None),
            (time(13,0),time(14,30),"IN_SESSION","CONTINUOUS"),(time(14,30),time(14,45),"IN_SESSION","ATC")),
    "UPCOM": ((time(9,0),time(11,30),"IN_SESSION","CONTINUOUS"),(time(11,30),time(13,0),"LUNCH_BREAK",None),
              (time(13,0),time(15,0),"IN_SESSION","CONTINUOUS")),
}
