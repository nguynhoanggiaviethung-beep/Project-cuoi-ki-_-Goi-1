"""Shared in-memory runtime status for data collectors."""
import threading

from . import config


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
