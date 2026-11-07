"""Reclassify saved SSI UPCoM quotes after 14:30 and rebuild their minute bars.

Dry-run by default. Pass --apply to update only saved source='ssi' observations;
raw payloads and original source/receive timestamps are left untouched.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.sli import database, market_rules
from src.sli.analytics import calculate_metrics, validate_observation

TIME_FLAGS = {
    "late_message", "future_source_timestamp", "invalid_source_timestamp",
}


def _observation(row):
    item = dict(row)
    item["session_phase"] = market_rules.session_phase(
        item["received_at_vn"], item["market"]
    )
    old_flags = set(json.loads(item.get("flags") or "[]"))
    item["time_flags"] = sorted(old_flags & TIME_FLAGS)
    validation = validate_observation(item)
    item["validation_status"] = validation["status"]
    item["validation_reason"] = validation.get("reason")
    item["flags"] = validation.get("flags", [])
    return item


def reconcile(apply=False):
    with database.connect() as db:
        rows = db.execute("""SELECT * FROM observations
            WHERE source='ssi' AND upper(market)='UPCOM'
              AND received_at_vn IS NOT NULL
              AND substr(received_at_vn,12,8) >= '14:30:00'
              AND substr(received_at_vn,12,8) < '15:00:00'
            ORDER BY received_at_vn,id""").fetchall()
        candidates = []
        affected = set()
        dates = set()
        for row in rows:
            item = _observation(row)
            if not database.is_session_open(item["received_at_vn"][:10], "UPCOM", db):
                continue
            stamp = datetime.fromisoformat(item["received_at_vn"])
            minute = stamp.replace(second=0, microsecond=0).isoformat(timespec="minutes")
            affected.add((item["symbol"], minute))
            dates.add(stamp.date().isoformat())
            old_flags = set(json.loads(row["flags"] or "[]"))
            if (row["session_phase"] != item["session_phase"]
                    or row["validation_status"] != item["validation_status"]
                    or old_flags != set(item["flags"])):
                candidates.append((row, item))

        print(f"SSI UPCoM observations in 14:30-15:00: {len(rows)}")
        print(f"Rows requiring phase/validation correction: {len(candidates)}")
        if not apply:
            print("Dry-run only. Re-run with --apply to rebuild saved bars.")
            return {"observations": len(rows), "corrected": len(candidates), "bars": 0}

        for old, item in candidates:
            db.execute("""UPDATE observations SET session_phase=?,flags=?,
                validation_status=?,validation_reason=? WHERE id=? AND source='ssi'""",
                (item["session_phase"], json.dumps(item["flags"]),
                 item["validation_status"], item["validation_reason"], old["id"]))

        # Remove only the affected UPCoM closing-window bars, then aggregate them
        # again from the corrected persisted observations via the shared service.
        for day in dates:
            db.execute("""DELETE FROM bars_1m WHERE source='ssi' AND market='UPCOM'
                AND session_date=? AND substr(minute_vn,12,5)>='14:30'
                AND substr(minute_vn,12,5)<'15:00'""", (day,))

        rebuilt = 0
        for symbol, minute in sorted(affected, key=lambda pair: (pair[1], pair[0])):
            # Use saved observations inside the exact minute; no data is synthesized.
            start = datetime.fromisoformat(minute)
            end = start + timedelta(minutes=1)
            row = db.execute("""SELECT * FROM observations WHERE source='ssi'
                AND symbol=? AND received_at_vn>=? AND received_at_vn<?
                ORDER BY received_at_vn DESC,id DESC LIMIT 1""",
                (symbol, start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"))).fetchone()
            if row:
                item = _observation(row)
                database._refresh_bar(db, item, "ssi", calculate_metrics(item), row["id"])
                exists = db.execute("SELECT 1 FROM bars_1m WHERE source='ssi' AND symbol=? AND minute_vn=?",
                                    (symbol, minute)).fetchone()
                rebuilt += bool(exists)

    for day in sorted(dates):
        database.record_session_close(day, "ssi")
    print(f"Corrected observations: {len(candidates)}")
    print(f"Rebuilt minute bars: {rebuilt}")
    print(f"Updated sessions: {', '.join(sorted(dates)) or 'none'}")
    return {"observations": len(rows), "corrected": len(candidates), "bars": rebuilt}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write corrections and rebuild bars")
    args = parser.parse_args()
    reconcile(apply=args.apply)
