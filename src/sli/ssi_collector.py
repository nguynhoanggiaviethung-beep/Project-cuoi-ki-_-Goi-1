"""SSI FastConnect market data collector for the full listed-stock universe."""
import dataclasses
import logging
import os
import queue
import threading
import time
from datetime import datetime, timedelta, time as datetime_time, timezone

from ssi_sdk import Auth, Config, Data, Stream
from ssi_sdk.enums import Board
from ssi_sdk.exceptions import AuthenticationError, ReauthenticationRequired
from ssi_sdk.models.streaming import QuoteMessage, TradeMessage

from . import config, database
from .analytics import map_message, number_or_none, _to_local_timestamp


_LOG = logging.getLogger(__name__)

def _enable_automatic_data_reauthentication(auth):
    """Retry SSI FastConnect Data login when an SDK refresh token expires.

    The Data API authenticates with the configured consumer credentials. The SDK's
    generic token manager otherwise stops and asks for OTP when refresh is rejected.
    Keep OTP out of the worker and retry login once through the SDK's normal auth API.
    """
    manager = auth.token_manager
    original_refresh = manager.refresh
    original_ensure = manager.ensure_authenticated
    lock = threading.RLock()

    def authenticate_again():
        with lock:
            # A parallel REST/stream callback may already have renewed the token.
            if manager.access_token and not manager.is_token_expired:
                return manager.token
            token = auth.authenticate()
            _LOG.info("SSI FastConnect Data access token renewed; restoring stream.")
            return token

    def refresh_with_login_fallback():
        try:
            return original_refresh()
        except ReauthenticationRequired:
            return authenticate_again()

    def ensure_with_login_fallback(*args, **kwargs):
        try:
            return original_ensure(*args, **kwargs)
        except ReauthenticationRequired:
            return authenticate_again().access_token
        except AuthenticationError:
            # A failed refresh can clear the SDK token before the stream asks again.
            if manager.token is None:
                return authenticate_again().access_token
            raise

    manager.refresh = refresh_with_login_fallback
    manager.ensure_authenticated = ensure_with_login_fallback
    return auth


class SsiCollector:
    BOARDS = (Board.HOSE, Board.HNX, Board.UPCOM)

    def __init__(self, runtime, worker_id=None):
        self.runtime=runtime; self._stop=threading.Event(); self._thread=None
        self.worker_id=worker_id
        self._stream=None; self._lock=threading.Lock(); self._writer=None
        self._history_thread=None; self._pending_by_symbol={}; self._pending_lock=threading.Lock()
        self._pending_signal=threading.Event(); self._pending_trades=queue.Queue(maxsize=20000)
        self._board_by_symbol={}

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive(): return False
            self._stop.clear()
            with self._pending_lock:self._pending_by_symbol.clear()
            while not self._pending_trades.empty():
                try:self._pending_trades.get_nowait()
                except queue.Empty:break
            self._pending_signal.clear()
            self.runtime.update(mode="ssi",connection_status="connecting",provider="SSI FastConnect · all listed stocks",
                last_error=None,messages_received=0,rows_valid=0,rows_invalid=0,duplicate_messages=0,queue_dropped=0,reconnect_count=0,
                queue_depth=0,queue_coalesced=0,last_message_at=None,last_persisted_at=None,
                trade_messages_received=0,trade_messages_saved=0,trade_messages_dropped=0,trade_messages_filtered=0,
                last_trade_received_at=None,last_trade_persisted_at=None,trade_queue_depth=0,
                historical_prices_rows=0,historical_symbols_total=0,historical_symbols_processed=0,
                watchlist=config.WATCHLIST,universe_status="loading",subscription_status="pending",
                subscription_ack_count=0,subscription_topics=[],subscribed_boards=[],subscription_error=None,
                trade_subscription_status="pending",trade_subscription_topics=[])
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

    @staticmethod
    def _retryable_history_error(exc):
        response=getattr(exc,"response",None)
        status=getattr(exc,"status_code",None) or getattr(response,"status_code",None)
        try: status=int(status) if status is not None else None
        except (TypeError,ValueError): status=None
        text=(type(exc).__name__+" "+str(exc)).lower()
        transient=status==429 or (status is not None and 500<=status<600) or any(
            token in text for token in ("timeout","timed out","connection reset","connection aborted",
                                        "connecterror","networkerror","remoteprotocolerror",
                                        "temporarily unavailable","rate limit","too many requests","http 429")
        )
        return transient,response

    def _fetch_history_with_retry(self, market_data, symbol, start, end, page=1, size=None):
        size=size or config.HISTORY_REST_PAGE_SIZE
        for attempt in range(config.HISTORY_REST_MAX_RETRIES+1):
            try:
                return market_data.get_ohlc_1day_historical(
                    symbol,from_date=start,to_date=end,page=page,size=size
                )
            except Exception as exc:
                retryable,response=self._retryable_history_error(exc)
                if not retryable or attempt>=config.HISTORY_REST_MAX_RETRIES:
                    raise
                retry_after=None
                headers=getattr(response,"headers",None)
                if headers:
                    try: retry_after=float(headers.get("Retry-After"))
                    except (TypeError,ValueError): retry_after=None
                backoff=min(2**attempt,config.HISTORY_REST_MAX_BACKOFF_SECONDS)
                delay=max(backoff,retry_after or 0.0)
                if self._stop.wait(delay):
                    raise RuntimeError("Đã dừng đồng bộ OHLC trong lúc chờ retry.") from exc

    def _sync_history(self, auth, symbols):
        md=Data(auth).market_data
        today=datetime.now(config.TIMEZONE).date()
        full_history_start=config.HISTORY_START_DATE
        end=today.strftime("%Y/%m/%d 23:59:59")
        today_key=today.isoformat()
        today_data_key=today.strftime("%Y/%m/%d")

        def sync_symbols(target_symbols, skip_fetched_today=False):
            already_fetched=(database.historical_symbols_fetched_on(today_key,source="ssi")
                             if skip_fetched_today else set())
            backfill_states=database.historical_backfill_states("ssi",full_history_start)
            full_history_done={symbol for symbol,item in backfill_states.items() if item["status"]=="complete"}
            self.runtime.update(historical_symbols_total=len(target_symbols),historical_symbols_processed=0,
                                historical_prices_rows=database.count_historical_prices("ssi"),
                                historical_backfill_start=full_history_start,
                                historical_backfill_page_size=config.HISTORY_REST_PAGE_SIZE,
                                historical_backfill_max_pages=config.HISTORY_REST_MAX_PAGES)
            last_request_at=None
            processed=0
            for index,symbol in enumerate(target_symbols,1):
                if self._stop.is_set():break
                full_backfill=symbol not in full_history_done
                if full_backfill or symbol not in already_fetched:
                    # First pass starts at market inception. Later passes resume
                    # from the last date that SSI history was requested through.
                    request_start=full_history_start if full_backfill else backfill_states[symbol]["requested_to"]
                    if len(request_start)==10 and request_start[4]=="/":
                        request_start += " 00:00:00"
                    request_end=end
                    max_pages=config.HISTORY_REST_MAX_PAGES
                    collected=[];pages_fetched=0;page_limit_reached=False
                    try:
                        for page in range(1,max_pages+1):
                            if self._stop.is_set():break
                            if last_request_at is not None:
                                remaining=config.HISTORY_REST_MIN_INTERVAL_SECONDS-(time.monotonic()-last_request_at)
                                if remaining>0 and self._stop.wait(remaining):break
                            try:
                                page_rows=self._fetch_history_with_retry(md,symbol,request_start,request_end,
                                    page=page,size=config.HISTORY_REST_PAGE_SIZE)
                            finally:
                                last_request_at=time.monotonic()
                            page_rows=list(page_rows or []);pages_fetched+=1;collected.extend(page_rows)
                            if len(page_rows)<config.HISTORY_REST_PAGE_SIZE:
                                break
                            if page==max_pages:
                                page_limit_reached=True
                        if self._stop.is_set():
                            break
                        database.upsert_historical_prices(symbol,collected,source="ssi")
                        if full_backfill:
                            status="page_limit_reached" if page_limit_reached else "complete"
                            database.set_historical_backfill_state(symbol,full_history_start,today_data_key,status,
                                pages_fetched,len(collected),source="ssi")
                            if page_limit_reached:
                                database.log_ingestion_error(
                                    f"ssi_history_page_limit_reached: SSI trả đủ {max_pages} trang; chưa thể xác nhận đã hết lịch sử.",
                                    symbol=symbol,source="ssi")
                            full_history_done.add(symbol) if not page_limit_reached else None
                        elif not page_limit_reached:
                            database.set_historical_backfill_state(symbol,full_history_start,today_data_key,"complete",
                                pages_fetched,len(collected),source="ssi")
                        else:
                            database.log_ingestion_error(
                                f"ssi_history_incremental_page_limit_reached: SSI trả đủ {max_pages} trang trong lần cập nhật tăng dần.",
                                symbol=symbol,source="ssi")
                    except Exception as exc:
                        if full_backfill:
                            database.set_historical_backfill_state(symbol,full_history_start,today_data_key,"error",
                                pages_fetched,len(collected),source="ssi")
                        if not self._stop.is_set():
                            database.log_ingestion_error(f"ssi_history_{type(exc).__name__}: {str(exc)[:180]}",symbol=symbol,source="ssi")
                processed=index
                if index%10==0 or index==len(target_symbols):
                    self.runtime.update(historical_symbols_processed=index,historical_prices_rows=database.count_historical_prices("ssi"),
                        historical_prices_updated_at=datetime.now(config.TIMEZONE).isoformat(timespec="seconds"),historical_last_symbol=symbol)
            self.runtime.update(historical_symbols_processed=processed,historical_prices_rows=database.count_historical_prices("ssi"),
                historical_prices_updated_at=datetime.now(config.TIMEZONE).isoformat(timespec="seconds"))

        sync_symbols(symbols,skip_fetched_today=True)

        # The first history pass can finish before the exchange publishes the
        # final daily bar (for example, when the worker starts during lunch).
        # After the session closes, backfill only symbols that still lack today's
        # OHLC row. Keep this in the collector worker and preserve REST pacing.
        if self._stop.is_set() or not any(
            database.is_session_open(today_key, board.value) for board in self.BOARDS
        ):
            return
        refresh_after=datetime.combine(today,datetime_time(15,0),tzinfo=config.TIMEZONE)
        wait_seconds=max(0.0,(refresh_after-datetime.now(config.TIMEZONE)).total_seconds())
        if wait_seconds and self._stop.wait(wait_seconds):
            return
        database.record_session_close(today_key,source="ssi")
        retry_slots=[datetime.combine(today,datetime_time(hour,minute),tzinfo=config.TIMEZONE)
                     for hour,minute in ((15,0),(15,30),(16,0),(17,0),(18,0))]
        next_morning=today+timedelta(days=1)
        while next_morning.weekday()>=5 or next_morning.isoformat() in config.MARKET_HOLIDAYS:
            next_morning+=timedelta(days=1)
        retry_slots.append(datetime.combine(next_morning,datetime_time(9,0),tzinfo=config.TIMEZONE))
        now=datetime.now(config.TIMEZONE)
        first_attempt=True
        for retry_at in retry_slots:
            if self._stop.is_set():break
            if retry_at<=now:
                if not first_attempt:continue
            else:
                if self._stop.wait((retry_at-now).total_seconds()):break
            missing_symbols=sorted(set(symbols)-database.historical_symbols_with_date(today_data_key,source="ssi"))
            if not missing_symbols:
                sync=database.update_session_ohlc_status(today_key,source="ssi")
                self.runtime.update(historical_sync_phase="complete" if sync["complete"] else "ohlc_incomplete",
                                    historical_symbols_processed=sync["present"],historical_symbols_total=sync["expected"])
                return
            self.runtime.update(historical_sync_phase="after_close_retry",historical_symbols_processed=0,
                                historical_symbols_total=len(missing_symbols),historical_retry_at=retry_at.isoformat(timespec="seconds"))
            sync_symbols(missing_symbols)
            sync=database.update_session_ohlc_status(today_key,source="ssi")
            if sync["complete"]:
                self.runtime.update(historical_sync_phase="complete",historical_symbols_processed=sync["present"],
                                    historical_symbols_total=sync["expected"])
                return
            first_attempt=False
            now=datetime.now(config.TIMEZONE)
        self.runtime.update(historical_sync_phase="awaiting_next_session" if not self._stop.is_set() else "stopped")

    def _history_scheduler(self, auth, symbols):
        """Keep SSI OHLC history current once per configured trading day."""
        last_sync_date = None
        while not self._stop.is_set():
            now = datetime.now(config.TIMEZONE)
            session_date = now.date().isoformat()
            is_weekday = now.weekday() < 5
            is_configured_holiday = session_date in config.MARKET_HOLIDAYS
            if is_weekday and not is_configured_holiday and session_date != last_sync_date:
                _LOG.info("Bắt đầu đồng bộ OHLC SSI cho ngày %s.", session_date)
                try:
                    self._sync_history(auth, symbols)
                    last_sync_date = session_date
                except Exception as exc:
                    message = f"SSI OHLC daily sync {type(exc).__name__}: {str(exc)[:180]}"
                    _LOG.exception("Đồng bộ OHLC SSI ngày %s thất bại; sẽ thử lại sau 5 phút.", session_date)
                    database.log_ingestion_error(message, source="ssi")
                    self.runtime.update(historical_sync_phase="retry_pending", last_error=message)
                    if self._stop.wait(300):
                        break
            self._stop.wait(30)

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

    @staticmethod
    def _trade_dict(message):
        q=dataclasses.asdict(message) if dataclasses.is_dataclass(message) else message if isinstance(message,dict) else None
        if not q:return None
        symbol=str(q.get("symbol") or "").strip().upper()
        price=number_or_none(q.get("price"));quantity=number_or_none(q.get("quantity"))
        if not symbol or price is None or quantity is None or price<=0 or quantity<=0:return None
        source_timestamp=q.get("trading_time") or q.get("timestamp")
        source_ts_vn=_to_local_timestamp(source_timestamp)
        received_utc=datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        received_vn=datetime.now(config.TIMEZONE).isoformat(timespec="milliseconds")
        delay=None
        if source_ts_vn:
            try:delay=(datetime.fromisoformat(received_vn)-datetime.fromisoformat(source_ts_vn)).total_seconds()
            except ValueError:pass
        side=q.get("side")
        if hasattr(side,"value"):side=side.value
        return {"symbol":symbol,"source_timestamp":source_timestamp,"source_ts_vn":source_ts_vn,
            "received_at_utc":received_utc,"received_at_vn":received_vn,"price":price,
            "quantity":quantity,"side":str(side) if side is not None else None,
            "total_volume":number_or_none(q.get("total_volume")),
            "open_price":number_or_none(q.get("open")),"high_price":number_or_none(q.get("high")),
            "low_price":number_or_none(q.get("low")),"average_price":number_or_none(q.get("avg")),
            "source_delay_seconds":delay,"validation_status":"valid" if source_ts_vn else "missing_source_timestamp"}

    def _write_loop(self):
        while not self._stop.is_set() or self._pending_by_symbol or not self._pending_trades.empty():
            self._pending_signal.wait(timeout=.25)
            with self._pending_lock:
                symbols=list(self._pending_by_symbol)[:250]
                batch=[self._pending_by_symbol.pop(symbol) for symbol in symbols]
                depth=len(self._pending_by_symbol)
            trade_batch=[]
            for _ in range(500):
                try:trade_batch.append(self._pending_trades.get_nowait())
                except queue.Empty:break
            if not depth and self._pending_trades.empty():self._pending_signal.clear()
            self.runtime.update(queue_depth=depth)
            self.runtime.update(trade_queue_depth=self._pending_trades.qsize())
            if batch:
                try:
                    counts=database.insert_observation_batch(batch,"ssi")
                    self.runtime.increment("duplicate_messages",counts["duplicate"])
                    self.runtime.increment("rows_valid",counts["valid"]);self.runtime.increment("rows_invalid",counts["invalid"])
                    if counts.get("last_persisted_at"):
                        self.runtime.update(last_persisted_at=counts["last_persisted_at"])
                except Exception as exc:self.runtime.update(last_error=f"SQLite quote batch: {str(exc)[:180]}")
            if trade_batch:
                try:
                    counts=database.insert_trade_batch(trade_batch,"ssi")
                    self.runtime.increment("trade_messages_saved",counts["inserted"])
                    self.runtime.increment("trade_messages_dropped",counts["invalid"])
                    if counts.get("last_persisted_at"):
                        self.runtime.update(last_trade_persisted_at=counts["last_persisted_at"])
                except Exception as exc:self.runtime.update(last_error=f"SQLite trade batch: {str(exc)[:180]}")

    def _run(self):
        auth=None;stream=None;run_id=database.start_collector_run(self.worker_id,os.getpid());run_status="failed"
        self.runtime.update(collector_run_id=run_id,worker_id=self.worker_id)
        try:
            auth=Auth(self._make_config());auth.authenticate()
            _enable_automatic_data_reauthentication(auth)
            symbols=self._load_catalog(auth)
            if not symbols:raise RuntimeError("SSI catalog không trả về cổ phiếu thường; không dùng dữ liệu giả lập.")
            self._writer=threading.Thread(target=self._write_loop,name="sli-ssi-db-writer",daemon=True);self._writer.start()
            stream=Stream(auth);client=stream.streaming;self._stream=client
            def on_connection(event):
                state=getattr(event,"state","")
                if state=="reconnected":self.runtime.increment("reconnect_count")
                if state in {"connected","reconnected"}:self.runtime.update(connection_status="connected",last_error=None)
                elif state in {"failed","disconnected"} and not self._stop.is_set():
                    error=getattr(event,"error",None);self.runtime.update(connection_status="error" if state=="failed" else "disconnected",last_error=str(error)[:240] if error else None)
            def on_data(message):
                if isinstance(message,TradeMessage) or (isinstance(message,dict) and {"symbol","price","quantity"}.issubset(message)):
                    self.runtime.increment("trade_messages_received")
                    trade=self._trade_dict(message)
                    if not trade:
                        self.runtime.increment("trade_messages_dropped");return
                    # SSI also publishes index aggregates on trade topics. Keep only listed stocks.
                    if trade["symbol"] not in self._board_by_symbol:
                        self.runtime.increment("trade_messages_filtered");return
                    try:self._pending_trades.put_nowait(trade)
                    except queue.Full:
                        try:self._pending_trades.get_nowait()
                        except queue.Empty:pass
                        self.runtime.increment("trade_messages_dropped")
                        try:self._pending_trades.put_nowait(trade)
                        except queue.Full:return
                    self.runtime.update(last_trade_received_at=trade["received_at_utc"],trade_queue_depth=self._pending_trades.qsize())
                    self._pending_signal.set();return
                # SSI shares many event types on one callback; only order-book quotes and public trades are stored.
                if not isinstance(message,QuoteMessage) and not (isinstance(message,dict) and ("bids" in message or "bid_prices" in message)):return
                self.runtime.increment("messages_received")
                item=self._quote_dict(message)
                if not item:self.runtime.increment("rows_invalid");return
                observation_list=map_message({"channel":"quote","data":item})
                if not observation_list:self.runtime.increment("rows_invalid");return
                for observation in observation_list:
                    observation["market"]=self._board_by_symbol.get(observation["symbol"],"HOSE")
                    symbol=observation["symbol"]
                    with self._pending_lock:
                        replaced=symbol in self._pending_by_symbol
                        self._pending_by_symbol[symbol]=observation
                        depth=len(self._pending_by_symbol)
                    self.runtime.update(last_message_at=observation["received_at_utc"],queue_depth=depth)
                    if replaced:self.runtime.increment("queue_coalesced")
                    self._pending_signal.set()
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
                trade_topics=[topic for topic in data_topics if topic.lower().startswith("trade.")]
                accepted=[board.value for board in self.BOARDS if any(board.value.lower() in topic.lower() for topic in quote_topics)]
                accepted_trade=[board.value for board in self.BOARDS if any(board.value.lower() in topic.lower() for topic in trade_topics)]
                expected=[board.value for board in self.BOARDS]
                subscription_status="confirmed" if set(accepted)==set(expected) else "partial" if accepted else "unconfirmed"
                trade_subscription_status="confirmed" if set(accepted_trade)==set(expected) else "partial" if accepted_trade else "unconfirmed"
                self.runtime.update(subscription_status=subscription_status,subscription_topics=data_topics,
                    subscribed_boards=accepted,subscription_error=None if subscription_status=="confirmed" else "SSI không xác nhận đầy đủ quote cho cả ba sàn.",
                    trade_subscription_status=trade_subscription_status,trade_subscription_topics=trade_topics)
            except Exception as exc:
                self.runtime.update(subscription_status="unverified",subscription_error=f"Không đọc được danh sách subscription SSI: {str(exc)[:180]}")
            # Daily OHLC is fetched per symbol; run incrementally while the stream remains active.
            self._history_thread=threading.Thread(target=self._history_scheduler,args=(auth,symbols),name="sli-ssi-history",daemon=True);self._history_thread.start()
            self.runtime.update(connection_status="connected",last_error=None)
            database.set_collector_run_status(run_id,"running")
            run_status="stopped"
            while not self._stop.is_set():
                client.wait(timeout_ms=1000)
                if not client.is_connected and not self._stop.is_set() and client.last_error:self.runtime.update(connection_status="error",last_error=str(client.last_error)[:240])
        except Exception as exc:
            run_status="failed"
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
            database.finish_collector_run(run_id,run_status,self.runtime.snapshot())
