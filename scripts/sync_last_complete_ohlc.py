"""Fetch missing SSI daily OHLC for one already-closed session (REST only).

Dry-run by default. Pass --apply to request only catalog stocks missing that
date, using the project's configured pacing and retry limits. Does not start
the WebSocket collector or overwrite rows that already exist for the date.
"""
import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ssi_sdk import Auth, Config, Data
from src.sli import config, database
from src.sli.ssi_collector import SsiCollector


def sync(day: str, apply: bool = False):
    normalized = datetime.strptime(day.replace("-", "/"), "%Y/%m/%d").date()
    if normalized.weekday() >= 5 or normalized.isoformat() in config.MARKET_HOLIDAYS:
        raise SystemExit("Ngày yêu cầu không phải ngày giao dịch theo lịch cấu hình.")
    key = normalized.strftime("%Y/%m/%d")
    symbols = database.list_catalog_symbols("ssi")
    present = database.historical_symbols_with_date(key, "ssi")
    missing = sorted(set(symbols) - present)
    print(f"Ngày SSI: {key}")
    print(f"Mã cổ phiếu trong danh mục: {len(symbols)}")
    print(f"Đã có OHLC: {len(set(symbols) & present)}; còn thiếu: {len(missing)}")
    if not apply:
        print("Dry-run. Dùng --apply để chỉ gọi SSI cho các mã còn thiếu.")
        return {"expected": len(symbols), "present": len(set(symbols) & present), "missing": len(missing)}

    auth = Auth(Config(api_key=os.getenv("SSI_API_KEY", "").strip(),
                       api_secret=os.getenv("SSI_API_SECRET", "").strip(),
                       auto_reconnect=True))
    collector = SsiCollector(runtime=None)
    saved_rows = 0
    attempted = 0
    failed = 0
    last_request = None
    try:
        auth.authenticate()
        market_data = Data(auth).market_data
        start = normalized.strftime("%Y/%m/%d 00:00:00")
        end = normalized.strftime("%Y/%m/%d 23:59:59")
        for index, symbol in enumerate(missing, 1):
            if last_request is not None:
                wait = config.HISTORY_REST_MIN_INTERVAL_SECONDS - (time.monotonic() - last_request)
                if wait > 0:
                    time.sleep(wait)
            try:
                bars = collector._fetch_history_with_retry(market_data, symbol, start, end)
                saved_rows += database.upsert_historical_prices(symbol, bars, source="ssi")
            except Exception as exc:
                failed += 1
                database.log_ingestion_error(f"ssi_history_{type(exc).__name__}: {str(exc)[:180]}",
                                             symbol=symbol, source="ssi")
            finally:
                attempted += 1
                last_request = time.monotonic()
            if index % 25 == 0 or index == len(missing):
                print(f"Đã xử lý {index}/{len(missing)} mã; lỗi yêu cầu: {failed}")
    finally:
        try:
            auth.close()
        except Exception:
            pass

    coverage = database.update_session_ohlc_status(normalized.isoformat(), "ssi")
    print(f"Số mã đã gọi SSI: {attempted}; số dòng OHLC ghi nhận: {saved_rows}")
    print(f"Yêu cầu lỗi: {failed}; độ phủ sau đồng bộ: {coverage['present']}/{coverage['expected']}")
    print("Trạng thái phiên: " + ("đầy đủ" if coverage["complete"] else "chưa đầy đủ"))
    return {**coverage, "attempted": attempted, "saved_rows": saved_rows, "failed": failed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="ngày giao dịch YYYY-MM-DD")
    parser.add_argument("--apply", action="store_true", help="gọi SSI REST và ghi phần OHLC còn thiếu")
    args = parser.parse_args()
    sync(args.date, apply=args.apply)
