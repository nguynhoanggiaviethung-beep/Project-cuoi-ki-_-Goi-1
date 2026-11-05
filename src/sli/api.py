from datetime import datetime, timezone
from statistics import median
from functools import lru_cache
import threading
import time

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, database
from .bookwalk import estimate as estimate_bookwalk
from .market_rules import session_phase
from .collector import RuntimeState
from .ssi_collector import SsiCollector

STATIC_DIR = config.ROOT_DIR / "frontend"
runtime = RuntimeState()
ssi_collector = SsiCollector(runtime)
_demo_thread = None
_demo_stop = threading.Event()

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
app = FastAPI(
    title="Stock Liquidity Intelligence",
    description="Local dashboard for observing displayed stock order-book liquidity.",
    version="0.1.0",
)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.on_event("startup")
def startup():
    database.initialize()
    ssi_collector.start()


@app.on_event("shutdown")
def shutdown():
    ssi_collector.stop()
    _stop_demo_loop()


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


def _source():
    return runtime.snapshot()["mode"]


def _numeric(values):
    return [float(value) for value in values if value is not None]


def _serialize_row(row):
    result = dict(row)
    for key, value in list(result.items()):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            result[key] = None
    return result


@app.get("/api/status")
def status():
    state = runtime.snapshot()
    source = state["mode"]
    rows = database.fetch_observations(source, limit=1)
    latest = rows[0]["received_at_utc"] if rows else None
    is_stale = False
    now = datetime.now(config.TIMEZONE)
    markets = set(database.list_catalog_boards("ssi")) or {"HOSE", "HNX", "UPCOM"}
    phases = {session_phase(now, market) for market in markets}
    market_open = any(database.is_session_open(now.date().isoformat(), market) for market in markets)
    expected_stream = market_open and bool(phases.intersection({"CONTINUOUS_AM", "CONTINUOUS_PM"}))
    if source in {"live", "ssi"}:
        if latest and expected_stream:
            try:
                stamp = datetime.fromisoformat(latest.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                is_stale = (
                    datetime.now(timezone.utc) - stamp
                ).total_seconds() > config.STALE_AFTER_SECONDS
            except ValueError:
                is_stale = True
        elif not latest and expected_stream:
            is_stale = True
    state.update({
        "source": source,
        "last_data_at": latest,
        "is_stale": is_stale,
        "stale_after_seconds": config.STALE_AFTER_SECONDS,
        "configured_watchlist": config.WATCHLIST,
        "historical_symbols_with_data": len(database.list_historical_symbols("ssi")),
        "market_phase": sorted(phases)[0] if len(phases) == 1 else sorted(phases),
        "market_session_expected": expected_stream,
    })
    if source == "demo":
        state["connection_status"] = "demo"
    elif is_stale and state["connection_status"] == "connected":
        state["connection_status"] = "stale"
    database.update_stale_alert(source, is_stale, config.STALE_AFTER_SECONDS, latest)
    return state


@app.get("/api/price-history")
def price_history(symbol: str | None = None, limit: int = Query(default=90, ge=1, le=1000),
                 page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200),
                 search: str | None = None):
    selected = symbol.upper() if symbol and symbol.upper() not in ("ALL", "__ALL__") else None
    if selected:
        rows=database.fetch_historical_prices(selected,limit=limit,source="ssi")
        return {"source":"ssi","timeframe":"1d","symbols_count":1 if rows else 0,
                "symbols":[selected] if rows else [],"series_by_symbol":{selected:rows} if rows else {},
                "note":"Giá OHLC ngày lịch sử từ SSI Market Data API; không phải quote realtime hoặc dữ liệu sổ lệnh."}
    catalog=database.fetch_latest_historical_page(search=search,page=page,page_size=page_size,source="ssi")
    return {"source":"ssi","timeframe":"1d","symbols_count":catalog["total"],
            "page":catalog["page"],"page_size":catalog["page_size"],"total":catalog["total"],
            "rows":catalog["rows"],"note":"Danh mục cổ phiếu thường và giá OHLC mới nhất lấy trực tiếp từ SSI; giá chưa đồng bộ được để trống."}


@lru_cache(maxsize=8)
def _cached_market_leaders(limit):
    return database.fetch_market_leaders(limit=limit,source="ssi")


@app.get("/api/market-leaders")
def market_leaders(limit: int = Query(default=25, ge=5, le=100)):
    rows=_cached_market_leaders(limit)
    return {"source":"ssi","rows":rows,"count":len(rows),
            "note":"OHLC ngày SSI; giá trị giao dịch lấy từ SSI hoặc giá đóng cửa × khối lượng nếu SSI không có trường giá trị. Mức đổi giá so với phiên trước."}


@lru_cache(maxsize=8)
def _cached_market_breadth(limit):
    return database.fetch_market_breadth(limit=limit,source="ssi")


@app.get("/api/market-breadth")
def market_breadth(limit: int = Query(default=90, ge=2, le=1000)):
    rows=_cached_market_breadth(limit)
    return {"source":"ssi","rows":rows,"count":len(rows),
            "note":"Độ rộng thị trường được tổng hợp từ OHLC ngày SSI của cổ phiếu thường: số mã tăng/giảm/đứng giá so với phiên trước. Không phải dữ liệu sổ lệnh."}


@app.get("/api/symbols")
def symbols():
    source = _source()
    available = database.list_catalog_symbols("ssi") if source == "ssi" else database.list_symbols(source)
    if source == "live" and not available: available=config.WATCHLIST
    return {"source":source,"symbols":available,"count":len(available)}

@app.get("/api/overview")
def overview(symbol: str | None = None, limit: int = Query(default=240, ge=1, le=2000),
             start_at: str | None = None, end_at: str | None = None,
             phase: str | None = Query(default=None, pattern="^(ATO|CONTINUOUS_AM|BREAK|CONTINUOUS_PM|ATC|CLOSED)$")):
    source = _source()
    available = database.list_symbols(source)
    selected = symbol.upper() if symbol and symbol.upper() not in ("__ALL__", "ALL") else None
    groups = {}
    for ticker in ([selected] if selected else available):
        rows = list(reversed(database.fetch_observations(source, symbol=ticker, limit=limit,
                                                         start_at=start_at, end_at=end_at, phase=phase)))
        if rows:
            groups[ticker] = [_serialize_row(row) for row in rows]
    flattened = [row for series in groups.values() for row in series]
    valid = [row for row in flattened if row["validation_status"] == "valid"]
    spreads = _numeric([row["spread_bps"] for row in valid])
    depths = _numeric([row["total_depth_value"] for row in valid])
    imbalances = _numeric([row["queue_imbalance"] for row in valid])
    count = len(flattened)
    return {"source": source, "symbol": selected, "symbols_count": len(groups),
        "observation_count": count, "valid_observation_count": len(valid),
        "valid_snapshot_rate": len(valid) / count if count else None,
        "median_spread_bps": median(spreads) if spreads else None,
        "median_total_depth_value": median(depths) if depths else None,
        "median_queue_imbalance": median(imbalances) if imbalances else None,
        "series": groups.get(selected, []) if selected else [], "series_by_symbol": groups}


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
    profile = profiles.get(ticker, {"name": ticker, "sector": "Chưa xác định", "description": "Tên doanh nghiệp chưa có trong danh mục cục bộ; hãy bổ sung vào danh mục doanh nghiệp của hệ thống."})
    return {"symbol": ticker, **profile}


@app.get("/api/rankings")
def rankings(limit: int = Query(default=500, ge=1, le=2000), start_at: str | None = None,
              end_at: str | None = None, phase: str | None = Query(default=None, pattern="^(OPEN|MID|CLOSE)$")):
    source = _source()
    results = []
    for symbol in database.list_symbols(source):
        bars_for_symbol = database.fetch_bars(source, symbol=symbol, limit=limit,
                                              start_at=start_at, end_at=end_at, bucket=phase)
        if source in {"live", "ssi"} and bars_for_symbol:
            points = bars_for_symbol
            spreads = _numeric([row["spread_bps"] for row in points])
            depths = _numeric([row["total_depth_value"] for row in points])
            valid_count = len(points)
            sample_kind = "bars_1m"
            latest_spread = points[-1]["spread_bps"]
            latest_depth = points[-1]["total_depth_value"]
            snapshot_count = len(database.fetch_observations(source, symbol=symbol, limit=limit,
                                                             start_at=start_at, end_at=end_at))
        else:
            rows = database.fetch_observations(source, symbol=symbol, limit=limit,
                                               start_at=start_at, end_at=end_at)
            valid = [row for row in rows if row["validation_status"] == "valid"]
            spreads = _numeric([row["spread_bps"] for row in valid])
            depths = _numeric([row["total_depth_value"] for row in valid])
            valid_count = len(valid)
            snapshot_count = len(rows)
            sample_kind = "synthetic_demo_snapshots" if source == "demo" else "snapshots"
            latest_spread = valid[0]["spread_bps"] if valid else None
            latest_depth = valid[0]["total_depth_value"] if valid else None
        if valid_count == 0:
            continue
        results.append({
            "symbol": symbol,
            "snapshot_count": snapshot_count,
            "sample_count": valid_count,
            "bar_count": valid_count if sample_kind == "bars_1m" else 0,
            "sample_kind": sample_kind,
            "median_spread_bps": median(spreads) if spreads else None,
            "median_total_depth_value": median(depths) if depths else None,
            "latest_spread_bps": latest_spread,
            "latest_total_depth_value": latest_depth,
            "spread_expansion_bps": (latest_spread - median(spreads)) if spreads and latest_spread is not None else None,
            "depth_change_pct": ((latest_depth / median(depths) - 1) * 100) if depths and median(depths) and latest_depth is not None else None,
        })
    minimum_samples = config.RANKING_MIN_BARS if source in {"live", "ssi"} else config.RANKING_MIN_SNAPSHOTS
    eligible = [row for row in results if row["sample_count"] >= minimum_samples]
    low_spread = sorted(eligible, key=lambda row: row["median_spread_bps"] if row["median_spread_bps"] is not None else float("inf"))
    high_depth = sorted(eligible, key=lambda row: row["median_total_depth_value"] if row["median_total_depth_value"] is not None else float("-inf"), reverse=True)
    depth_falling = sorted((row for row in eligible if row["depth_change_pct"] is not None), key=lambda row: row["depth_change_pct"])
    spread_widening = sorted((row for row in eligible if row["spread_expansion_bps"] is not None), key=lambda row: row["spread_expansion_bps"], reverse=True)
    return {
        "source": source,
        "sample_window": limit,
        "minimum_eligible_samples": minimum_samples,
        "filter": {"start_at": start_at, "end_at": end_at, "phase": phase},
        "lowest_spread": low_spread,
        "highest_depth": high_depth,
        "depth_falling": depth_falling,
        "spread_widening": spread_widening,
        "symbols": results,
    }


@app.get("/api/bars")
def bars(symbol: str | None = None, limit: int = Query(default=1000, ge=1, le=10000),
         source: str | None = None, start_at: str | None = None, end_at: str | None = None,
         phase: str | None = Query(default=None, pattern="^(OPEN|MID|CLOSE)$")):
    return {"source": source or _source(), "bars": database.fetch_bars(
        source or _source(), symbol, limit, start_at, end_at, phase)}


@app.get("/api/intraday-profile")
def intraday_profile(symbol: str | None = None, start_at: str | None = None,
                     end_at: str | None = None, source: str | None = None):
    selected_source = source or _source()
    bars_data = database.fetch_bars(selected_source, symbol, 10000, start_at, end_at)
    grouped = {}
    for row in bars_data:
        minute = datetime.fromisoformat(row["minute_vn"]).strftime("%H:%M")
        slot = grouped.setdefault(minute, {"spread": [], "ticks": [], "depth": [], "snapshots": 0, "symbols": set()})
        for key, target in (("spread_bps", "spread"), ("spread_ticks", "ticks"), ("total_depth_value", "depth")):
            if row.get(key) is not None:
                slot[target].append(row[key])
        slot["snapshots"] += row["n_snapshots"]
        slot["symbols"].add(row["symbol"])
    profile = [{"minute": minute, "median_spread_bps": median(v["spread"]) if v["spread"] else None,
                "median_spread_ticks": median(v["ticks"]) if v["ticks"] else None,
                "median_total_depth_value": median(v["depth"]) if v["depth"] else None,
                "bar_count": len(v["spread"]) or len(v["depth"]), "snapshot_count": v["snapshots"],
                "symbols_count": len(v["symbols"])} for minute, v in sorted(grouped.items())]
    return {"source": selected_source, "symbol": symbol.upper() if symbol else None,
            "profile": profile, "basis": "bars_1m", "note": "Chỉ tổng hợp bar đã lưu trong phiên liên tục."}


@app.get("/api/liquidity-map")
def liquidity_map(limit: int = Query(default=2000, ge=1, le=10000),
                  start_at: str | None = None, end_at: str | None = None,
                  source: str | None = None):
    selected_source = source or _source()
    points = []
    for symbol in database.list_symbols(selected_source):
        bars_data = database.fetch_bars(selected_source, symbol, limit, start_at, end_at)
        if selected_source in {"live", "ssi"} and bars_data:
            spreads = _numeric([row["spread_bps"] for row in bars_data])
            depths = _numeric([row["total_depth_value"] for row in bars_data])
            count, basis = len(bars_data), "bars_1m"
        else:
            rows = database.fetch_observations(selected_source, symbol, limit, start_at, end_at)
            valid = [row for row in rows if row["validation_status"] == "valid"]
            spreads = _numeric([row["spread_bps"] for row in valid])
            depths = _numeric([row["total_depth_value"] for row in valid])
            count, basis = len(valid), "synthetic_demo_snapshots" if selected_source == "demo" else "snapshots"
        minimum = config.RANKING_MIN_BARS if basis == "bars_1m" else config.RANKING_MIN_SNAPSHOTS
        if count >= minimum and spreads and depths:
            points.append({"symbol": symbol, "median_spread_bps": median(spreads),
                           "median_total_depth_value": median(depths), "sample_count": count,
                           "sample_kind": basis})
    spread_mid = median([p["median_spread_bps"] for p in points]) if points else None
    depth_mid = median([p["median_total_depth_value"] for p in points]) if points else None
    for point in points:
        point["quadrant"] = ("spread_wide_depth_thin" if point["median_spread_bps"] > spread_mid and point["median_total_depth_value"] < depth_mid
                             else "spread_narrow_depth_thick" if point["median_spread_bps"] <= spread_mid and point["median_total_depth_value"] >= depth_mid
                             else "spread_wide_depth_thick" if point["median_spread_bps"] > spread_mid
                             else "spread_narrow_depth_thin")
    return {"source": selected_source, "points": points, "spread_median": spread_mid,
            "depth_median": depth_mid, "minimum_samples": config.RANKING_MIN_BARS if selected_source in {"live", "ssi"} else config.RANKING_MIN_SNAPSHOTS,
            "depth_axis": "log", "note": "Bản đồ mô tả tương đối trong watchlist, không đại diện toàn thị trường."}


@app.post("/api/bookwalk/{symbol}")
def bookwalk(symbol: str, side: str = Query(..., pattern="^(buy|sell)$"),
             quantity: int | None = Query(default=None, ge=1),
             notional: float | None = Query(default=None, gt=0),
             max_slippage_bps: float | None = Query(default=None, ge=0)):
    rows = database.fetch_observations(_source(), symbol=symbol, limit=1)
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
    return {"symbol": symbol.upper(), "snapshot_at": row.get("received_at_vn", row.get("received_at_utc")), **result}


@app.get("/api/alerts")
def alerts(limit: int = Query(default=100, ge=1, le=500)):
    state = status()
    items = database.fetch_alerts(_source(), limit)
    return {
        "source": _source(),
        "baseline_min_observations": config.BASELINE_MIN_BARS,
        "baseline_min_bars": config.BASELINE_MIN_BARS,
        "baseline_sessions": config.BASELINE_SESSIONS,
        "alerts": items[:limit],
    }


@app.get("/api/collector/runs")
def collector_runs(limit: int = Query(default=20, ge=1, le=100)):
    return {"runs": database.recent_collector_runs(limit)}


@app.get("/api/quality")
def data_quality():
    return database.data_quality_summary(_source())


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
            {"metric": "Cảnh báo theo phân vị (percentile)", "meaning": "So sánh chênh lệch giá (spread) hiện tại với phân vị 90 (P90) lịch sử hoặc độ sâu (depth) với phân vị 10 (P10) lịch sử của chính mã sau khi đạt số mẫu tối thiểu đã cấu hình.", "caveat": "Ngưỡng là quy tắc giám sát nội bộ; kết quả phụ thuộc cửa sổ, phiên, chất lượng dữ liệu và không phải chuẩn đầu tư."}
        ],
        "theoretical_framework": [
            {"title": "Chênh lệch giá (spread) như chi phí giao dịch tức thời", "body": "Chênh lệch giá mua – bán (bid–ask spread) biểu thị khoảng cách giữa giá mua tốt nhất và giá bán tốt nhất đang được niêm yết. Theo vi cấu trúc thị trường, mức chênh lệch này bù đắp một phần chi phí xử lý lệnh, nắm giữ tồn kho và rủi ro giao dịch với đối tác có thông tin tốt hơn. SLI tính chênh lệch theo báo giá (quoted spread) bằng điểm cơ bản (bps) so với giá giữa (mid-price) để giảm lệ thuộc vào thang giá tuyệt đối. Đây là chỉ báo từ báo giá, không phải chênh lệch thực tế (effective spread) tính từ giao dịch đã khớp."},
            {"title": "Độ sâu sổ lệnh (depth) và khả năng hấp thụ lệnh", "body": "Độ sâu ba mức giá đầu (top-3 depth) cộng lượng niêm yết ở ba mức giá tốt nhất cho mỗi phía; giá trị độ sâu (depth value) nhân giá từng mức với khối lượng tương ứng. Nhiều lượng hiển thị gần giá có thể cho thấy vùng đệm niêm yết dày hơn, nhưng không chứng minh thị trường hấp thụ được một lệnh cụ thể: lệnh có thể bị rút, thứ tự ưu tiên và tốc độ cập nhật có ý nghĩa, còn SLI không mô phỏng khớp lệnh."},
            {"title": "Mất cân bằng khối lượng chờ (queue imbalance) là ảnh chụp sổ lệnh", "body": "Chỉ số mất cân bằng khối lượng chờ (queue imbalance) chuẩn hóa chênh lệch tổng khối lượng bên mua (bid) và bên bán (ask). Chỉ số gần +1 cho biết lượng mua hiển thị trội; gần −1 cho biết lượng bán trội; gần 0 cho biết tương đối cân bằng. Nghiên cứu mất cân bằng dòng lệnh (order-flow imbalance, OFI) thường xét biến động sổ lệnh qua sự kiện thêm, hủy và khớp lệnh. Vì SLI hiện tính từ ảnh chụp dữ liệu (snapshot), không được gọi chỉ số này là OFI hoặc dùng như dự báo giá."},
            {"title": "Mốc tham chiếu (baseline) riêng từng mã và cảnh báo theo phân vị", "body": "Ngưỡng được tính từ phân phối lịch sử của chính mã: chênh lệch giá (spread) cao hơn phân vị 90 (P90) hoặc độ sâu sổ lệnh (depth) thấp hơn phân vị 10 (P10) khi đủ số mẫu tối thiểu đã cấu hình. Cách so sánh theo mã giúp hạn chế việc áp một thang giá trị tuyệt đối cho các cổ phiếu khác nhau. Phân vị (percentile) nhạy với cửa sổ, phiên giao dịch, ngoại lệ và chế độ thị trường; cần đọc cảnh báo cùng số mẫu và trạng thái nguồn."},
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
            "minimum_snapshots_per_bar": config.MIN_BAR_SNAPSHOTS,
            "alert_consecutive_bars": config.ALERT_CONSECUTIVE_BARS,
            "alert_cooldown_minutes": config.ALERT_COOLDOWN_MIN,
            "spread_percentile": config.SPREAD_PERCENTILE,
            "depth_percentile": config.DEPTH_PERCENTILE,
            "imbalance_threshold": config.IMBALANCE_THRESHOLD,
        },
    }


@app.post("/api/collector/start")
def start_collector():
    _stop_demo_loop()
    started = ssi_collector.start()
    return {"ok": True,
            "message": "Đã yêu cầu kết nối SSI FastConnect WebSocket." if started else "Collector SSI WebSocket đang chạy.",
            "status": runtime.snapshot()}


@app.post("/api/collector/ssi/start")
def start_ssi_collector():
    return start_collector()


@app.post("/api/collector/stop")
def stop_collector():
    ssi_collector.stop()
    return {"ok": True, "status": runtime.snapshot()}


@app.post("/api/collector/demo")
def use_demo():
    ssi_collector.stop()
    runtime.update(mode="demo", connection_status="demo", last_error=None)
    _start_demo_loop()
    return {"ok": True, "status": runtime.snapshot()}
