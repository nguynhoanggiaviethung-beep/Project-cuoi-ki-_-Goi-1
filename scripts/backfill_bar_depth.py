"""One-time backfill of bar depth from persisted metrics (no raw-field calculation)."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.sli import database
from src.sli.detail_service import backfill_saved_depth_bars

if __name__ == "__main__":
    database.initialize()  # Adds nullable depth columns for older databases.
    print(f"bars_updated={backfill_saved_depth_bars('ssi')}")
