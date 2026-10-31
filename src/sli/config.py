import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT_DIR / "database" / "sli.sqlite3")))
NEO_WS_URL = os.getenv("NEO_WS_URL", "wss://neoapi.vpbanks.com.vn/stock-stream/broker")
TOKEN_URL = os.getenv("TOKEN_URL", "https://external.vpbanks.com.vn/auth/token")
WATCHLIST = [item.strip().upper() for item in os.getenv("WATCHLIST", "FPT,HPG,VPB").split(",") if item.strip()]
STALE_AFTER_SECONDS = int(os.getenv("STALE_AFTER_SECONDS", "120"))
BASELINE_MIN_OBSERVATIONS = int(os.getenv("BASELINE_MIN_OBSERVATIONS", "100"))
SPREAD_PERCENTILE = float(os.getenv("SPREAD_PERCENTILE", "90"))
DEPTH_PERCENTILE = float(os.getenv("DEPTH_PERCENTILE", "10"))
IMBALANCE_THRESHOLD = float(os.getenv("IMBALANCE_THRESHOLD", "0.60"))
RECONNECT_MAX_SECONDS = int(os.getenv("RECONNECT_MAX_SECONDS", "30"))