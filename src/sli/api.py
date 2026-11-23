from datetime import datetime, timedelta, timezone
from statistics import median
from functools import lru_cache
import asyncio
import ctypes
import json
import math
import os
import subprocess
import sys
import threading
import time

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, database
from .detail_service import build_detail
from .investor_service import build_watchlist_live, build_positions_live
from .market_context_service import get_market_turnover
from .depth_service import build_market_depth
from .bookwalk import estimate as estimate_bookwalk
from .market_clock import get_clock_now, get_market_state
from .ranking import attach_rank_movement, calculate_symbol_row, is_rankable_stock, rank_rows
from .collector import RuntimeState

STATIC_DIR = config.ROOT_DIR / "frontend"
runtime = RuntimeState()
_demo_thread = None
_demo_stop = threading.Event()
_worker_process = None
_status_alert_state = {}
_market_liquidity_baseline_cache = {}
_market_liquidity_baseline_lock = threading.Lock()
_status_alert_lock = threading.Lock()
_health_snapshot = {"worker_run": None, "last_persisted_at": None, "database_busy": True}
_health_snapshot_lock = threading.Lock()
_health_snapshot_thread = None


def _health_snapshot_loop():
    """Poll the DB off-request so SQLite contention never stalls dashboard status."""
    while True:
        worker_run = None
        persisted_at = None
        busy = False
        try:
            with database.read_only_scope():
                worker_run = database.latest_collector_run(timeout=1)
        except Exception:
            busy = True
        try:
            with database.read_only_scope():
                persisted_at = database.latest_observation_received_at("ssi", timeout=1)
        except Exception:
            busy = True
        with _health_snapshot_lock:
            if worker_run is not None:
                _health_snapshot["worker_run"] = worker_run
            if persisted_at is not None:
                _health_snapshot["last_persisted_at"] = persisted_at
            _health_snapshot["database_busy"] = busy
        time.sleep(5)

@lru_cache(maxsize=2)
def _security_directory(source):
    metadata = database.fetch_security_metadata("ssi") if source == "ssi" else {}
    return tuple((symbol, (metadata.get(symbol, {}).get("name_vi") or "").strip(),
                  metadata.get(symbol, {}).get("board") or "")
                 for symbol in (database.list_catalog_symbols("ssi") if source == "ssi" else database.list_symbols(source)))

def _demo_loop():
    while not _demo_stop.wait(4):
        if runtime.snapshot().get("mode") == "demo":
            database.add_demo_tick()

def _start_demo_loop():
    global _demo_thread
    _demo_stop.clear()
    if _demo_thread is None or not _demo_thread.is_alive():
        _demo_thread = threading.Thread(target=_demo_loop, daemon=True)
        _demo_thread.start()

def _stop_demo_loop():
    _demo_stop.set()


def _pid_exists(pid):
    try:
        pid = int(pid)
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return ctypes.get_last_error() == 5  # Access denied still means the PID exists.
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False
def _parse_status_age(stamp, now):
    if not stamp:return None
    try:
        value=datetime.fromisoformat(str(stamp).replace("Z","+00:00"))
        if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
        return max(0.0,(now-value.astimezone(timezone.utc)).total_seconds())
    except (TypeError,ValueError):
        return None


def _derive_pipeline_state(*,expected_stream,worker_active,connection_status,subscription_status,
                           received_at,persisted_at,received_count,valid_count,persistence_status,
                           queue_depth=0,queue_dropped=0,database_busy=False,now=None):
    now=now or datetime.now(timezone.utc)
    receive_age=_parse_status_age(received_at,now)
    persist_age=_parse_status_age(persisted_at,now)
    socket_ok=connection_status=="connected" and worker_active
    subscription_ok=subscription_status=="confirmed"
    message_ok=bool(received_at and int(received_count or 0)>0 and receive_age is not None)
    persistence_ok=bool(persisted_at and int(valid_count or 0)>0 and persistence_status=="saved")
    checks={"worker_alive":bool(worker_active),"socket_connected":socket_ok,
            "subscription_confirmed":subscription_ok,"message_received":message_ok,
            "valid_snapshot_persisted":persistence_ok,"queue_clear":int(queue_depth or 0)<=250 and int(queue_dropped or 0)==0,
            "database_available":not database_busy}
    if not expected_stream:state="outside_session"
    elif not worker_active:state="disconnected"
    elif connection_status in {"error","disconnected"}:state=connection_status
    elif connection_status in {"connecting","reconnecting"}:state="connecting"
    elif subscription_status in {"pending","subscribing"}:state="subscription_pending"
    elif subscription_status=="partial":state="subscription_partial"
    elif subscription_status!="confirmed":state="subscription_unverified"
    elif not message_ok:state="awaiting_quote"
    elif receive_age>config.STALE_AFTER_SECONDS:state="stale"
    elif persistence_status=="failed":state="persistence_error"
    elif not persistence_ok:state="awaiting_persistence"
    elif persist_age is not None and persist_age>config.STALE_AFTER_SECONDS:state="persistence_stale"
    elif int(queue_dropped or 0)>0:state="degraded"
    elif database_busy or int(queue_depth or 0)>250 or (receive_age is not None and receive_age>30) or (persist_age is not None and persist_age>30):state="delayed"
    else:state="live"
    return {"state":state,"verified":state=="live","checks":checks,
            "subscription_status":subscription_status,"persistence_status":persistence_status,
            "message_age_seconds":round(receive_age,1) if receive_age is not None else None,
            "persisted_age_seconds":round(persist_age,1) if persist_age is not None else None,
            "queue_depth":int(queue_depth or 0),"queue_dropped":int(queue_dropped or 0)}


app = FastAPI(
    title="Stock Liquidity Intelligence",
    description="Local dashboard for observing displayed stock order-book liquidity.",
    version="0.1.0",
)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.middleware("http")
async def sqlite_read_only_for_get(request: Request, call_next):
    if request.method != "GET":
        return await call_next(request)
    with database.read_only_scope():
        return await call_next(request)


def _market_meta(source=None):
    source=source or _source()
    now=time.monotonic()
    with _market_meta_lock:
        cached=_market_meta_cache.get(source)
        if cached:
            # Metadata includes several read-only queries over the multi-GB
            # history DB. Reuse it for a short window; expire at a market
            # phase boundary so ATO/continuous/lunch/close labels stay timely.
            ttl=15.0
            next_change=cached[1].get("next_change_at")
            if next_change:
                try:
                    seconds_to_change=(datetime.fromisoformat(next_change)-get_clock_now()).total_seconds()
                    if seconds_to_change <= ttl:
                        ttl=max(0.0,seconds_to_change)
                except (TypeError,ValueError):
                    pass
            if now-cached[0] < ttl:
                return cached[1]
        # Keep the lock during a cache miss so simultaneous dashboard requests
        # share one metadata query instead of each scanning the large SSI DB.
        result=_compute_market_meta(source)
        _market_meta_cache[source]=(now,result)
        return result


_market_meta_lock=threading.Lock()
_market_meta_cache={}


def _compute_market_meta(source=None):
    source=source or _source()
    now=get_clock_now()
    latest_bar=database.latest_bar_minute(source) if source in {"ssi","live"} else None
    latest_history=database.latest_historical_date("ssi")
    latest_complete=latest_bar or (f"{latest_history.replace('/', '-')}T15:00:00+07:00" if latest_history else None)
    try:
        last_completed=datetime.fromisoformat(latest_complete) if latest_complete else None
    except ValueError:
        last_completed=None
    calendar=database.session_calendar_overrides(now.date().isoformat(),(now.date()+timedelta(days=370)).isoformat())
    clock=get_market_state(now,"ALL",last_completed_at=last_completed,calendar=calendar)
    if clock["state"]=="CLOSED":
        if clock["closed_reason"]=="after_close" and clock["is_trading_day"]:
            clock["session_date"]=now.date().isoformat()
        elif latest_history:
            clock["session_date"]=latest_history.replace("/","-")
        else:
            clock["session_date"]=None
    session_sync=database.get_session_record(clock["session_date"],"ssi") if clock.get("session_date") else None
    if config.MARKET_CLOCK_OVERRIDE:
        clock["clock_simulated"]=True
    as_of=database.latest_data_as_of(source)
    if not as_of and source=="ssi":
        as_of=database.latest_data_as_of("live")
    return {**clock,"data_as_of":as_of,"latest_historical_date":latest_history.replace("/","-") if latest_history else None,
            "refresh_seconds":config.MARKET_REFRESH_SECONDS if clock["state"]=="IN_SESSION" else None,
            "clock_speed":config.MARKET_CLOCK_SPEED if config.MARKET_CLOCK_OVERRIDE else 1,
            "source":source,"symbols_count":database.count_historical_symbols("ssi"),
            "session_sync":session_sync or ({"status":"not_started"} if clock["state"]=="CLOSED" and clock.get("closed_reason")=="after_close" else None)}


def _incomplete_session_date(meta=None):
    meta=meta or _market_meta("ssi")
    day=meta.get("session_date")
    if not day:return None
    record=database.get_session_record(day,"ssi")
    if record and record.get("status")!="complete":return day.replace("-","/")
    if meta.get("state")=="CLOSED" and meta.get("closed_reason")=="after_close" and not record:
        return day.replace("-","/")
    return None


@app.get("/api/market-clock")
def market_clock():
    return {"meta":_market_meta()}


@app.get("/api/stream")
async def market_stream(request: Request):
    async def events():
        prior_state=None
        while not await request.is_disconnected():
            # Market metadata and overview queries hit SQLite synchronously.
            # Running them on the event loop starves page reloads and status
            # requests whenever the live stream is busy.
            with database.read_only_scope():
                meta=await asyncio.to_thread(_market_meta)
            exchange_key=tuple((name,item["state"],item.get("phase")) for name,item in sorted(meta.get("exchange_states",{}).items()))
            state_key=(meta["state"],meta.get("phase"),meta.get("session_date"),exchange_key)
            if state_key!=prior_state:
                yield "event: market_state\ndata: "+json.dumps(meta,ensure_ascii=False)+"\n\n"
                prior_state=state_key
            if meta["state"]=="IN_SESSION":
                with database.read_only_scope():
                    payload=await asyncio.to_thread(
                        overview, symbol="__all__", limit=240, window_minutes=60, phase=None
                    )
                yield "event: data_diff\ndata: "+json.dumps({"meta":meta,"data":payload},ensure_ascii=False,default=str)+"\n\n"
                until_change=max(0.25,(datetime.fromisoformat(meta["next_change_at"])-get_clock_now()).total_seconds()/meta.get("clock_speed",1)) if meta.get("next_change_at") else 60
                await asyncio.sleep(min(meta["refresh_seconds"] or 5,until_change))
            else:
                yield "event: heartbeat\ndata: "+json.dumps(meta,ensure_ascii=False)+"\n\n"
                until_change=max(0.25,(datetime.fromisoformat(meta["next_change_at"])-get_clock_now()).total_seconds()/meta.get("clock_speed",1)) if meta.get("next_change_at") else 30
                await asyncio.sleep(min(30,until_change))
    return StreamingResponse(events(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


@app.on_event("startup")
def startup():
    global _health_snapshot_thread
    # Warm a read-only, bounded status snapshot outside HTTP request threads.
    if _health_snapshot_thread is None or not _health_snapshot_thread.is_alive():
        _health_snapshot_thread = threading.Thread(target=_health_snapshot_loop, name="sli-health-snapshot", daemon=True)
        _health_snapshot_thread.start()
    # Do not run schema DDL/WAL setup against the multi-GB live database on
    # every web restart. It can contend with the independent SSI writer and
    # leave Uvicorn stuck before it binds the port. Bootstrap only a new DB;
    # schema migrations must be run explicitly before a version upgrade.
    if not config.DB_PATH.exists():
        database.initialize()
    else:
        # Apply small, additive local-profile migrations without running full
        # schema bootstrap or changing WAL mode on the live collector database.
        database.migrate_inv_d_settings()
        database.migrate_inv_c_positions()
    if not database.fetch_local_watchlists():
        database.create_local_watchlist("Danh mục của tôi")
    # Collector lifecycle is owned by run_collector.py, never by the web app.


@app.on_event("shutdown")
def shutdown():
    _stop_demo_loop()


@app.get("/")
def home(request: Request):
    # Streamlit is the only user-facing entrypoint. It embeds this dashboard
    # with a build query so the existing UI remains intact inside the shell.
    # Direct visits to the backend root go to the canonical Streamlit URL.
    if not request.query_params.get("_sli_build"):
        return RedirectResponse(
            url=os.getenv("SLI_STREAMLIT_URL", "http://127.0.0.1:8501/"),
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )
    return FileResponse(STATIC_DIR / "index.html")


def _source():
    return runtime.snapshot()["mode"]


def _numeric(values):
    return [float(value) for value in values if value is not None]


def _market_window(minutes, source="ssi"):
    # Ranking only needs the wall clock and the last completed SSI bar to pick
    # its range. Building the full market metadata here also scans historical
    # OHLC fetch timestamps across the entire catalog and can take many seconds
    # on the multi-year SQLite database, leaving the ranking widget blank while
    # unrelated charts continue to load.
    now=get_clock_now()
    end=now.replace(second=0,microsecond=0)
    minutes=max(1,min(int(minutes),390))
    latest=database.latest_bar_minute(source) if source in {"ssi","live"} else None
    if latest:
        latest_minute=datetime.fromisoformat(latest)
        age_seconds=(end-latest_minute).total_seconds()
        fresh_current=(latest_minute.date()==now.date() and 0<=age_seconds<=60)
        if not fresh_current:end=latest_minute.replace(second=0,microsecond=0)+timedelta(minutes=1)
    start=end-timedelta(minutes=minutes)
    if minutes>=390:
        session_day=latest_minute.date() if latest else now.date()
        start=datetime.combine(session_day,datetime.min.time(),tzinfo=config.TIMEZONE).replace(hour=9)
    return (start.isoformat(timespec="minutes"),
            end.isoformat(timespec="minutes"))


def _serialize_row(row):
    result = dict(row)
    for key, value in list(result.items()):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            result[key] = None
    return result


@app.get("/api/status")
def status():
    state = runtime.snapshot()
    with _health_snapshot_lock:
        worker_run = _health_snapshot.get("worker_run")
        latest_persisted_snapshot = _health_snapshot.get("last_persisted_at")
        database_busy = bool(_health_snapshot.get("database_busy"))
    if worker_run and worker_run.get("heartbeat_at"):
        try:
            heartbeat = datetime.fromisoformat(worker_run["heartbeat_at"])
            heartbeat_age = max(0.0, (datetime.now(config.TIMEZONE) - heartbeat).total_seconds())
        except ValueError:
            heartbeat_age = float("inf")
        run_summary = {key: worker_run.get(key) for key in (
            "messages_received","rows_valid","rows_invalid","duplicate_messages","reconnect_count",
            "queue_dropped","last_message_at","last_error","subscription_status","subscription_topics",
            "subscribed_boards","subscription_ack_count","subscription_error","quote_persistence_status",
            "trade_subscription_status","trade_subscription_topics","trade_persistence_status",
            "last_persisted_at","trade_messages_received","trade_messages_saved","trade_messages_dropped",
            "trade_messages_filtered","last_trade_received_at","last_trade_persisted_at","queue_depth",
            "trade_queue_depth"
        )}
        for key in ("subscription_topics", "subscribed_boards", "trade_subscription_topics"):
            raw = run_summary.get(key)
            if isinstance(raw, str):
                try:
                    decoded = json.loads(raw)
                    run_summary[key] = decoded if isinstance(decoded, list) else []
                except (TypeError, ValueError):
                    run_summary[key] = []
        state.update(run_summary)
        worker_alive = worker_run.get("ended_at") is None and _pid_exists(worker_run.get("process_id"))
        if worker_run.get("ended_at") is None and heartbeat_age <= 15:
            state.update(mode="ssi", connection_status=worker_run.get("connection_status") or "connecting",
                         worker_status="running", worker_process_alive=worker_alive,
                         worker_heartbeat_at=worker_run["heartbeat_at"],
                         worker_heartbeat_age_seconds=round(heartbeat_age, 1), collector_run_id=worker_run["id"])
        elif worker_alive:
            # SQLite can delay the heartbeat row while the collector is still
            # receiving and persisting messages. Use the message timestamp to
            # distinguish a delayed heartbeat from a genuinely stale feed.
            message_age = float("inf")
            try:
                message_stamp = datetime.fromisoformat(
                    str(worker_run.get("last_message_at") or "").replace("Z", "+00:00")
                )
                if message_stamp.tzinfo is None:
                    message_stamp = message_stamp.replace(tzinfo=timezone.utc)
                message_age = max(0.0, (datetime.now(timezone.utc) - message_stamp).total_seconds())
            except (TypeError, ValueError):
                pass
            feed_connected = message_age <= config.STALE_AFTER_SECONDS
            state.update(mode="ssi", connection_status=(worker_run.get("connection_status") or "connected")
                         if feed_connected else "stale", worker_status="heartbeat_delayed",
                         worker_process_alive=True, worker_heartbeat_at=worker_run["heartbeat_at"],
                         worker_heartbeat_age_seconds=round(heartbeat_age, 1), collector_run_id=worker_run["id"])
        else:
            worker_status = ("heartbeat_lost" if worker_run.get("ended_at") is None
                             else worker_run.get("status"))
            state.update(mode="ssi", connection_status="disconnected", worker_status=worker_status,
                         worker_process_alive=False, worker_heartbeat_at=worker_run["heartbeat_at"],
                         worker_heartbeat_age_seconds=round(heartbeat_age, 1))
    source = state["mode"]
    latest_received = state.get("last_message_at")
    run_persisted = worker_run.get("last_persisted_at") if worker_run else state.get("last_persisted_at")
    latest_persisted = run_persisted or latest_persisted_snapshot
    freshness_timestamp = latest_persisted or latest_received
    is_stale = False
    now = get_clock_now()
    # Health polling must not scan historical OHLC or wait behind chart queries.
    # Market phase is derived from the local market clock; history metadata is served elsewhere.
    market_meta=get_market_state(now, "ALL")
    # SSI may stream during opening/closing auctions as well as continuous
    # trading. Only lunch break and closed market suppress a live-feed check.
    expected_stream = market_meta["state"]=="IN_SESSION"
    quote_age = None
    if latest_received:
        try:
            quote_stamp = datetime.fromisoformat(latest_received.replace("Z", "+00:00"))
            if quote_stamp.tzinfo is None:
                quote_stamp = quote_stamp.replace(tzinfo=timezone.utc)
            quote_age = max(0.0, (datetime.now(timezone.utc) - quote_stamp).total_seconds())
        except ValueError:
            quote_age = None
    if source in {"live", "ssi"}:
        if freshness_timestamp and expected_stream:
            try:
                stamp = datetime.fromisoformat(freshness_timestamp.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                is_stale = (
                    datetime.now(timezone.utc) - stamp
                ).total_seconds() > config.STALE_AFTER_SECONDS
            except ValueError:
                is_stale = True
        elif not freshness_timestamp and expected_stream:
            is_stale = True
    persisted_lag = None
    if latest_persisted:
        try:
            persisted_stamp = datetime.fromisoformat(latest_persisted.replace("Z", "+00:00"))
            if persisted_stamp.tzinfo is None:
                persisted_stamp = persisted_stamp.replace(tzinfo=timezone.utc)
            persisted_lag = max(0.0, (datetime.now(timezone.utc) - persisted_stamp).total_seconds())
        except ValueError:
            persisted_lag = None
    processing_delayed = bool(expected_stream and (persisted_lag is None or persisted_lag > 15 or state.get("queue_depth", 0) > 250))
    worker_active = state.get("worker_status") in {"running", "heartbeat_delayed"} and state.get("worker_process_alive", True)
    run_quote_persisted=run_persisted
    quote_pipeline=_derive_pipeline_state(expected_stream=expected_stream,worker_active=worker_active,
        connection_status=state.get("connection_status"),subscription_status=state.get("subscription_status"),
        received_at=latest_received,persisted_at=run_quote_persisted,received_count=state.get("messages_received"),
        valid_count=state.get("rows_valid"),persistence_status=state.get("quote_persistence_status"),
        queue_depth=state.get("queue_depth",0),queue_dropped=state.get("queue_dropped",0),
        database_busy=database_busy)
    trade_pipeline=_derive_pipeline_state(expected_stream=expected_stream,worker_active=worker_active,
        connection_status=state.get("connection_status"),subscription_status=state.get("trade_subscription_status"),
        received_at=state.get("last_trade_received_at"),persisted_at=state.get("last_trade_persisted_at"),
        received_count=state.get("trade_messages_received"),valid_count=state.get("trade_messages_saved"),
        persistence_status=state.get("trade_persistence_status"),queue_depth=state.get("trade_queue_depth",0),
        queue_dropped=state.get("trade_messages_dropped",0),database_busy=database_busy)
    feed_state=quote_pipeline["state"]
    state.update({
        "source": source,
        "last_data_at": latest_persisted,
        "last_quote_received_at": latest_received,
        "last_persisted_at": latest_persisted,
        "persistence_lag_seconds": round(persisted_lag, 1) if persisted_lag is not None else None,
        "processing_delayed": processing_delayed,
        "quote_age_seconds": round(quote_age, 1) if quote_age is not None else None,
        "feed_state": feed_state,
        "realtime_verified": quote_pipeline["verified"],
        "quote_pipeline": quote_pipeline,
        "trade_feed_state": trade_pipeline["state"],
        "trade_realtime_verified": trade_pipeline["verified"],
        "trade_pipeline": trade_pipeline,
        "run_last_persisted_at": run_quote_persisted,
        "is_stale": is_stale,
        "stale_after_seconds": config.STALE_AFTER_SECONDS,
        "configured_watchlist": config.WATCHLIST,
        "historical_symbols_with_data": None,  # Loaded by history views, never by health polling.
        "status_db_busy": database_busy,
        "market_phase": ("BREAK" if market_meta["state"]=="LUNCH_BREAK" else market_meta.get("phase")),
        "market_session_expected": expected_stream,
        "market_state": market_meta["state"],
        "phase": market_meta.get("phase"),
        "session_date": market_meta.get("session_date"),
        # Keep the overview status banner on the same OHLC source of truth as
        # the history widgets. The live-feed clock alone does not know this date.
        "latest_historical_date": (str(database.latest_historical_date("ssi") or "").replace("/", "-") or None),
        "session_sync": (database.get_session_record(market_meta.get("session_date"), "ssi")
                         if market_meta.get("session_date") else None),
        "effective_now": market_meta.get("effective_now"),
        "data_as_of": market_meta.get("data_as_of"),
        "next_change_at": market_meta.get("next_change_at"),
        "next_open_at": market_meta.get("next_open_at"),
        "refresh_seconds": market_meta.get("refresh_seconds"),
        "clock_simulated": market_meta.get("clock_simulated",False),
    })
    if source == "demo":
        state["connection_status"] = "demo"
    elif is_stale and state["connection_status"] == "connected":
        state["connection_status"] = "stale"
    # Status is polled every few seconds. Persist alert changes only on transitions;
    # writing on every poll contends with the live collector's SQLite writer.
    # Alert persistence is intentionally handled outside this high-frequency route;
    # health polling must remain read-only while the collector writes live batches.
    return state


@app.get("/api/price-history")
def price_history(symbol: str | None = None, limit: int = Query(default=10000, ge=1, le=10000),
                 page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200),
                 search: str | None = None, from_date: str | None = None, to_date: str | None = None):
    selected = symbol.upper() if symbol and symbol.upper() not in ("ALL", "__ALL__") else None
    if selected:
        incomplete_date=_incomplete_session_date()
        rows=database.fetch_historical_prices(selected,limit=limit,source="ssi",exclude_date=incomplete_date,
                                              start_date=from_date,end_date=to_date)
        return {"source":"ssi","timeframe":"1d","symbols_count":1 if rows else 0,
                "symbols":[selected] if rows else [],"series_by_symbol":{selected:rows} if rows else {},
                "pending_session_date":incomplete_date,
                "note":"Giá OHLC ngày lịch sử từ SSI Market Data API; không phải quote realtime hoặc dữ liệu sổ lệnh."}
    incomplete_date=_incomplete_session_date()
    catalog=database.fetch_latest_historical_page(search=search,page=page,page_size=page_size,source="ssi",exclude_date=incomplete_date)
    return {"source":"ssi","timeframe":"1d","symbols_count":catalog["total"],
            "page":catalog["page"],"page_size":catalog["page_size"],"total":catalog["total"],
            "rows":catalog["rows"],"pending_session_date":incomplete_date,
            "note":"Danh mục cổ phiếu thường và OHLC phiên đã đồng bộ từ SSI; phiên đang chờ đồng bộ được giữ khỏi số liệu hiển thị."}


@lru_cache(maxsize=32)
def _cached_market_leaders(limit, cache_minute, as_of=None):
    return database.fetch_market_leaders(limit=limit,source="ssi",as_of=as_of)


@app.get("/api/market-leaders")
def market_leaders(limit: int = Query(default=25, ge=5, le=100), as_of: str | None = None):
    cache_minute=int(datetime.now(config.TIMEZONE).timestamp()//60)
    selected_date=(as_of.replace("-","/")[:10] if as_of else None) or _incomplete_session_date()
    if selected_date and not as_of:
        previous=database.fetch_market_breadth(limit=1000,source="ssi")
        available=[row["trading_date"] for row in previous if str(row["trading_date"]).replace("/","-")<selected_date.replace("/","-")]
        selected_date=available[-1] if available else None
    rows=_cached_market_leaders(limit,cache_minute,selected_date)
    return {"source":"ssi","rows":rows,"count":len(rows),
            "as_of_date":selected_date,
            "note":"OHLC ngày SSI; giá trị giao dịch lấy từ SSI hoặc giá đóng cửa × khối lượng nếu SSI không có trường giá trị. Mức đổi giá so với phiên trước."}


@lru_cache(maxsize=16)
def _cached_market_breadth(limit, start_date, end_date, board, cache_minute):
    return database.fetch_market_breadth(limit=limit,source="ssi",start_date=start_date,end_date=end_date,board=board)


@app.get("/api/market-breadth")
def market_breadth(limit: int = Query(default=10000, ge=2, le=10000), from_date: str | None = None, to_date: str | None = None,
                   board: str | None = Query(default=None, pattern="^(HOSE|HNX|UPCOM)$")):
    cache_minute=int(datetime.now(config.TIMEZONE).timestamp()//60)
    rows=_cached_market_breadth(limit,from_date,to_date,board,cache_minute)
    incomplete_date=_incomplete_session_date()
    pending_coverage=database.historical_session_coverage(incomplete_date,"ssi",board=board) if incomplete_date else {"present":0,"expected":0}
    # Keep the current SSI session visible when coverage is partial. Missing
    # symbols are not synthesized and are not counted as unchanged.
    latest_date=rows[-1]["trading_date"] if rows else None
    coverage=database.historical_session_coverage(latest_date,"ssi",board=board) if latest_date else {"present":0,"expected":0}
    synced=(coverage["expected"]>0 and coverage["present"]>=coverage["expected"])
    expected=coverage.get("expected",0)
    latest_row=rows[-1] if rows else {}
    comparable=sum(int(latest_row.get(key) or 0) for key in ("advancing","declining","unchanged"))
    no_comparison=max(0,int(expected)-comparable)
    return {"source":"ssi","board":board or "ALL","rows":rows,"count":len(rows),
            "latest_trading_date":latest_date,"pending_session_date":incomplete_date,
            "pending_session_coverage":pending_coverage,
            "pending_session_has_data":pending_coverage["present"]>0,
            "session_sync_complete":synced,
            "session_coverage":coverage,
            "breadth_latest":{"trading_date":latest_date,"advancing":int(latest_row.get("advancing") or 0),
                "declining":int(latest_row.get("declining") or 0),"unchanged":int(latest_row.get("unchanged") or 0),
                "no_comparison":no_comparison,"total_symbols":int(expected),"comparable_symbols":comparable},
            "latest_session_partial":bool(latest_date and incomplete_date and latest_date.replace("-","/")==incomplete_date and coverage["expected"] and coverage["present"]<coverage["expected"]),
            "note":"Độ rộng thị trường được tổng hợp từ OHLC ngày SSI của cổ phiếu thường: số mã tăng/giảm/đứng giá so với phiên trước. Phiên thiếu độ phủ vẫn tính từ bản ghi SSI có sẵn; mã không có bản ghi không được tính là đứng giá. Không phải dữ liệu sổ lệnh."}


@lru_cache(maxsize=32)
def _cached_market_breadth_intraday(session_date, board, cache_minute):
    return database.fetch_market_breadth_intraday(session_date,source="ssi",board=board)


@app.get("/api/market-breadth-intraday")
def market_breadth_intraday(session_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
                            board: str | None = Query(default=None, pattern="^(HOSE|HNX|UPCOM)$")):
    cache_minute=int(datetime.now(config.TIMEZONE).timestamp()//60)
    result=_cached_market_breadth_intraday(session_date,board,cache_minute)
    return result


@app.get("/api/market-turnover")
def market_turnover(limit: int = Query(default=10000, ge=20, le=10000),
                    metric: str = Query(default="value", pattern="^(value|volume)$"),
                    from_date: str | None = None, to_date: str | None = None,
                    board: str | None = Query(default=None, pattern="^(HOSE|HNX|UPCOM)$")):
    return get_market_turnover(limit=limit, metric=metric, market_meta=_market_meta("ssi"),
                               start_date=from_date,end_date=to_date,board=board)


@app.get("/api/market-history-range")
def market_history_range():
    """Small SSI-only date-bound query for the historical range controls."""
    return {"source":"ssi",**database.historical_date_bounds("ssi"),
            "historical_symbols_with_data":database.count_historical_symbols("ssi"),
            "recent_sessions":database.recent_historical_dates("ssi",30)}


@app.get("/api/symbols")
def symbols():
    source = _source()
    available = database.list_catalog_symbols("ssi") if source == "ssi" else database.list_symbols(source)
    if source == "live" and not available: available=config.WATCHLIST
    directory = _security_directory(source)
    securities = [{"symbol": symbol, "name_vi": name, "board": board}
                  for symbol, name, board in directory]
    return {"source":source,"symbols":available,"securities":securities,"count":len(available),
            "detail_default":database.fetch_detail_default_symbol(source)}


def _local_watchlist(watchlist_id):
    item=next((entry for entry in database.fetch_local_watchlists() if entry["id"]==watchlist_id),None)
    if not item:raise HTTPException(status_code=404,detail="Không tìm thấy danh sách theo dõi.")
    return item


def _clean_watchlist_name(value):
    name=" ".join(str(value or "").split())
    if not 1<=len(name)<=60:raise HTTPException(status_code=422,detail="Tên danh sách cần từ 1 đến 60 ký tự.")
    return name


def _validate_local_symbol(symbol):
    ticker=str(symbol or "").strip().upper()
    metadata=database.fetch_company_metadata("ssi",ticker)
    if not metadata or str(metadata.get("stock_type") or "").strip().lower()!="stock":
        raise HTTPException(status_code=422,detail="Chỉ thêm mã cổ phiếu có trong danh mục SSI.")
    return ticker


def _validate_position_payload(payload):
    if not isinstance(payload,dict):raise HTTPException(status_code=422,detail="Thông tin vị thế không hợp lệ.")
    symbol=_validate_local_symbol(payload.get("symbol"))
    shares=payload.get("shares")
    if isinstance(shares,bool) or not isinstance(shares,int) or shares<100 or shares>100_000_000 or shares%100:
        raise HTTPException(status_code=422,detail="Khối lượng phải là bội số lô 100, từ 100 đến 100.000.000 cổ phiếu.")
    try:average_cost=float(payload.get("average_cost"))
    except (TypeError,ValueError):raise HTTPException(status_code=422,detail="Giá vốn phải là số dương.")
    if not math.isfinite(average_cost) or average_cost<=0:raise HTTPException(status_code=422,detail="Giá vốn phải là số dương hợp lệ.")
    try:opened_on=datetime.fromisoformat(str(payload.get("opened_on"))).date().isoformat()
    except (TypeError,ValueError):raise HTTPException(status_code=422,detail="Ngày mua cần theo định dạng YYYY-MM-DD.")
    if opened_on>datetime.now(config.TIMEZONE).date().isoformat():
        raise HTTPException(status_code=422,detail="Ngày mua không thể ở tương lai.")
    note=str(payload.get("note") or "").strip()
    if len(note)>200:raise HTTPException(status_code=422,detail="Ghi chú vị thế tối đa 200 ký tự.")
    return {"symbol":symbol,"shares":shares,"average_cost":average_cost,"opened_on":opened_on,"note":note}


@app.get("/api/me/positions/live")
def local_positions_live(participation_pct:int=Query(default=10,ge=5,le=30)):
    return build_positions_live(participation_pct,get_clock_now(),_market_meta("ssi"))


@app.post("/api/me/positions")
def add_local_position(payload:dict=Body(...)):
    row=_validate_position_payload(payload)
    if len(database.fetch_local_positions())>=100:raise HTTPException(status_code=409,detail="Hồ sơ cục bộ tối đa 100 vị thế.")
    position_id=database.add_local_position(**row)
    return {"ok":True,"position_id":position_id,"positions":database.fetch_local_positions()}


@app.put("/api/me/positions/{position_id}")
def update_local_position(position_id:int,payload:dict=Body(...)):
    row=_validate_position_payload(payload)
    if not database.update_local_position(position_id,**row):raise HTTPException(status_code=404,detail="Không tìm thấy vị thế.")
    return {"ok":True,"positions":database.fetch_local_positions()}


@app.delete("/api/me/positions/{position_id}")
def delete_local_position(position_id:int):
    if not database.delete_local_position(position_id):raise HTTPException(status_code=404,detail="Không tìm thấy vị thế.")
    return {"ok":True,"positions":database.fetch_local_positions()}


@app.get("/api/me/watchlists")
def local_watchlists():
    watchlists=database.fetch_local_watchlists()
    return {"profile":"local","watchlists":watchlists,"max_watchlists":5,"max_symbols_per_list":50}


@app.post("/api/me/watchlists")
def create_local_watchlist(payload: dict=Body(...)):
    lists=database.fetch_local_watchlists()
    if len(lists)>=5:raise HTTPException(status_code=409,detail="Tối đa 5 danh sách theo dõi trên hồ sơ cục bộ.")
    name=_clean_watchlist_name(payload.get("name"))
    if any(item["name"].casefold()==name.casefold() for item in lists):raise HTTPException(status_code=409,detail="Tên danh sách đã tồn tại.")
    watchlist_id=database.create_local_watchlist(name)
    return _local_watchlist(watchlist_id)


@app.patch("/api/me/watchlists/{watchlist_id}")
def rename_local_watchlist(watchlist_id:int,payload:dict=Body(...)):
    _local_watchlist(watchlist_id)
    name=_clean_watchlist_name(payload.get("name"))
    if any(item["id"]!=watchlist_id and item["name"].casefold()==name.casefold() for item in database.fetch_local_watchlists()):
        raise HTTPException(status_code=409,detail="Tên danh sách đã tồn tại.")
    database.rename_local_watchlist(watchlist_id,name)
    return _local_watchlist(watchlist_id)


@app.delete("/api/me/watchlists/{watchlist_id}")
def delete_local_watchlist(watchlist_id:int):
    _local_watchlist(watchlist_id)
    database.delete_local_watchlist(watchlist_id)
    return {"ok":True,"watchlists":database.fetch_local_watchlists()}


@app.post("/api/me/watchlists/{watchlist_id}/items")
def add_local_watchlist_item(watchlist_id:int,payload:dict=Body(...)):
    watchlist=_local_watchlist(watchlist_id)
    symbol=_validate_local_symbol(payload.get("symbol"))
    note=str(payload.get("note") or "").strip()
    if len(note)>200:raise HTTPException(status_code=422,detail="Ghi chú tối đa 200 ký tự.")
    if any(item["symbol"]==symbol for item in watchlist["items"]):raise HTTPException(status_code=409,detail="Mã đã có trong danh sách.")
    if len(watchlist["items"])>=50:raise HTTPException(status_code=409,detail="Mỗi danh sách tối đa 50 mã.")
    try:database.add_local_watchlist_item(watchlist_id,symbol,note)
    except ValueError as exc:raise HTTPException(status_code=409,detail=str(exc)) from exc
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):raise HTTPException(status_code=409,detail="Mã đã có trong danh sách.") from exc
        raise
    return _local_watchlist(watchlist_id)


@app.delete("/api/me/watchlists/{watchlist_id}/items/{symbol}")
def remove_local_watchlist_item(watchlist_id:int,symbol:str):
    _local_watchlist(watchlist_id)
    if not database.remove_local_watchlist_item(watchlist_id,symbol.upper()):raise HTTPException(status_code=404,detail="Mã không có trong danh sách.")
    return _local_watchlist(watchlist_id)


@app.patch("/api/me/watchlists/{watchlist_id}/items/{symbol}")
def update_local_watchlist_note(watchlist_id:int,symbol:str,payload:dict=Body(...)):
    _local_watchlist(watchlist_id)
    note=str(payload.get("note") or "").strip()
    if len(note)>200:raise HTTPException(status_code=422,detail="Ghi chú tối đa 200 ký tự.")
    if not database.update_local_watchlist_item_note(watchlist_id,symbol.upper(),note):raise HTTPException(status_code=404,detail="Mã không có trong danh sách.")
    return _local_watchlist(watchlist_id)


@app.put("/api/me/watchlists/{watchlist_id}/order")
def reorder_local_watchlist(watchlist_id:int,payload:dict=Body(...)):
    _local_watchlist(watchlist_id)
    symbols=payload.get("symbols")
    if not isinstance(symbols,list) or len(symbols)>50 or any(not isinstance(item,str) for item in symbols):
        raise HTTPException(status_code=422,detail="Thứ tự mã không hợp lệ.")
    try:database.reorder_local_watchlist(watchlist_id,[item.upper() for item in symbols])
    except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc)) from exc
    return _local_watchlist(watchlist_id)


@app.get("/api/me/watchlists/{watchlist_id}/live")
def local_watchlist_live(watchlist_id:int,order_value_million:int|None=Query(default=None,ge=10,le=5000),
                         tolerance_bps:int|None=Query(default=None,ge=5,le=200),side:str|None=Query(default=None,pattern="^(buy|sell)$")):
    watchlist=_local_watchlist(watchlist_id)
    meta=_market_meta("ssi")
    return build_watchlist_live(watchlist,meta,get_clock_now(),order_value_million,tolerance_bps,side)


@app.post("/api/me/watchlists/{watchlist_id}/starter-set")
def add_local_watchlist_starter_set(watchlist_id:int):
    watchlist=_local_watchlist(watchlist_id)
    existing={str(item["symbol"]).upper() for item in watchlist.get("items",[])}
    candidates=database.fetch_deepest_saved_symbols("ssi",10)
    added=[]
    for row in candidates:
        symbol=str(row["symbol"]).upper()
        if symbol in existing or len(watchlist.get("items",[]))+len(added)>=50:continue
        try:database.add_local_watchlist_item(watchlist_id,symbol,"Mã mẫu từ 10 mã có độ sâu sổ lệnh SSI lưu gần nhất; không phải gợi ý đầu tư.")
        except ValueError:break
        added.append(symbol)
    if not added:raise HTTPException(status_code=409,detail="Chưa có bar sổ lệnh SSI lưu hợp lệ để tạo nhóm mã mẫu.")
    return {"watchlist":_local_watchlist(watchlist_id),"added":added,"basis":"deepest_latest_saved_ssi_book","disclaimer":"Nhóm mã chỉ để có dữ liệu bắt đầu, không phải gợi ý đầu tư."}


@app.get("/api/me/watchlists/{watchlist_id}/items/{symbol}/cost-matrix")
def local_watchlist_cost_matrix(watchlist_id:int,symbol:str,sizes:str=Query(default="50,100,200,500"),
                                side:str|None=Query(default=None,pattern="^(buy|sell)$"),
                                order_value_million:int|None=Query(default=None,ge=10,le=5000),
                                tolerance_bps:int|None=Query(default=None,ge=5,le=200)):
    watchlist=_local_watchlist(watchlist_id);ticker=symbol.strip().upper()
    item=next((entry for entry in watchlist.get("items",[]) if entry.get("symbol")==ticker),None)
    if item is None:raise HTTPException(status_code=404,detail="Mã không có trong danh sách đang chọn.")
    try:values=sorted({int(part.strip()) for part in sizes.split(",") if part.strip()})
    except (TypeError,ValueError):raise HTTPException(status_code=422,detail="sizes cần là danh sách triệu đồng, ví dụ 50,100,200,500.")
    if not values or len(values)>10 or any(value<10 or value>5000 for value in values):
        raise HTTPException(status_code=422,detail="Mỗi cỡ lệnh phải từ 10 đến 5.000 triệu đồng; tối đa 10 cỡ.")
    meta=_market_meta("ssi")
    result=build_watchlist_live({**watchlist,"items":[item]},meta,get_clock_now(),order_value_million,tolerance_bps,side)
    row=result["items"][0]
    return {"symbol":ticker,"source":"ssi","meta":result["meta"],"snapshot_ts":row.get("bookwalk_snapshot_at"),"quote_age_s":row.get("quote_age_s"),
            "quote_stale":row.get("quote_stale"),"side":result["settings"]["order_side"],"tolerance_bps":result["settings"]["tolerance_bps"],
            "matrix":{str(value):row.get("bookwalk",{}).get(str(value*1_000_000)) for value in values+[result["settings"]["order_value_million"]]},
            "order_cost":row.get("order_cost"),"max_order":row.get("max_order"),"disclaimer":"Mô phỏng tĩnh từ snapshot SSI; không gửi lệnh."}


@app.get("/api/me/settings")
def local_user_settings():
    settings=database.get_local_settings()
    settings.update({"favorable_spread_pctl":config.FAVORABLE_SPREAD_PERCENTILE,
                     "stress_spread_pctl":config.STRESS_SPREAD_PERCENTILE,
                     "favorable_depth_pctl":config.FAVORABLE_DEPTH_PERCENTILE,
                     "stress_depth_pctl":config.STRESS_DEPTH_PERCENTILE})
    return {"profile":"local","settings":settings}


def _validate_local_settings(payload,base=None):
    threshold_keys={"favorable_spread_pctl","stress_spread_pctl","favorable_depth_pctl","stress_depth_pctl"}
    editable={"standard_order_shares","order_value_million","tolerance_bps","order_side","quiet_start","quiet_end","notification_channel"}
    allowed=editable|threshold_keys  # accept older clients/profile files; ignore their threshold values
    unknown=set(payload)-allowed
    if unknown:raise HTTPException(status_code=422,detail="Có trường cài đặt không được hỗ trợ.")
    merged={**(base or database.get_local_settings()),**payload}
    shares=merged.get("standard_order_shares")
    if isinstance(shares,bool) or not isinstance(shares,int) or not 100<=shares<=10000000 or shares%100:
        raise HTTPException(status_code=422,detail="Quy mô lệnh chuẩn phải là bội số 100, từ 100 đến 10.000.000 cổ phiếu.")
    order_value=merged.get("order_value_million",100);tolerance=merged.get("tolerance_bps",30);order_side=merged.get("order_side","buy")
    if isinstance(order_value,bool) or not isinstance(order_value,int) or not 10<=order_value<=5000:
        raise HTTPException(status_code=422,detail="Giá trị lệnh phải từ 10 đến 5.000 triệu đồng.")
    if isinstance(tolerance,bool) or not isinstance(tolerance,int) or not 5<=tolerance<=200:
        raise HTTPException(status_code=422,detail="Ngưỡng trượt giá phải từ 5 đến 200 bps.")
    if order_side not in {"buy","sell"}:raise HTTPException(status_code=422,detail="Chiều mô phỏng chỉ nhận buy hoặc sell.")
    merged.update({"favorable_spread_pctl":config.FAVORABLE_SPREAD_PERCENTILE,
                   "stress_spread_pctl":config.STRESS_SPREAD_PERCENTILE,
                   "favorable_depth_pctl":config.FAVORABLE_DEPTH_PERCENTILE,
                   "stress_depth_pctl":config.STRESS_DEPTH_PERCENTILE})
    for key in ("quiet_start","quiet_end"):
        value=merged.get(key)
        if value is not None and value!="":
            try:datetime.strptime(value,"%H:%M")
            except (TypeError,ValueError):raise HTTPException(status_code=422,detail="Giờ yên lặng cần theo định dạng HH:MM.")
        else:merged[key]=None
    if merged.get("notification_channel") not in {"in_app","browser"}:
        raise HTTPException(status_code=422,detail="Kênh thông báo chỉ hỗ trợ trong ứng dụng hoặc trình duyệt.")
    return {key:merged[key] for key in editable}


@app.put("/api/me/settings")
def update_local_user_settings(payload:dict=Body(...)):
    validated=_validate_local_settings(payload)
    return {"profile":"local","settings":database.update_local_settings(validated)}


def _validate_profile_document(payload):
    if not isinstance(payload,dict) or payload.get("format")!="sli-local-profile-v1":
        raise HTTPException(status_code=422,detail="Tệp không đúng định dạng hồ sơ SLI.")
    lists=payload.get("watchlists")
    if not isinstance(lists,list) or len(lists)>5:raise HTTPException(status_code=422,detail="Tệp chứa tối đa 5 danh sách.")
    validated=[];seen_names=set()
    for entry in lists:
        if not isinstance(entry,dict):raise HTTPException(status_code=422,detail="Danh sách trong tệp không hợp lệ.")
        name=_clean_watchlist_name(entry.get("name"))
        if name.casefold() in seen_names:raise HTTPException(status_code=422,detail="Tên danh sách bị trùng trong tệp.")
        seen_names.add(name.casefold());items=entry.get("items")
        if not isinstance(items,list) or len(items)>50:raise HTTPException(status_code=422,detail="Mỗi danh sách tối đa 50 mã.")
        clean=[];seen=set()
        for item in items:
            if not isinstance(item,dict):raise HTTPException(status_code=422,detail="Mã trong tệp không hợp lệ.")
            symbol=_validate_local_symbol(item.get("symbol"));note=str(item.get("note") or "").strip()
            if symbol in seen or len(note)>200:raise HTTPException(status_code=422,detail="Mã bị trùng hoặc ghi chú vượt 200 ký tự.")
            seen.add(symbol);clean.append({"symbol":symbol,"note":note})
        validated.append({"name":name,"items":clean})
    settings=payload.get("settings") or {}
    if not isinstance(settings,dict):raise HTTPException(status_code=422,detail="Cài đặt trong tệp không hợp lệ.")
    return settings,validated


@app.get("/api/me/export")
def export_local_profile():
    settings=database.get_local_settings()
    allowed={"standard_order_shares","order_value_million","tolerance_bps","order_side","quiet_start","quiet_end","notification_channel"}
    fixed={"favorable_spread_pctl":config.FAVORABLE_SPREAD_PERCENTILE,
           "stress_spread_pctl":config.STRESS_SPREAD_PERCENTILE,
           "favorable_depth_pctl":config.FAVORABLE_DEPTH_PERCENTILE,
           "stress_depth_pctl":config.STRESS_DEPTH_PERCENTILE}
    return {"format":"sli-local-profile-v1","settings":{**{key:settings[key] for key in allowed},**fixed},
            "watchlists":[{"name":row["name"],"items":[{"symbol":item["symbol"],"note":item["note"]} for item in row["items"]]}
                          for row in database.fetch_local_watchlists()],
            "positions":[{key:row[key] for key in ("symbol","shares","average_cost","opened_on","note")}
                          for row in database.fetch_local_positions()]}


@app.post("/api/me/import")
def import_local_profile(payload:dict=Body(...)):
    settings,lists=_validate_profile_document(payload)
    validated_settings=_validate_local_settings(settings)
    positions=None
    if "positions" in payload:
        raw_positions=payload.get("positions")
        if not isinstance(raw_positions,list) or len(raw_positions)>100:
            raise HTTPException(status_code=422,detail="Hồ sơ tối đa 100 vị thế.")
        positions=[_validate_position_payload(row) for row in raw_positions]
    backup_file=database.backup_local_profile_json()
    database.replace_local_profile(validated_settings,lists,positions)
    return {"ok":True,"backup_file":backup_file,"watchlists":database.fetch_local_watchlists(),
            "positions":database.fetch_local_positions(),"settings":database.get_local_settings()}

@app.get("/api/overview")
def overview(symbol: str | None = None, limit: int = Query(default=240, ge=1, le=2000),
             window_minutes: int = Query(default=60, ge=5, le=390),
             summary_only: bool = False,
             start_at: str | None = None, end_at: str | None = None,
             phase: str | None = Query(default=None, pattern="^(ATO|CONTINUOUS_AM|BREAK|CONTINUOUS_PM|ATC|CLOSED)$")):
    source = _source()
    selected = symbol.upper() if symbol and symbol.upper() not in ("__ALL__", "ALL") else None
    window_start,window_end=_market_window(window_minutes,source)
    start_at=start_at or window_start
    end_at=end_at or window_end
    raw_groups = {}
    if selected:
        recent = database.fetch_observations(source, symbol=selected, limit=1,
            start_at=start_at, end_at=end_at, phase=phase)
        raw_groups = {selected: list(reversed(recent))} if recent else {}
    else:
        # One latest quote per ticker within a shared clock window keeps KPI scope comparable.
        effective_limit = 1
        raw_groups = database.fetch_recent_observations_by_symbol(source, effective_limit, start_at, end_at, phase)
    groups = {}
    flattened = [row for series in raw_groups.values() for row in series]
    if not summary_only:
        display_fields = {"symbol", "received_at_utc", "received_at_vn", "source_ts_vn", "market",
            "session_phase", "validation_status", "validation_reason", "last_price", "spread_bps",
            "spread_ticks", "mid_price", "bid_depth_value", "ask_depth_value", "total_depth_value",
            "queue_imbalance", "bid_price_1", "bid_volume_1", "ask_price_1", "ask_volume_1"}
        for ticker, rows in raw_groups.items():
            series=[]
            for row in rows:
                clean=_serialize_row(row)
                series.append({key:clean.get(key) for key in display_fields})
            groups[ticker]=series
    valid = [row for row in flattened if row["validation_status"] == "valid"]
    spreads = _numeric([row["spread_bps"] for row in valid])
    depths = _numeric([row["total_depth_value"] for row in valid])
    imbalances = _numeric([row["queue_imbalance"] for row in valid])
    count = len(flattened)
    return {"source": source, "symbol": selected, "symbols_count": len(groups),
        "observation_count": count, "valid_observation_count": len(valid),
        "window_limit_per_symbol": 1,
        "window_minutes":window_minutes,"window_start":start_at,"window_end_exclusive":window_end,
        "window_basis":"shared_clock_window; chart bars exclude the unfinished minute",
        "valid_snapshot_rate": len(valid) / count if count else None,
        "median_spread_bps": median(spreads) if spreads else None,
        "median_spread_ticks": median(_numeric([row.get("spread_ticks") for row in valid])) if valid else None,
        "median_total_depth_value": median(depths) if depths else None,
        "median_queue_imbalance": median(imbalances) if imbalances else None,
        "summary_only": summary_only,
        "series": groups.get(selected, []) if selected else [],
        "series_by_symbol": {} if summary_only else groups}


@app.get("/api/trades")
def trades(symbol: str | None = None, limit: int = Query(default=50, ge=1, le=500)):
    selected=symbol.upper() if symbol else None
    rows=database.fetch_recent_trades(_source(),selected,limit)
    rows.reverse()
    return {"source":_source(),"symbol":selected,"trades":rows,"count":len(rows),
            "note":"Public TradeMessage từ SSI FastConnect; quantity là khối lượng lần khớp, total_volume là tổng khối lượng SSI gửi trong message. Không đồng nhất với khối lượng chờ bid/ask."}


@app.get("/api/market-liquidity-intraday")
def market_liquidity_intraday(board: str = Query(default="HOSE", pattern="^(HOSE|HNX|UPCOM)$")):
    """Fast current-session turnover series; historical comparison loads separately."""
    source="ssi"
    meta=_market_meta(source)
    latest_trade=database.latest_trade_timestamp(source)
    if meta.get("state") in {"IN_SESSION","LUNCH_BREAK"}:
        session_date=str(meta.get("session_date") or get_clock_now().date().isoformat())[:10]
    else:
        session_date=str(latest_trade or "")[:10] or str(meta.get("latest_historical_date") or "")[:10]
    raw=database.fetch_trade_value_by_minute(source,board,[session_date] if session_date else [])
    current={}
    for row in raw:
        minute=str(row.get("minute") or "")[:5]
        if len(minute)!=5 or minute<"09:15" or minute>"14:45" or "11:30"<=minute<"13:00":continue
        point=current.setdefault(minute,{"value":0.0,"trade_count":0})
        point["value"]+=float(row.get("traded_value") or 0)
        point["trade_count"]+=int(row.get("trade_count") or 0)
    last_minute=max(current) if current else None
    state=meta.get("state")
    if not current:
        return {"source":source,"board":board,"session_date":session_date or None,"market_state":state,
                "status":"awaiting_trades" if state in {"IN_SESSION","LUNCH_BREAK"} else "no_trade_data",
                "latest_trade_at":latest_trade,"sessions_used":0,"unit":"tỷ đồng",
                "points":[],"meta":meta,
                "note":f"Chưa có TradeMessage SSI đã lưu cho sàn {board} trong phiên {session_date or 'gần nhất'}. Biểu đồ không ước tính GTGD từ OHLC hoặc sổ lệnh."}
    if last_minute<"09:15":
        return {"source":source,"board":board,"session_date":session_date,"market_state":state,
                "status":"awaiting_trades","latest_trade_at":latest_trade,"sessions_used":0,
                "unit":"tỷ đồng","points":[],"meta":meta,
                "note":"Chưa có giao dịch liên tục sau phiên ATO trong dữ liệu SSI đã lưu."}
    start=9*60+15;end=min(14*60+45,int(last_minute[:2])*60+int(last_minute[3:]))
    grid=[]
    for minute_of_day in range(start,end+1):
        if 11*60+30<=minute_of_day<13*60:continue
        grid.append(f"{minute_of_day//60:02d}:{minute_of_day%60:02d}")
    values=[];cumulative_value=0.0;cursor=0;keys=sorted(current)
    for minute in grid:
        while cursor<len(keys) and keys[cursor]<=minute:
            cumulative_value+=current[keys[cursor]]["value"];cursor+=1
        values.append(cumulative_value)
    max_value=max(values,default=0)
    divisor,unit=(1e12,"nghìn tỷ đồng") if max_value>=1e12 else (1e9,"tỷ đồng") if max_value>=1e9 else (1e6,"triệu đồng")
    points=[]
    for index,minute in enumerate(grid):
        current_minute=current.get(minute,{})
        points.append({"minute":f"{session_date}T{minute}:00+07:00",
            "current_value":values[index]/divisor,
            "average5_value":None,
            "trade_count":current_minute.get("trade_count",0)})
    return {"source":source,"board":board,"session_date":session_date,"market_state":state,
        "status":"ready","latest_trade_at":latest_trade,"sessions_used":0,
        "comparison_sessions":[],"baseline_pending":True,"scale":divisor,"unit":unit,"points":points,"meta":meta,
        "note":"Đường GTGD hiện tại được tính từ TradeMessage SSI (giá khớp × khối lượng khớp), lũy kế theo phút. Đường so sánh 5 phiên được tải riêng để biểu đồ hiện dữ liệu hiện tại trước; không suy ra từ OHLC hoặc độ sâu sổ lệnh."}


@app.get("/api/market-liquidity-baseline")
def market_liquidity_baseline(
    board: str = Query(default="HOSE", pattern="^(HOSE|HNX|UPCOM)$"),
    session_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    scale: float = Query(default=1e9, gt=0, le=1e12),
):
    """Historical five-session comparison, requested after the live line renders."""
    cache_key=(board,session_date,float(scale))
    now=time.time()
    with _market_liquidity_baseline_lock:
        cached=_market_liquidity_baseline_cache.get(cache_key)
        if cached and now-cached[0]<21600:
            return cached[1]
    source="ssi"
    previous_sessions=list(reversed(database.recent_trade_dates(source,session_date,5)))
    raw=database.fetch_trade_value_by_minute(source,board,previous_sessions)
    by_day={}
    for row in raw:
        minute=str(row.get("minute") or "")[:5]
        if len(minute)!=5 or minute<"09:15" or minute>"14:45" or "11:30"<=minute<"13:00":continue
        day=row["session_date"]
        per_minute=by_day.setdefault(day,{})
        per_minute[minute]=per_minute.get(minute,0.0)+float(row.get("traded_value") or 0)
    sessions=[day for day in previous_sessions if by_day.get(day)][-5:]
    start=9*60+15;end=14*60+45;grid=[]
    for minute_of_day in range(start,end+1):
        if 11*60+30<=minute_of_day<13*60:continue
        grid.append(f"{minute_of_day//60:02d}:{minute_of_day%60:02d}")
    def cumulative(day):
        observations=by_day.get(day,{})
        value=0.0;cursor=0;keys=sorted(observations);series=[]
        for minute in grid:
            while cursor<len(keys) and keys[cursor]<=minute:
                value+=observations[keys[cursor]];cursor+=1
            series.append(value/scale)
        return series
    series_by_day=[cumulative(day) for day in sessions]
    points=[]
    for index,minute in enumerate(grid):
        average=(sum(series[index] for series in series_by_day)/len(series_by_day)) if series_by_day else None
        points.append({"minute":f"{session_date}T{minute}:00+07:00","average5_value":average})
    result={"source":source,"board":board,"session_date":session_date,"status":"ready",
        "sessions_used":len(sessions),"comparison_sessions":sessions,"scale":scale,"points":points,
        "note":"Trung bình GTGD lũy kế cùng phút từ các phiên SSI gần nhất có TradeMessage hợp lệ trên sàn đã chọn."}
    with _market_liquidity_baseline_lock:
        _market_liquidity_baseline_cache[cache_key]=(time.time(),result)
        if len(_market_liquidity_baseline_cache)>18:
            oldest=min(_market_liquidity_baseline_cache,key=lambda key:_market_liquidity_baseline_cache[key][0])
            _market_liquidity_baseline_cache.pop(oldest,None)
    return result


@app.get("/api/detail/{symbol}")
def detail_page(symbol: str, window_sessions: int = Query(default=5, ge=1, le=20),
                from_date: str | None = None, to_date: str | None = None):
    """Single-clock payload for every widget on the stock detail page."""
    result = build_detail(symbol, source=_source(), window_sessions=window_sessions,
                          from_date=from_date,to_date=to_date)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.get("/api/symbol/{symbol}")
def symbol_detail(symbol: str, limit: int = Query(default=300, ge=1, le=2000),
                  start_at: str | None = None, end_at: str | None = None,
                  phase: str | None = Query(default=None, pattern="^(ATO|CONTINUOUS_AM|BREAK|CONTINUOUS_PM|ATC|CLOSED)$")):
    rows = list(reversed(database.fetch_observations(_source(), symbol=symbol, limit=limit,
                                                      start_at=start_at, end_at=end_at, phase=phase)))
    if not rows:
        return {"source": _source(), "symbol": symbol.upper(), "latest": None, "series": []}
    valid = [row for row in rows if row["validation_status"] == "valid"]
    return {
        "source": _source(),
        "symbol": symbol.upper(),
        "latest": _serialize_row(rows[-1]),
        "snapshot_count": len(rows),
        "valid_snapshot_rate": len(valid) / len(rows),
        "series": [_serialize_row(row) for row in rows],
    }


@app.get("/api/company/{symbol}")
def company(symbol: str):
    profiles = {
        "FPT": {"name": "Công ty Cổ phần FPT", "sector": "Công nghệ thông tin", "description": "Tập đoàn công nghệ cung cấp dịch vụ công nghệ, viễn thông và giáo dục."},
        "HPG": {"name": "Công ty Cổ phần Tập đoàn Hòa Phát", "sector": "Thép và công nghiệp", "description": "Nhà sản xuất thép và sản phẩm công nghiệp; mã HPG được dùng làm ví dụ trong bộ dữ liệu DEMO."},
        "VPB": {"name": "Ngân hàng TMCP Việt Nam Thịnh Vượng", "sector": "Ngân hàng", "description": "Ngân hàng thương mại cổ phần; mã VPB được dùng làm ví dụ trong bộ dữ liệu DEMO."},
    }
    ticker = symbol.upper()
    metadata = database.fetch_company_metadata("ssi", ticker)
    catalog_name = (metadata.get("name_vi") or "").strip()
    if catalog_name:
        board = metadata.get("board") or "Chưa xác định"
        profile = {"name": catalog_name, "sector": board, "board": board,
                   "description": f"Tên doanh nghiệp lấy từ danh mục mã SSI; mã {ticker} giao dịch trên sàn {board}."}
    else:
        profile = profiles.get(ticker, {"name": ticker, "sector": "Chưa xác định", "description": "Mã này chưa có tên doanh nghiệp trong danh mục SSI."})
    return {"symbol": ticker, **profile}


@app.get("/api/rankings")
def rankings(limit: int = Query(default=500, ge=1, le=2000), window_minutes: int = Query(default=60,ge=15,le=390),start_at: str | None = None,
              end_at: str | None = None, phase: str | None = Query(default=None, pattern="^(OPEN|MID|CLOSE)$"),
              board: str | None = Query(default=None, pattern="^(HOSE|HNX|UPCOM)$")):
    source = _source()
    window_start,window_end=_market_window(window_minutes,source)
    start_at=start_at or window_start
    end_at=end_at or window_end
    minimum_samples = config.RANKING_MIN_BARS if source in {"live", "ssi"} else config.RANKING_MIN_SNAPSHOTS
    comparison_date=None
    comparison_start=None
    comparison_end=None
    if source in {"live", "ssi"}:
        metadata=database.fetch_security_metadata("ssi")
        stock_catalog = {ticker: profile for ticker, profile in metadata.items()
                         if is_rankable_stock(ticker, profile.get("stock_type"))
                         and (not board or profile.get("board") == board)}
        catalog_count = len(stock_catalog)

        def summarize_bar_window(start, end):
            grouped = database.fetch_bars_window_by_symbol(source,start,end,window_minutes)
            results=[]
            for ticker, points in grouped.items():
                if ticker not in stock_catalog:
                    continue
                selected=[row for row in points if not phase or row.get("baseline_bucket") == phase]
                row=calculate_symbol_row(ticker,selected,metadata)
                if row:
                    results.append(row)
            return results,len(grouped)

        results,bar_symbols=summarize_bar_window(start_at,end_at)
        ranked=rank_rows(results,minimum_samples)
        eligible=ranked["eligible"]
        current_start=datetime.fromisoformat(start_at)
        current_end=datetime.fromisoformat(end_at)
        session_date=current_end.date().isoformat()
        comparison_date=database.previous_bar_session(source,session_date)
        comparison_rows=[]
        if comparison_date:
            if window_minutes>=390:
                previous_start=datetime.fromisoformat(comparison_date+"T00:00:00+07:00")
                previous_end=previous_start+timedelta(days=1)
            else:
                start_clock=current_start.strftime("%H:%M")
                end_clock=current_end.strftime("%H:%M")
                previous_start=datetime.fromisoformat(comparison_date+"T"+start_clock+":00+07:00")
                previous_end=datetime.fromisoformat(comparison_date+"T"+end_clock+":00+07:00")
            comparison_start=previous_start.isoformat(timespec="minutes")
            comparison_end=previous_end.isoformat(timespec="minutes")
            previous_results,_=summarize_bar_window(comparison_start,comparison_end)
            comparison_rows=rank_rows(previous_results,minimum_samples)["lowest_spread"]
        ranked["lowest_spread"]=attach_rank_movement(ranked["lowest_spread"],comparison_rows)
        comparison_eligible_symbols=len(comparison_rows)
        quote_valid_symbols=len(results)
    else:
        grouped=database.fetch_recent_observations_by_symbol(source,limit,start_at,end_at)
        results=[]
        for ticker, rows in grouped.items():
            valid=[row for row in rows if row["validation_status"]=="valid"]
            spreads=_numeric([row["spread_bps"] for row in valid]); depths=_numeric([row["total_depth_value"] for row in valid])
            if not valid: continue
            latest_spread,latest_depth=valid[-1]["spread_bps"],valid[-1]["total_depth_value"]
            results.append({"symbol":ticker,"snapshot_count":len(rows),"sample_count":len(valid),"bar_count":0,
                "sample_kind":f"{source}_observations","median_spread_bps":median(spreads) if spreads else None,
                "median_total_depth_value":median(depths) if depths else None,"latest_spread_bps":latest_spread,
                "latest_total_depth_value":latest_depth,"spread_expansion_bps":latest_spread-median(spreads) if spreads and latest_spread is not None else None,
                "depth_change_pct":((latest_depth/median(depths)-1)*100) if depths and median(depths) and latest_depth is not None else None})
        catalog_count=len(database.list_symbols(source))
        bar_symbols=len(grouped)
        ranked=rank_rows(results,minimum_samples)
        eligible=ranked["eligible"]
        comparison_eligible_symbols=0
        quote_valid_symbols=len(results)
    return {"source":source,"sample_window_minutes":window_minutes,"window_start":start_at,"window_end_exclusive":end_at,
        "minimum_eligible_samples":minimum_samples,"minimum_snapshots":0,
        "candidate_symbols":len(results),"catalog_symbols":catalog_count,"symbols_with_window_bars":bar_symbols,
        "symbols_with_valid_quotes":quote_valid_symbols,"eligible_symbols":len(eligible),
        "displayed_symbols":min(len(eligible),limit),"one_tick_count":ranked["one_tick_count"],
        "one_tick_share":ranked["one_tick_share"],"filter":{"start_at":start_at,"end_at":end_at,"phase":phase,"board":board},
        "rank_change_basis":"Cùng khung giờ của phiên giao dịch trước, xếp hạng riêng từ bar SSI đã lưu" if comparison_date else "Chưa có bar phiên trước để so sánh",
        "comparison_session_date":comparison_date,"comparison_eligible_symbols":comparison_eligible_symbols,
        "comparison_window_start":comparison_start,"comparison_window_end_exclusive":comparison_end,
        "lowest_spread":ranked["lowest_spread"][:limit],"highest_depth":ranked["highest_depth"][:limit],
        "depth_falling":ranked["depth_falling"][:limit],"spread_widening":ranked["spread_widening"][:limit],
        "symbols":eligible[:limit],"note":"Xếp hạng dùng bar 1 phút hợp lệ trong cửa sổ chung; chỉ lấy bar của phiên khớp liên tục (loại ATO/ATC và giờ nghỉ trưa). Mức đổi hạng đối chiếu cùng khung giờ phiên giao dịch trước; thiếu bar thì ghi chưa đủ dữ liệu."}


@app.get("/api/bars")
def bars(symbol: str | None = None, limit: int = Query(default=1000, ge=1, le=10000),
         source: str | None = None, start_at: str | None = None, end_at: str | None = None,
         phase: str | None = Query(default=None, pattern="^(OPEN|MID|CLOSE)$")):
    return {"source": source or _source(), "bars": database.fetch_bars(
        source or _source(), symbol, limit, start_at, end_at, phase)}


@app.get("/api/intraday-profile")
def intraday_profile(symbol: str | None = None, window_minutes: int = Query(default=60,ge=5,le=390),start_at: str | None = None,
                     end_at: str | None = None, from_date: str | None = None,
                     to_date: str | None = None, source: str | None = None):
    selected_source = source or _source()
    window_start,window_end=_market_window(window_minutes,selected_source)
    historical_range=bool(from_date or to_date)
    start_at=start_at or (f"{from_date}T00:00:00+07:00" if from_date else window_start)
    end_exclusive=end_at or (f"{(datetime.fromisoformat(to_date)+timedelta(days=1)).date().isoformat()}T00:00:00+07:00" if to_date else window_end)
    if historical_range and to_date:
        meta=_market_meta(selected_source)
        if (str(to_date)[:10]==str(meta.get("session_date") or "")[:10]
                and meta.get("state") in {"IN_SESSION","LUNCH_BREAK"} and meta.get("effective_now")):
            completed_cutoff=datetime.fromisoformat(meta["effective_now"]).replace(second=0,microsecond=0).isoformat(timespec="minutes")
            end_exclusive=min(end_exclusive,completed_cutoff)
    if selected_source in {"live", "ssi"}:
        # Aggregate in a streaming database pass instead of building hundreds
        # of thousands of ticker-minute objects in the API process.
        profile = database.aggregate_historical_bar_profile(
            selected_source, start_at, end_exclusive, symbol
        )
        session_date = profile[-1]["minute"][:10] if profile else None
        latest_bar = profile[-1]["minute"] if profile else None
        sessions_count = len({row["minute"][:10] for row in profile})
        if not historical_range:
            profile = [{**row, "minute": row["minute"][11:16]} for row in profile]
        return {"source": selected_source, "symbol": symbol.upper() if symbol else None,
                "profile": profile, "basis": "completed_bars_1m", "window_minutes": window_minutes,
                "window_start": start_at, "completed_through_exclusive": end_exclusive,
                "session_date": session_date, "as_of": latest_bar, "data_as_of": latest_bar,
                "sessions_count": sessions_count,
                "note": "Chỉ dùng bar một phút đã hoàn tất; mỗi phút lấy trung vị bar của từng mã, sau đó lấy trung vị giữa các mã. Số mã tham gia được ghi tại từng điểm."}
    bars_data = database.fetch_bars(selected_source, symbol, 10000, start_at, end_exclusive)
    grouped = {}
    for row in bars_data:
        minute = str(row["minute_vn"])[:16] if historical_range else datetime.fromisoformat(row["minute_vn"]).strftime("%H:%M")
        slot = grouped.setdefault(minute, {"spread": [], "ticks": [], "depth": [], "imbalance":[],"snapshots": 0, "symbols": set()})
        for key, target in (("spread_bps", "spread"), ("spread_ticks", "ticks"), ("total_depth_value", "depth"),("queue_imbalance","imbalance")):
            if row.get(key) is not None:
                slot[target].append(row[key])
        slot["snapshots"] += row["n_snapshots"]
        slot["symbols"].add(row["symbol"])
    session_date = max((str(row.get("minute_vn") or "")[:10] for row in bars_data), default=None) or None
    latest_bar = max((str(row.get("minute_vn") or "") for row in bars_data), default=None) or None
    profile = [{"minute": (minute+":00+07:00" if historical_range else (f"{session_date}T{minute}:00+07:00" if session_date else minute)), "median_spread_bps": median(v["spread"]) if v["spread"] else None,
                "median_spread_ticks": median(v["ticks"]) if v["ticks"] else None,
                "median_total_depth_value": median(v["depth"]) if v["depth"] else None,
                "median_queue_imbalance":median(v["imbalance"]) if v["imbalance"] else None,
                "bar_count": len(v["spread"]) or len(v["depth"]), "snapshot_count": v["snapshots"],
                "symbols_count": len(v["symbols"])} for minute, v in sorted(grouped.items())]
    return {"source": selected_source, "symbol": symbol.upper() if symbol else None,
            "profile": profile, "basis": "completed_bars_1m", "window_minutes":window_minutes,
            "window_start":start_at,"completed_through_exclusive":end_exclusive,
            "session_date":session_date,"as_of":latest_bar,"data_as_of":latest_bar,
            "sessions_count":len({str(row.get("minute_vn") or "")[:10] for row in bars_data}),
            "note": "Chỉ dùng bar một phút đã hoàn tất; mỗi phút lấy trung vị bar của từng mã, sau đó lấy trung vị giữa các mã. Số mã tham gia được ghi tại từng điểm."}


@app.get("/api/market-depth")
def market_depth(window_minutes: int = Query(default=60, ge=5, le=390),
                 from_date: str | None = None, to_date: str | None = None):
    """Single-service, latest-session depth payload for the overview widget."""
    meta = _market_meta("ssi")
    payload = build_market_depth(window_minutes=window_minutes, source="ssi", market_meta=meta,
                                 start_date=from_date, end_date=to_date)
    payload["meta"] = meta
    return payload


@app.get("/api/liquidity-map")
def liquidity_map(window_minutes:int=Query(default=120,ge=15,le=390),
                  start_at: str | None = None, end_at: str | None = None,
                  from_date: str | None = None, to_date: str | None = None,
                  source: str | None = None):
    selected_source = source or _source()
    points=[]
    if selected_source in {"live","ssi"}:
        window_start,window_end=_market_window(window_minutes,selected_source)
        historical_range=bool(from_date or to_date)
        start_at=start_at or (f"{from_date}T00:00:00+07:00" if from_date else window_start)
        end_at=end_at or (f"{(datetime.fromisoformat(to_date)+timedelta(days=1)).date().isoformat()}T00:00:00+07:00" if to_date else window_end)
        if historical_range and to_date:
            meta=_market_meta(selected_source)
            if (str(to_date)[:10]==str(meta.get("session_date") or "")[:10]
                    and meta.get("state") in {"IN_SESSION","LUNCH_BREAK"} and meta.get("effective_now")):
                completed_cutoff=datetime.fromisoformat(meta["effective_now"]).replace(second=0,microsecond=0).isoformat(timespec="minutes")
                end_at=min(end_at,completed_cutoff)
        grouped=database.fetch_bars_window_by_symbol(selected_source,start_at,end_at,None if historical_range else window_minutes)
        minimum=config.RANKING_MIN_BARS
        for ticker,rows in grouped.items():
            spreads=_numeric([r["spread_bps"] for r in rows]); depths=_numeric([r["total_depth_value"] for r in rows])
            snapshot_count=sum(int(row.get("n_snapshots") or 0) for row in rows)
            if len(rows)>=minimum and snapshot_count>=config.RANKING_MIN_SNAPSHOTS and spreads and depths:
                points.append({"symbol":ticker,"median_spread_bps":median(spreads),"median_spread_ticks":median(_numeric([r.get("spread_ticks") for r in rows])),"median_total_depth_value":median(depths),"sample_count":len(rows),"snapshot_count":snapshot_count,"sample_kind":"bars_1m"})
    else:
        grouped=database.fetch_recent_observations_by_symbol(selected_source,limit,start_at,end_at)
        minimum=config.RANKING_MIN_SNAPSHOTS
        for ticker,rows in grouped.items():
            valid=[r for r in rows if r["validation_status"]=="valid"]
            spreads=_numeric([r["spread_bps"] for r in valid]); depths=_numeric([r["total_depth_value"] for r in valid])
            if len(valid)>=minimum and spreads and depths:
                points.append({"symbol":ticker,"median_spread_bps":median(spreads),"median_total_depth_value":median(depths),"sample_count":len(valid),"sample_kind":f"{selected_source}_observations"})
    security_metadata=database.fetch_security_metadata('ssi')
    quote_metadata=database.fetch_latest_quote_details(selected_source,[point['symbol'] for point in points])
    for point in points:
        profile=security_metadata.get(point['symbol'],{})
        quote=quote_metadata.get(point['symbol'],{})
        point.update({
            'name':profile.get('name_vi') or None,
            'exchange':profile.get('board') or None,
            'price':quote.get('last_price'),
            'price_as_of':quote.get('price_as_of'),
            'price_source':quote.get('price_source'),
            'tick_size':quote.get('tick_size'),
            'n_bars':point.get('sample_count',0),
        })
    spread_mid=median([p["median_spread_bps"] for p in points]) if points else None
    depth_mid=median([p["median_total_depth_value"] for p in points]) if points else None
    for point in points:
        point["quadrant"]=("spread_wide_depth_thin" if point["median_spread_bps"]>spread_mid and point["median_total_depth_value"]<depth_mid
            else "spread_narrow_depth_thick" if point["median_spread_bps"]<=spread_mid and point["median_total_depth_value"]>=depth_mid
            else "spread_wide_depth_thick" if point["median_spread_bps"]>spread_mid else "spread_narrow_depth_thin")
    candidate_symbols=len(grouped)
    eligible_symbols=len(points)
    points=sorted(points,key=lambda p:(p["snapshot_count"],p["sample_count"]),reverse=True)[:250]
    return {"source":selected_source,"points":points,"spread_median":spread_mid,"depth_median":depth_mid,"window_minutes":window_minutes,
        "minimum_samples":minimum,"minimum_snapshots":config.RANKING_MIN_SNAPSHOTS if selected_source in {"live","ssi"} else minimum,
        "candidate_symbols":candidate_symbols,"eligible_symbols":eligible_symbols,"shown_symbols":len(points),"depth_axis":"log",
        "note":"Bản đồ mô tả tương đối trong watchlist, không đại diện toàn thị trường."}


@app.post("/api/bookwalk/{symbol}")
def bookwalk(symbol: str, side: str = Query(..., pattern="^(buy|sell)$"),
             quantity: int | None = Query(default=None, ge=1),
             notional: float | None = Query(default=None, gt=0),
             max_slippage_bps: float | None = Query(default=None, ge=0),
             snapshot_at: str | None = Query(default=None)):
    selected_source = "ssi"
    rows = database.fetch_observations(selected_source, symbol=symbol, limit=2000)
    rows = [row for row in rows if row.get("validation_status") == "valid"]
    if snapshot_at:
        rows = [row for row in rows if (row.get("source_ts_vn") or row.get("received_at_vn") or row.get("received_at_utc")) == snapshot_at]
    if not rows:
        return {"status": "unavailable", "reason": "no snapshot for symbol", "symbol": symbol.upper()}
    row = rows[0]
    observation = {key: row.get(key) for key in (
        "bid_price_1", "bid_volume_1", "bid_price_2", "bid_volume_2", "bid_price_3", "bid_volume_3",
        "ask_price_1", "ask_volume_1", "ask_price_2", "ask_volume_2", "ask_price_3", "ask_volume_3", "market")}
    try:
        result = estimate_bookwalk(observation, side, quantity, notional, max_slippage_bps)
    except ValueError as exc:
        return {"status": "invalid_input", "reason": str(exc)}
    market_meta = _market_meta(selected_source)
    return {"symbol": symbol.upper(), "source": selected_source,
            "snapshot_at": row.get("source_ts_vn") or row.get("received_at_vn", row.get("received_at_utc")),
            "market_state": market_meta.get("state"), "session_date": market_meta.get("session_date"), **result}


@app.get("/api/alerts")
def alerts(limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0), symbol: str | None = Query(default=None, max_length=20)):
    selected_source = _source()
    selected_symbol = symbol.strip().upper() if symbol and symbol.strip() else None
    items = database.fetch_alerts(selected_source, limit, offset, selected_symbol)
    total_count = database.count_alerts(selected_source, selected_symbol)
    return {
        "source": selected_source,
        "baseline_min_observations": config.BASELINE_MIN_BARS,
        "baseline_min_bars": config.BASELINE_MIN_BARS,
        "baseline_sessions": config.BASELINE_SESSIONS,
        "min_baseline_sessions": config.MIN_BASELINE_SESSIONS,
        "peer_min_size": config.PEER_MIN_SIZE,
        "baseline_qualified_symbols": database.count_baseline_qualified_symbols(selected_source) if selected_source in {"live", "ssi"} else 0,
        "alerts": items,
        "alert_count": total_count,
        "offset": offset,
        "limit": limit,
        "symbol": selected_symbol,
        "has_more": offset + len(items) < total_count,
        "active_count": sum(1 for item in items if item.get("status") == "active"),
        "resolved_count": sum(1 for item in items if item.get("status") == "resolved"),
    }


@app.get("/api/collector/runs")
def collector_runs(limit: int = Query(default=20, ge=1, le=100)):
    return {"runs": database.recent_collector_runs(limit)}


@app.get("/api/quality")
def data_quality():
    report=database.data_quality_summary(_source())
    state=runtime.snapshot()
    with _health_snapshot_lock:
        run=_health_snapshot.get("worker_run")
        db_busy=bool(_health_snapshot.get("database_busy"))
    if run:
        for key in ("connection_status","subscription_status","subscription_topics","subscribed_boards",
                    "subscription_ack_count","subscription_error","quote_persistence_status",
                    "trade_subscription_status","trade_subscription_topics","trade_persistence_status",
                    "trade_messages_received","trade_messages_saved","trade_messages_dropped",
                    "last_trade_received_at","last_trade_persisted_at","trade_queue_depth",
                    "messages_received","rows_valid","rows_invalid","duplicate_messages",
                    "queue_dropped","queue_depth","reconnect_count","last_message_at","last_persisted_at","last_error"):
            if key in run:state[key]=run.get(key)
    state["status_db_busy"]=db_busy
    expected=bool(get_market_state(get_clock_now(),"ALL")["state"]=="IN_SESSION")
    active=bool(run and run.get("ended_at") is None and _pid_exists(run.get("process_id")))
    quote_status=_derive_pipeline_state(expected_stream=expected,worker_active=active,
        connection_status=state.get("connection_status"),subscription_status=state.get("subscription_status"),
        received_at=state.get("last_message_at"),persisted_at=state.get("last_persisted_at"),
        received_count=state.get("messages_received"),valid_count=state.get("rows_valid"),
        persistence_status=state.get("quote_persistence_status"),queue_depth=state.get("queue_depth",0),
        queue_dropped=state.get("queue_dropped",0),database_busy=db_busy)
    trade_status=_derive_pipeline_state(expected_stream=expected,worker_active=active,
        connection_status=state.get("connection_status"),subscription_status=state.get("trade_subscription_status"),
        received_at=state.get("last_trade_received_at"),persisted_at=state.get("last_trade_persisted_at"),
        received_count=state.get("trade_messages_received"),valid_count=state.get("trade_messages_saved"),
        persistence_status=state.get("trade_persistence_status"),queue_depth=state.get("trade_queue_depth",0),
        queue_dropped=state.get("trade_messages_dropped",0),database_busy=db_busy)
    report["as_of_vn"]=datetime.now(config.TIMEZONE).isoformat(timespec="seconds")
    report["scope_note"]="Toàn bộ snapshot đã lưu của nguồn đang chọn; không giới hạn theo cửa sổ biểu đồ."
    report["baseline_min_bars"]=config.BASELINE_MIN_BARS
    report["baseline_sessions"]=config.BASELINE_SESSIONS
    report["min_baseline_sessions"]=config.MIN_BASELINE_SESSIONS
    report["peer_min_size"]=config.PEER_MIN_SIZE
    report["baseline_qualified_symbols"]=database.count_baseline_qualified_symbols(_source()) if _source() in {"live","ssi"} else 0
    report["current_collector"]={key:state.get(key) for key in (
        "connection_status","subscription_status","messages_received","rows_valid",
        "rows_invalid","duplicate_messages","queue_dropped","queue_depth","queue_coalesced",
        "reconnect_count","last_message_at","last_persisted_at","last_error",
        "trade_messages_received","trade_messages_saved","trade_messages_dropped",
        "last_trade_received_at","last_trade_persisted_at","trade_queue_depth",
        "subscription_topics","subscribed_boards","subscription_ack_count","subscription_error",
        "quote_persistence_status","trade_subscription_status","trade_subscription_topics","trade_persistence_status"
    )}
    report["current_collector"].update(realtime_verified=quote_status["verified"],
        feed_state=quote_status["state"],quote_pipeline=quote_status,
        trade_realtime_verified=trade_status["verified"],trade_feed_state=trade_status["state"],
        trade_pipeline=trade_status,worker_active=active,database_busy=db_busy)
    return report


@app.get("/api/calendar/{session_date}")
def get_session_calendar(session_date: str, market: str = "HOSE"):
    try:
        parsed = datetime.fromisoformat(session_date).date()
    except ValueError:
        return {"error": "session_date must use YYYY-MM-DD"}
    with database.connect() as db:
        row = db.execute("SELECT * FROM session_calendar WHERE session_date=? AND market=?", (parsed.isoformat(), market.upper())).fetchone()
    return dict(row) if row else {"session_date": parsed.isoformat(), "market": market.upper(),
                                  "is_open": database.is_session_open(parsed.isoformat(), market),
                                  "source": "weekday_default; configure exchange holidays explicitly"}


@app.put("/api/calendar/{session_date}")
def set_session_calendar(session_date: str, market: str = "HOSE", is_open: bool = Body(...), note: str | None = Body(default=None)):
    try:
        parsed = datetime.fromisoformat(session_date).date()
    except ValueError:
        return {"error": "session_date must use YYYY-MM-DD"}
    database.set_session_day(parsed.isoformat(), market, is_open, note)
    return {"session_date": parsed.isoformat(), "market": market.upper(), "is_open": is_open, "note": note}


@app.get("/api/methodology")
def methodology():
    return {
        "project": "Stock Liquidity Intelligence",
        "source": "Dữ liệu báo giá thị trường SSI FastConnect qua WebSocket; snapshot được lưu vào SQLite sau khi xác thực và đăng ký quote thành công.",
        "fields": [
            "symbol", "bidPrice1-3", "bidVol1-3",
            "askPrice1-3", "askVol1-3",
            "timestamp/tradingTime if actually present",
        ],
        "metrics": [
            {"name": "Mid-price", "formula": "(ask1 + bid1) / 2"},
            {"name": "Quoted spread (bps)", "formula": "((ask1 - bid1) / mid-price) * 10000"},
            {"name": "Bid/ask depth quantity", "formula": "sum of valid displayed volumes across up to 3 levels"},
            {"name": "Bid/ask depth value", "formula": "sum(price * volume) across valid displayed levels"},
            {"name": "Queue imbalance", "formula": "(bid depth quantity - ask depth quantity) / total depth quantity"},
            {"name": "Value imbalance", "formula": "(bid depth value - ask depth value) / total depth value"},
        ],
        "interpretations": [
            {"metric": "Chênh lệch theo báo giá (quoted spread)", "meaning": "Chi phí tức thời quan sát được giữa giá mua tốt nhất và giá bán tốt nhất. Chênh lệch giá (spread) thấp thường thuận lợi hơn cho giao dịch nhỏ tại thời điểm ảnh chụp dữ liệu (snapshot).", "caveat": "Không tính phí, trượt giá, kích thước lệnh hay khả năng khớp; cần so sánh theo thời gian và giữa các mã cùng bối cảnh."},
            {"metric": "Độ sâu ba mức giá đầu (top-3 depth)", "meaning": "Giá trị chào mua/chào bán đang hiển thị trong ba mức giá tốt nhất. Độ sâu (depth) lớn biểu thị nhiều lượng niêm yết hơn gần giá hiện tại.", "caveat": "Lệnh có thể bị hủy; đây không phải thanh khoản thực hiện được hay toàn bộ sổ lệnh."},
            {"metric": "Mất cân bằng khối lượng chờ (queue imbalance)", "meaning": "Chuẩn hóa chênh lệch khối lượng hiển thị hai phía về khoảng [-1, 1]. Số dương nghĩa là khối lượng bên mua (bid) lớn hơn bên bán (ask) trong các mức hợp lệ.", "caveat": "Mô tả trạng thái sổ lệnh, không phải mất cân bằng dòng lệnh (order-flow imbalance, OFI), không chứng minh chiều giá tiếp theo."},
            {"metric": "Phân loại thanh khoản theo phân vị lịch sử", "meaning": "Thuận lợi khi spread ≤ P25 và depth ≥ P75; căng thẳng khi spread ≥ P90 hoặc depth ≤ P10; vùng giữa là cần lưu ý. So sánh với lịch sử cùng mã/cùng pha.", "caveat": "Đây là quy ước sàng lọc nội bộ, không phải chuẩn của cơ quan quản lý hay lý thuyết tài chính. Chỉ phân loại khi mỗi chỉ tiêu có ít nhất 120 bar hợp lệ, trải trên 120 phiên lịch sử riêng biệt."}
        ],
        "theoretical_framework": [
            {"title": "Chênh lệch giá (spread) như chi phí giao dịch tức thời", "body": "Chênh lệch giá mua – bán (bid–ask spread) biểu thị khoảng cách giữa giá mua tốt nhất và giá bán tốt nhất đang được niêm yết. Theo vi cấu trúc thị trường, mức chênh lệch này bù đắp một phần chi phí xử lý lệnh, nắm giữ tồn kho và rủi ro giao dịch với đối tác có thông tin tốt hơn. SLI tính chênh lệch theo báo giá (quoted spread) bằng điểm cơ bản (bps) so với giá giữa (mid-price) để giảm lệ thuộc vào thang giá tuyệt đối. Đây là chỉ báo từ báo giá, không phải chênh lệch thực tế (effective spread) tính từ giao dịch đã khớp."},
            {"title": "Độ sâu sổ lệnh (depth) và khả năng hấp thụ lệnh", "body": "Độ sâu ba mức giá đầu (top-3 depth) cộng lượng niêm yết ở ba mức giá tốt nhất cho mỗi phía; giá trị độ sâu (depth value) nhân giá từng mức với khối lượng tương ứng. Nhiều lượng hiển thị gần giá có thể cho thấy vùng đệm niêm yết dày hơn, nhưng không chứng minh thị trường hấp thụ được một lệnh cụ thể: lệnh có thể bị rút, thứ tự ưu tiên và tốc độ cập nhật có ý nghĩa, còn SLI không mô phỏng khớp lệnh."},
            {"title": "Mất cân bằng khối lượng chờ (queue imbalance) là ảnh chụp sổ lệnh", "body": "Chỉ số mất cân bằng khối lượng chờ (queue imbalance) chuẩn hóa chênh lệch tổng khối lượng bên mua (bid) và bên bán (ask). Chỉ số gần +1 cho biết lượng mua hiển thị trội; gần −1 cho biết lượng bán trội; gần 0 cho biết tương đối cân bằng. Nghiên cứu mất cân bằng dòng lệnh (order-flow imbalance, OFI) thường xét biến động sổ lệnh qua sự kiện thêm, hủy và khớp lệnh. Vì SLI hiện tính từ ảnh chụp dữ liệu (snapshot), không được gọi chỉ số này là OFI hoặc dùng như dự báo giá."},
            {"title": "Mốc tham chiếu riêng từng mã và ngưỡng phân loại dùng chung", "body": "Thuận lợi: spread ≤ P25 và depth ≥ P75. Căng thẳng: spread ≥ P90 hoặc depth ≤ P10. Đây là quy ước sàng lọc nội bộ, không phải chuẩn bắt buộc. Baseline lấy tối đa 120 phiên trước, cùng mã và cùng pha trong ngày; chỉ phân loại khi mỗi chỉ tiêu có ít nhất 120 bar hợp lệ, trải trên 120 phiên lịch sử riêng biệt. Đọc cùng số mẫu và trạng thái nguồn."},
            {"title": "Tính nhất quán và dữ liệu thiếu", "body": "Mỗi ảnh chụp dữ liệu (snapshot) được lưu cùng gói dữ liệu gốc (payload), thời điểm nhận cục bộ, mã nguồn và trạng thái kiểm tra; các chỉ tiêu có thể được tính lại từ trường mua/bán (bid/ask) hợp lệ. Trường thiếu được để null (không có giá trị), không tự thay bằng 0. Nếu gói dữ liệu (payload) không có mốc thời gian máy chủ (server timestamp), thời điểm nhận không thể thay thế chính xác thời điểm tạo dữ liệu."}
        ],
        "references": [
            {"citation": "Glosten, L. R. & Milgrom, P. R. (1985). Bid, ask and transaction prices in a specialist market with heterogeneously informed traders.", "doi": "10.1016/0304-405X(85)90044-3", "relevance": "Cơ sở kinh tế học cho quoted bid–ask spread và chi phí giao dịch bất lợi."},
            {"citation": "Hasbrouck, J. (2007). Empirical Market Microstructure: The Institutions, Economics, and Econometrics of Securities Trading.", "doi": "10.1093/oso/9780195301649.001.0001", "relevance": "Khung đo lường và diễn giải dữ liệu vi cấu trúc thị trường."},
            {"citation": "Amihud, Y. (2002). Illiquidity and stock returns: cross-section and time-series effects.", "doi": "10.1016/S1384-4181(01)00024-6", "relevance": "Nghiên cứu tính kém thanh khoản; SLI không tính Amihud illiquidity nếu không có dữ liệu giao dịch phù hợp."},
            {"citation": "Kyle, A. S. (1985). Continuous auctions and insider trading.", "doi": "10.2307/1913210", "relevance": "Nền tảng về độ sâu và tác động giá trong đấu giá liên tục."},
            {"citation": "Cont, R., Kukanov, A. & Stoikov, S. (2014). The price impact of order book events.", "doi": "10.1093/jjfinec/nbt003", "relevance": "Phân tích OFI từ sự kiện sổ lệnh; SLI hiện đo queue imbalance snapshot, không đồng nhất với OFI."},
            {"citation": "Hyndman, R. J. & Fan, Y. (1996). Sample quantiles in statistical packages.", "doi": "10.1080/00031305.1996.10473566", "relevance": "Tham khảo phương pháp phân vị mẫu cho baseline cảnh báo."}
        ],
        "limitations": [
            "Top-3 displayed depth is not the full order book or guaranteed executable liquidity.",
            "Imbalance describes the displayed book and is not a price prediction.",
            "Missing fields remain unavailable; they are never replaced with zero.",
            "Without a source timestamp, received_at_utc is only the local receive time.",
            "Demo values are synthetic and must not be interpreted as market data.",
        ],
        "configuration": {
            "baseline_min_bars": config.BASELINE_MIN_BARS,
            "baseline_sessions": config.BASELINE_SESSIONS,
            "min_baseline_sessions": config.MIN_BASELINE_SESSIONS,
            "peer_min_size": config.PEER_MIN_SIZE,
            "minimum_snapshots_per_bar": config.MIN_BAR_SNAPSHOTS,
            "alert_consecutive_bars": config.ALERT_CONSECUTIVE_BARS,
            "alert_cooldown_minutes": config.ALERT_COOLDOWN_MIN,
            "favorable_spread_percentile": config.FAVORABLE_SPREAD_PERCENTILE,
            "stress_spread_percentile": config.STRESS_SPREAD_PERCENTILE,
            "favorable_depth_percentile": config.FAVORABLE_DEPTH_PERCENTILE,
            "stress_depth_percentile": config.STRESS_DEPTH_PERCENTILE,
            "spread_percentile": config.SPREAD_PERCENTILE,
            "depth_percentile": config.DEPTH_PERCENTILE,
            "imbalance_threshold": config.IMBALANCE_THRESHOLD,
        },
    }


@app.post("/api/collector/start")
def start_collector():
    global _worker_process
    try:
        current = database.latest_collector_run(timeout=1)
    except Exception:
        return {"ok": False, "message": "Cơ sở dữ liệu đang bận; giữ nguyên collector hiện tại, không khởi chạy bản trùng."}
    if current and current.get("ended_at") is None and current.get("heartbeat_at"):
        try:
            age = (datetime.now(config.TIMEZONE) - datetime.fromisoformat(current["heartbeat_at"])).total_seconds()
        except ValueError:
            age = float("inf")
        if age <= 15:
            return {"ok": True, "message": "Tiến trình thu thập SSI đang chạy.",
                    "worker_process_id": current.get("process_id"), "worker_status": "running"}
        if _pid_exists(current.get("process_id")):
            return {"ok": True, "message": "Collector vẫn còn chạy; heartbeat database đang trễ nên giữ nguyên PID hiện tại, không tạo worker trùng.",
                    "worker_process_id": current.get("process_id"), "worker_status": "heartbeat_delayed"}
    script = config.ROOT_DIR / "scripts" / "ssi_worker.py"
    log_dir = config.ROOT_DIR / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = (log_dir / "ssi_worker.log").open("a", encoding="utf-8")
    except OSError:
        return {"ok": False, "message": "Không thể tạo nhật ký kết nối. Kiểm tra quyền ghi thư mục dự án.",
                "status": status()}
    options = {"cwd": str(config.ROOT_DIR), "stdin": subprocess.DEVNULL,
               "stdout": log_file, "stderr": subprocess.STDOUT, "close_fds": True}
    if os.name == "nt":
        # Keep a hidden, redirected child process. DETACHED_PROCESS can sever
        # inherited handles in some Windows launch contexts and hide its errors.
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    try:
        _worker_process = subprocess.Popen([sys.executable, "-u", str(script)], **options)
    except OSError as exc:
        log_file.close()
        return {"ok": False, "message": "Không thể khởi chạy tiến trình thu thập dữ liệu. Hãy kiểm tra cấu hình ứng dụng.",
                "status": status()}
    log_file.close()
    return {"ok": True, "message": "Đã yêu cầu kết nối SSI; trạng thái sẽ tự cập nhật.",
            "worker_process_id": _worker_process.pid, "status": status()}


@app.post("/api/collector/ssi/start")
def start_ssi_collector():
    return start_collector()


@app.post("/api/collector/stop")
def stop_collector():
    current = database.latest_collector_run()
    if not current or current.get("ended_at") is not None:
        return {"ok": True, "message": "Không có tiến trình thu thập đang chạy.", "status": status()}
    database.request_collector_stop(current.get("worker_id"))
    return {"ok": True, "message": "Đã yêu cầu ngắt kết nối SSI.", "status": status()}


@app.post("/api/collector/demo")
def use_demo():
    raise HTTPException(status_code=410, detail="Chế độ mô phỏng đã tắt; hệ thống chỉ dùng dữ liệu SSI.")




