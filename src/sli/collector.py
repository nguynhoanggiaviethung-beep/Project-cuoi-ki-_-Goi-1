"""Shared in-memory runtime status for data collectors."""
import threading

from . import config


class RuntimeState:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "mode": "ssi",
            "connection_status": "disconnected",
            "last_message_at": None,
            "last_persisted_at": None,
            "messages_received": 0,
            "rows_valid": 0,
            "rows_invalid": 0,
            "duplicate_messages": 0,
            "queue_dropped": 0,
            "queue_depth": 0,
            "queue_coalesced": 0,
            "trade_messages_received": 0,
            "trade_messages_saved": 0,
            "trade_messages_dropped": 0,
            "trade_messages_filtered": 0,
            "last_trade_received_at": None,
            "last_trade_persisted_at": None,
            "trade_queue_depth": 0,
            "subscription_status": "pending",
            "subscription_topics": [],
            "subscribed_boards": [],
            "subscription_ack_count": 0,
            "subscription_error": None,
            "quote_persistence_status": "pending",
            "trade_subscription_status": "pending",
            "trade_subscription_topics": [],
            "trade_persistence_status": "pending",
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
