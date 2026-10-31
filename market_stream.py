import json
import os
import sys
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests
import websocket
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
TOKEN_URL = "https://external.vpbanks.com.vn/auth/token"
WS_URL = "wss://neoapi.vpbanks.com.vn/stock-stream/broker"
SYMBOLS = [item.strip().upper() for item in os.getenv("NEO_SYMBOLS", "FPT").split(",") if item.strip()]
RUN_SECONDS = 120
OUTPUT = ROOT / "stock_data.jsonl"


def get_access_token():
    username = os.getenv("VPBS_USERNAME")
    password = os.getenv("VPBS_PASSWORD")
    if not username or not password:
        raise RuntimeError("Thiếu VPBS_USERNAME hoặc VPBS_PASSWORD trong .env")

    response = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/json"},
        json={"username": username, "password": password},
        timeout=20,
    )
    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError(f"API token trả về dữ liệu không phải JSON (HTTP {response.status_code}).") from exc

    if response.ok and result.get("status") == 1:
        token = (result.get("data") or {}).get("access_token")
        if token:
            return token
    raise RuntimeError(f"Không lấy được access token (HTTP {response.status_code}, code={result.get('code')}).")


def main():
    if not SYMBOLS:
        raise RuntimeError("Danh sách mã NEO_SYMBOLS đang trống.")

    token = get_access_token()
    counts = Counter()
    authenticated = False
    ws = None

    def on_open(socket_app):
        print("WebSocket đã kết nối; đang xác thực.")
        socket_app.send(json.dumps({"action": "auth", "data": token}))

    def on_message(socket_app, message):
        nonlocal authenticated
        try:
            data = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            print("Message không phải JSON:", message)
            return

        if isinstance(data, dict) and data.get("type") == "auth":
            if data.get("status") == "success":
                authenticated = True
                print("Xác thực WebSocket thành công; subscribe:", SYMBOLS)
                socket_app.send(json.dumps({"type": "sub", "channel": "stockInfo", "listId": SYMBOLS}))
            else:
                print("Xác thực WebSocket thất bại.")
                socket_app.close()
            return

        payload = data.get("data", data) if isinstance(data, dict) else data
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict):
                symbol = item.get("symbol", "UNKNOWN")
                counts[symbol] += 1

        record = {"received_at": datetime.now(timezone.utc).isoformat(), "data": data}
        with OUTPUT.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

        print("\n========== RECEIVED MESSAGE ==========")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"Message #{sum(counts.values())}; số mã nhận được: {len(counts)}; thống kê: {dict(counts)}")

    def on_error(_socket_app, error):
        print("WebSocket error:", error)

    def on_close(_socket_app, status_code, message):
        print(f"WebSocket đóng (status={status_code}).")

    ws = websocket.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    timer = threading.Timer(RUN_SECONDS, ws.close)
    timer.daemon = True
    timer.start()
    try:
        print(f"Theo dõi {', '.join(SYMBOLS)} trong tối đa {RUN_SECONDS} giây qua WSS.")
        ws.run_forever(ping_interval=25, ping_timeout=10)
    finally:
        timer.cancel()

    print(f"Kết thúc: auth={'thành công' if authenticated else 'chưa thành công'}, message={sum(counts.values())}, mã={dict(counts)}")
    if OUTPUT.exists():
        print("JSONL:", OUTPUT)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Lỗi:", error)
        sys.exit(1)
