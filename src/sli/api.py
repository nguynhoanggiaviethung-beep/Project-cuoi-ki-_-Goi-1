from datetime import datetime, timezone
from statistics import median
import threading
import time

from fastapi import Body, FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, database
from .bookwalk import estimate as estimate_bookwalk
from .market_rules import session_phase
from .collector import NeoCollector, RuntimeState

STATIC_DIR = config.ROOT_DIR / "frontend"
runtime = RuntimeState()
collector = NeoCollector(runtime)
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
    runtime.update(mode="demo", connection_status="demo", watchlist=config.WATCHLIST)
    _start_demo_loop()


@app.on_event("shutdown")
def shutdown():
    collector.stop()
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
    markets = {config.SYMBOL_MARKETS.get(symbol, "HOSE") for symbol in config.WATCHLIST}
    phases = {session_phase(now, market) for market in markets}
    market_open = any(database.is_session_open(now.date().isoformat(), market) for market in markets)
    expected_stream = market_open and bool(phases.intersection({"CONTINUOUS_AM", "CONTINUOUS_PM"}))
    if source == "live":
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
        "market_phase": sorted(phases)[0] if len(phases) == 1 else sorted(phases),
        "market_session_expected": expected_stream,
    })
    if source == "demo":
        state["connection_status"] = "demo"
    elif is_stale and state["connection_status"] == "connected":
        state["connection_status"] = "stale"
    database.update_stale_alert(source, is_stale, config.STALE_AFTER_SECONDS, latest)
    return state


@app.get("/api/symbols")
def symbols():
    source = _source()
    available = database.list_symbols(source)
    if source == "live" and not available:
        available = config.WATCHLIST
    return {"source": source, "symbols": available}


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
    profile = profiles.get(ticker, {"name": ticker, "sector": "Chưa xác định", "description": "Tên doanh nghiệp chưa có trong danh mục cục bộ; có thể bổ sung từ stockDetail khi NEO LIVE được cấp quyền mạng."})
    return {"symbol": ticker, **profile}


@app.get("/api/rankings")
def rankings(limit: int = Query(default=500, ge=1, le=2000), start_at: str | None = None,
              end_at: str | None = None, phase: str | None = Query(default=None, pattern="^(OPEN|MID|CLOSE)$")):
    source = _source()
    results = []
    for symbol in database.list_symbols(source):
        bars_for_symbol = database.fetch_bars(source, symbol=symbol, limit=limit,
                                              start_at=start_at, end_at=end_at, bucket=phase)
        if source == "live" and bars_for_symbol:
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
    minimum_samples = config.RANKING_MIN_BARS if source == "live" else config.RANKING_MIN_SNAPSHOTS
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
        if selected_source == "live" and bars_data:
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
            "depth_median": depth_mid, "minimum_samples": config.RANKING_MIN_BARS if selected_source == "live" else config.RANKING_MIN_SNAPSHOTS,
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
        "source": "NEO API market stream, channel stockInfo",
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
            {"metric": "Quoted spread", "meaning": "Chi phí tức thời quan sát được giữa giá mua tốt nhất và giá bán tốt nhất. Spread thấp thường thuận lợi hơn cho giao dịch nhỏ tại thời điểm snapshot.", "caveat": "Không tính phí, trượt giá, kích thước lệnh hay khả năng khớp; phải so sánh theo thời gian và giữa các mã cùng bối cảnh."},
            {"metric": "Depth top 3", "meaning": "Giá trị chào mua/chào bán đang hiển thị trong ba mức giá tốt nhất. Depth lớn biểu thị nhiều lượng niêm yết hơn gần giá hiện tại.", "caveat": "Lệnh có thể bị hủy; đây không phải thanh khoản thực hiện được hay toàn bộ sổ lệnh."},
            {"metric": "Queue imbalance", "meaning": "Chuẩn hóa chênh lệch khối lượng hiển thị hai phía về khoảng [-1, 1]. Dương nghĩa là khối lượng bid lớn hơn ask trong các mức hợp lệ.", "caveat": "Mô tả trạng thái sổ lệnh, không phải order-flow imbalance (OFI), không chứng minh chiều giá tiếp theo."},
            {"metric": "Cảnh báo percentile", "meaning": "So spread hiện tại với P90 lịch sử hoặc depth với P10 lịch sử của chính mã sau tối thiểu số mẫu cấu hình.", "caveat": "Ngưỡng là quy tắc giám sát nội bộ; kết quả phụ thuộc cửa sổ, phiên, chất lượng dữ liệu và không phải chuẩn đầu tư."}
        ],
        "theoretical_framework": [
            {"title": "Spread như chi phí giao dịch tức thời", "body": "Bid–ask spread biểu thị khoảng cách giữa mức giá mua tốt nhất và bán tốt nhất đang được niêm yết. Theo vi cấu trúc thị trường, spread bù đắp một phần chi phí xử lý lệnh, nắm giữ tồn kho và rủi ro giao dịch với đối tác có thông tin tốt hơn. SLI tính quoted spread theo điểm cơ bản so với mid-price để giảm lệ thuộc vào thang giá tuyệt đối. Đây là chỉ báo từ báo giá, không phải effective spread từ giao dịch đã khớp."},
            {"title": "Depth và khả năng hấp thụ lệnh", "body": "Depth top 3 cộng lượng niêm yết ở ba mức giá đầu tiên cho mỗi phía; depth value nhân mỗi mức giá với khối lượng tương ứng. Nhiều lượng hiển thị gần giá có thể cho thấy vùng đệm niêm yết dày hơn, nhưng không chứng minh thị trường hấp thụ được một lệnh cụ thể: lệnh có thể bị rút, thứ tự ưu tiên và tốc độ cập nhật có ý nghĩa, còn SLI không mô phỏng khớp lệnh."},
            {"title": "Queue imbalance là ảnh chụp trạng thái sổ lệnh", "body": "Queue imbalance chuẩn hóa chênh lệch tổng khối lượng bid và ask. Chỉ số gần +1 cho biết khối lượng bid hiển thị trội; gần −1 cho biết ask trội; gần 0 cho biết tương đối cân bằng. Nghiên cứu order-flow imbalance thường xét biến động của sổ lệnh qua sự kiện thêm, hủy và khớp lệnh. Vì SLI hiện tính từ snapshot, không được gọi chỉ số này là OFI hoặc dùng như dự báo giá."},
            {"title": "Baseline riêng từng mã và cảnh báo percentile", "body": "Ngưỡng được tính từ phân phối lịch sử của chính mã: spread cao hơn P90 hoặc depth thấp hơn P10 khi đủ tối thiểu số mẫu cấu hình. Cách so sánh theo mã giúp hạn chế việc áp một thang giá trị tuyệt đối cho các cổ phiếu khác nhau. Percentile nhạy với cửa sổ, phiên giao dịch, ngoại lệ và chế độ thị trường; cảnh báo cần được xem cùng số mẫu và trạng thái nguồn."},
            {"title": "Tính nhất quán và dữ liệu thiếu", "body": "Mỗi snapshot được lưu cùng payload gốc, thời điểm nhận cục bộ, mã nguồn và trạng thái kiểm tra; chỉ tiêu được tính có thể tái lập từ các trường bid/ask hợp lệ. Trường thiếu được để null, không tự thay bằng 0. Nếu payload không có timestamp máy chủ thì thời điểm nhận không thể thay thế chính xác thời điểm tạo dữ liệu."}
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
    started = collector.start()
    return {
        "ok": True,
        "message": "Đã yêu cầu kết nối NEO." if started else "Collector đang chạy.",
        "status": runtime.snapshot(),
    }


@app.post("/api/collector/stop")
def stop_collector():
    collector.stop()
    return {"ok": True, "status": runtime.snapshot()}


@app.post("/api/collector/demo")
def use_demo():
    collector.stop()
    runtime.update(mode="demo", connection_status="demo", last_error=None)
    _start_demo_loop()
    return {"ok": True, "status": runtime.snapshot()}
