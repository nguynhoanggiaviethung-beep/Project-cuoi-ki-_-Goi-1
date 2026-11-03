import base64
import binascii
import json
import os
import threading
import time

import requests
import websocket

from . import config, database
from .analytics import map_message


class RuntimeState:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "mode": "demo",
            "connection_status": "demo",
            "last_message_at": None,
            "messages_received": 0,
            "rows_valid": 0,
            "rows_invalid": 0,
            "duplicate_messages": 0,
            "reconnect_count": 0,
            "last_error": None,
            "watchlist": config.WATCHLIST,
        }

    def update(self, **changes):
        with self._lock:
            self._data.update(changes)

    def increment(self, key, amount=1):
        with self._lock:
            self._data[key] = self._data.get(key, 0) + amount

    def snapshot(self):
        with self._lock:
            return dict(self._data)


class NeoCollector:
    def __init__(self, runtime):
        self.runtime = runtime
        self._stop = threading.Event()
        self._thread = None
        self._ws = None
        self._lock = threading.Lock()
        self._run_id = None

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self._run_id = database.start_collector_run()
            self.runtime.update(
                mode="live", connection_status="connecting",
                last_error=None, messages_received=0,
                rows_valid=0, rows_invalid=0, duplicate_messages=0,
                watchlist=config.WATCHLIST,
            )
            self._thread = threading.Thread(
                target=self._run, name="sli-neo-collector", daemon=True
            )
            self._thread.start()
            return True

    def stop(self):
        self._stop.set()
        socket_app = self._ws
        if socket_app:
            try:
                socket_app.close()
            except Exception:
                pass
        self.runtime.update(connection_status="disconnected")
        database.finish_collector_run(self._run_id, "stopped", self.runtime.snapshot())
        self._run_id = None

    def _token(self):
        configured_token = os.getenv("NEO_ACCESS_TOKEN", "").strip()
        if configured_token:
            # Read only the JWT expiry locally; the server still validates its signature.
            try:
                payload = configured_token.split(".")[1]
                payload += "=" * (-len(payload) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload))
                expires_at = int(claims["exp"])
                if expires_at > int(time.time()):
                    return configured_token
            except (IndexError, KeyError, TypeError, ValueError, binascii.Error):
                pass

        username = os.getenv("VPBS_USERNAME")
        password = os.getenv("VPBS_PASSWORD")
        if not username or not password:
            if configured_token:
                raise RuntimeError("NEO_ACCESS_TOKEN hết hạn hoặc không hợp lệ; cập nhật token trong .env.")
            raise RuntimeError("Thiếu NEO_ACCESS_TOKEN hoặc VPBS_USERNAME/VPBS_PASSWORD trong .env.")
        response = requests.post(
            config.TOKEN_URL,
            headers={"Content-Type": "application/json"},
            json={"username": username, "password": password},
            timeout=20,
        )
        try:
            result = response.json()
        except ValueError:
            raise RuntimeError(
                f"Token endpoint trả HTTP {response.status_code}, không phải JSON."
            )
        if response.ok and result.get("status") == 1:
            token = (result.get("data") or {}).get("access_token")
            if token:
                return token
        code = result.get("code")
        raise RuntimeError(
            f"Không lấy được access token (HTTP {response.status_code}, code={code})."
        )

    def _run(self):
        delay = 1
        while not self._stop.is_set():
            try:
                token = self._token()
                auth_received = threading.Event()

                def on_open(socket_app):
                    socket_app.send(json.dumps({"action": "auth", "data": token}))

                    def auth_timeout():
                        if not auth_received.is_set() and not self._stop.is_set():
                            self.runtime.update(
                                connection_status="error",
                                last_error="Đã gửi auth nhưng NEO không phản hồi trong 15 giây.",
                            )
                            socket_app.close()

                    timer = threading.Timer(15, auth_timeout)
                    timer.daemon = True
                    timer.start()

                def on_message(socket_app, message):
                    self.runtime.increment("messages_received")
                    try:
                        payload = json.loads(message)
                    except (json.JSONDecodeError, TypeError):
                        self.runtime.increment("rows_invalid")
                        self.runtime.update(last_error="WebSocket gửi message không phải JSON.")
                        database.log_ingestion_error("websocket_message_not_json")
                        return

                    if isinstance(payload, dict) and payload.get("type") == "auth":
                        auth_received.set()
                        if payload.get("status") == "success":
                            self.runtime.update(connection_status="connected", last_error=None)
                            socket_app.send(json.dumps({
                                "type": "sub",
                                "channel": "stockInfo",
                                "listId": config.WATCHLIST,
                            }))
                        else:
                            self.runtime.update(
                                connection_status="error",
                                last_error="NEO từ chối xác thực WebSocket.",
                            )
                            socket_app.close()
                        return

                    observations = map_message(payload)
                    if not observations:
                        self.runtime.increment("rows_invalid")
                        self.runtime.update(
                            last_error="Payload chưa map được; cần xác minh cấu trúc JSON thực tế."
                        )
                        database.log_ingestion_error("payload_shape_unmapped", payload)
                        return
                    for observation in observations:
                        self.runtime.update(last_message_at=observation["received_at_utc"])
                        result = database.insert_observation(observation, "live")
                        if result["duplicate"]:
                            self.runtime.increment("duplicate_messages")
                        elif observation["validation_status"] == "valid":
                            self.runtime.increment("rows_valid")
                        else:
                            self.runtime.increment("rows_invalid")
                            database.log_ingestion_error(
                                observation.get("validation_reason") or observation["validation_status"],
                                observation.get("raw_payload"), observation.get("symbol"),
                            )

                def on_error(_socket_app, error):
                    self.runtime.update(
                        connection_status="error",
                        last_error=str(error)[:240],
                    )

                def on_close(_socket_app, _status, _message):
                    if not self._stop.is_set():
                        current = self.runtime.snapshot().get("connection_status")
                        if current != "error":
                            self.runtime.update(connection_status="reconnecting")

                socket_app = websocket.WebSocketApp(
                    config.NEO_WS_URL,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                self._ws = socket_app
                self.runtime.update(connection_status="connecting")
                socket_app.run_forever(ping_interval=25, ping_timeout=10)
                if self._stop.is_set():
                    break
                self.runtime.increment("reconnect_count")
                self.runtime.update(connection_status="reconnecting")
                self._stop.wait(delay)
                delay = min(delay * 2, config.RECONNECT_MAX_SECONDS)
            except Exception as error:
                self.runtime.update(connection_status="error", last_error=str(error)[:240])
                if self._stop.is_set():
                    break
                self.runtime.increment("reconnect_count")
                self._stop.wait(delay)
                delay = min(delay * 2, config.RECONNECT_MAX_SECONDS)

        self._ws = None
        self.runtime.update(connection_status="disconnected")
        database.finish_collector_run(self._run_id, "stopped", self.runtime.snapshot())
        self._run_id = None
