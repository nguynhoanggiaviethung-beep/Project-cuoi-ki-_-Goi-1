"""Repair sessions bar coverage summaries from persisted SSI bars without stopping the collector."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.sli import database

if __name__ == "__main__":
    result = database.reconcile_session_summaries("ssi")
    print(f"Reconciled {result['sessions_reconciled']} SSI sessions; expected universe: {result['expected_symbols']} stocks.")
