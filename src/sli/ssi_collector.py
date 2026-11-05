"""SSI FastConnect market data collector for the full listed-stock universe."""
import dataclasses
import os
import queue
import threading
from datetime import date, datetime, timedelta

from ssi_sdk import Auth, Config, Data, Stream
from ssi_sdk.enums import Board
from ssi_sdk.models.streaming import QuoteMessage

from . import config, database
from .analytics import map_message


class SsiCollector:
    BOARDS = (Board.HOSE, Board.HNX, Board.UPCOM)

    def __init__(self, runtime):
        self.runtime=runtime; self._stop=threading.Event(); self._thread=None
        self._stream=None; self._lock=threading.Lock(); self._writer=None
        self._history_thread=None; self._queue=queue.Queue(maxsize=30000); self._board_by_symbol={}

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive(): return False
            self._stop.clear()
            self.runtime.update(mode="ssi",connection_status="connecting",provider="SSI FastConnect · all listed stocks",
                last_error=None,messages_received=0,rows_valid=0,rows_invalid=0,duplicate_messages=0,
                historical_prices_rows=0,historical_symbols_total=0,historical_symbols_processed=0,
                watchlist=config.WATCHLIST,universe_status="loading",subscription_status="pending",
                subscription_ack_count=0,subscription_topics=[],subscribed_boards=[],subscription_error=None)
            self._thread=threading.Thread(target=self._run,name="sli-ssi-stream",daemon=True);self._thread.start();return True

    def stop(self):
        self._stop.set()
        if self._stream:
            try:self._stream.disconnect()
            except Exception:pass
        if self.runtime.snapshot().get("mode")=="ssi":self.runtime.update(connection_status="disconnected")

    @staticmethod
    def _make_config():
        key=os.getenv("SSI_API_KEY","").strip();secret=os.getenv("SSI_API_SECRET","").strip()
        if not key or not secret: raise RuntimeError("Thiếu SSI_API_KEY hoặc SSI_API_SECRET trong file .env.")
        return Config(api_key=key,api_secret=secret,auto_reconnect=True)

    def _load_catalog(self, auth):
        md=Data(auth).market_data; records=[]
        for board in self.BOARDS:
            if self._stop.is_set(): break
            items=md.get_securities_info_by_board(board)
            records.extend((board.value,item) for item in items)
        count=database.upsert_securities_catalog(records,source="ssi")
        self._board_by_symbol={}
        for board,item in records:
            symbol=str(getattr(item,"symbol","") or "").upper()
            if symbol and str(getattr(item,"stock_type","") or "").lower()=="stock":self._board_by_symbol[symbol]=board
        symbols=database.list_catalog_symbols("ssi")
        self.runtime.update(universe_status="ready",universe_symbols=len(symbols),catalog_updated_at=datetime.now(config.TIMEZONE).isoformat(timespec="seconds"))
        return symbols

    def _sync_history(self, auth, symbols):
        md=Data(auth).market_data; today=date.today(); start=(today-timedelta(days=90)).strftime("%Y/%m/%d 00:00:00");end=today.strftime("%Y/%m/%d 23:59:59")
        today_key=datetime.now(config.TIMEZONE).date().isoformat()
        already_fetched=database.historical_symbols_fetched_on(today_key,source="ssi")
        self.runtime.update(historical_symbols_total=len(symbols),historical_symbols_processed=0,
                            historical_prices_rows=database.count_historical_prices("ssi"))
        for index,symbol in enumerate(symbols,1):
            if self._stop.is_set():break
            if symbol not in already_fetched:
                try:
                    bars=md.get_ohlc_1day_historical(symbol,from_date=start,to_date=end,page=1,size=100)
                    database.upsert_historical_prices(symbol,bars,source="ssi")
                except Exception as exc:
                    database.log_ingestion_error(f"ssi_history_{type(exc).__name__}: {str(exc)[:180]}",symbol=symbol,source="ssi")
            if index%10==0 or index==len(symbols):
                self.runtime.update(historical_symbols_processed=index,historical_prices_rows=database.count_historical_prices("ssi"),
                    historical_prices_updated_at=datetime.now(config.TIMEZONE).isoformat(timespec="seconds"),historical_last_symbol=symbol)
        self.runtime.update(historical_symbols_processed=min(len(symbols),index if symbols else 0),historical_prices_rows=database.count_historical_prices("ssi"),
            historical_prices_updated_at=datetime.now(config.TIMEZONE).isoformat(timespec="seconds"))

    @staticmethod
    def _quote_dict(message):
        q=dataclasses.asdict(message) if dataclasses.is_dataclass(message) else message if isinstance(message,dict) else None
        if not q:return None
        symbol=str(q.get("symbol") or "").strip().upper()
        if not symbol:return None
        item={"symbol":symbol,"timestamp":q.get("trading_time") or q.get("timestamp")}
        for side in ("bid","ask"):
            prices=q.get(f"{side}_prices") or [];volumes=q.get(f"{side}_volumes") or []
            for level in range(1,4):
                i=level-1; item[f"{side}Price{level}"]=prices[i] if i<len(prices) else None;item[f"{side}Vol{level}"]=volumes[i] if i<len(volumes) else None
        return item

    def _write_loop(self):
        batch=[]
        while not self._stop.is_set() or not self._queue.empty():
            try:batch.append(self._queue.get(timeout=.25))
            except queue.Empty:pass
            if batch and (len(batch)>=250 or self._stop.is_set() or self._queue.empty()):
                try:
                    counts=database.insert_observation_batch(batch,"ssi")
                    self.runtime.increment("duplicate_messages",counts["duplicate"])
                    self.runtime.increment("rows_valid",counts["valid"]);self.runtime.increment("rows_invalid",counts["invalid"])
                    if counts["last_message_at"]:self.runtime.update(last_message_at=counts["last_message_at"])
                except Exception as exc:self.runtime.update(last_error=f"SQLite batch: {str(exc)[:180]}")
                batch=[]

    def _run(self):
        auth=None;stream=None
        try:
            auth=Auth(self._make_config());auth.authenticate()
            symbols=self._load_catalog(auth)
            if not symbols:raise RuntimeError("SSI catalog không trả về cổ phiếu thường; không dùng dữ liệu giả lập.")
            self._writer=threading.Thread(target=self._write_loop,name="sli-ssi-db-writer",daemon=True);self._writer.start()
            stream=Stream(auth);client=stream.streaming;self._stream=client
            def on_connection(event):
                state=getattr(event,"state","")
                if state in {"connected","reconnected"}:self.runtime.update(connection_status="connected",last_error=None)
                elif state in {"failed","disconnected"} and not self._stop.is_set():
                    error=getattr(event,"error",None);self.runtime.update(connection_status="error" if state=="failed" else "disconnected",last_error=str(error)[:240] if error else None)
            def on_data(message):
                # SSI shares many event types on one callback; only true order-book quote events are stored.
                if not isinstance(message,QuoteMessage) and not (isinstance(message,dict) and ("bids" in message or "bid_prices" in message)):return
                self.runtime.increment("messages_received")
                item=self._quote_dict(message)
                if not item:self.runtime.increment("rows_invalid");return
                observation_list=map_message({"channel":"quote","data":item})
                if not observation_list:self.runtime.increment("rows_invalid");return
                for observation in observation_list:
                    observation["market"]=self._board_by_symbol.get(observation["symbol"],"HOSE")
                    try:self._queue.put_nowait(observation)
                    except queue.Full:self.runtime.increment("rows_invalid")
            client.on_connection=on_connection;client.on_data=on_data;client.connect()
            ack_lock=threading.Lock(); ack_messages=[]
            def on_subscription_ack(payload):
                # SSI sends these on its ACK route; keep only response metadata, never credentials.
                with ack_lock:
                    ack_messages.append(payload)
                    recent=ack_messages[-9:]
                self.runtime.update(subscription_ack_count=len(ack_messages),subscription_acks=recent)
            self.runtime.update(subscription_status="subscribing",subscription_error=None)
            client.subscribe_board(list(self.BOARDS),on_response=on_subscription_ack)
            try:
                subscriptions=client.list_subscription(timeout_ms=5000)
                data_topics=[str(topic) for topic in subscriptions.get("data",[])]
                quote_topics=[topic for topic in data_topics if topic.lower().startswith("quote.")]
                accepted=[board.value for board in self.BOARDS if any(board.value.lower() in topic.lower() for topic in quote_topics)]
                expected=[board.value for board in self.BOARDS]
                subscription_status="confirmed" if set(accepted)==set(expected) else "partial" if accepted else "unconfirmed"
                self.runtime.update(subscription_status=subscription_status,subscription_topics=data_topics,
                    subscribed_boards=accepted,subscription_error=None if subscription_status=="confirmed" else "SSI không xác nhận đầy đủ quote cho cả ba sàn.")
            except Exception as exc:
                self.runtime.update(subscription_status="unverified",subscription_error=f"Không đọc được danh sách subscription SSI: {str(exc)[:180]}")
            # Daily OHLC is fetched per symbol; run incrementally while the stream remains active.
            self._history_thread=threading.Thread(target=self._sync_history,args=(auth,symbols),name="sli-ssi-history",daemon=True);self._history_thread.start()
            self.runtime.update(connection_status="connected",last_error=None)
            while not self._stop.is_set():
                client.wait(timeout_ms=1000)
                if not client.is_connected and not self._stop.is_set() and client.last_error:self.runtime.update(connection_status="error",last_error=str(client.last_error)[:240])
        except Exception as exc:
            if not self._stop.is_set():self.runtime.update(connection_status="error",last_error=str(exc)[:240]);database.log_ingestion_error(str(exc)[:240],source="ssi")
        finally:
            if stream:
                try:stream.streaming.disconnect()
                except Exception:pass
            if self._history_thread and self._history_thread.is_alive():self._history_thread.join(timeout=10)
            if self._writer and self._writer.is_alive():self._writer.join(timeout=10)
            if auth:
                try:auth.close()
                except Exception:pass
            self._stream=None
            if self.runtime.snapshot().get("mode")=="ssi" and self._stop.is_set():self.runtime.update(connection_status="disconnected")
