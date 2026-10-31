from datetime import datetime, timezone
from statistics import median
import threading
import time

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, database
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
    if source == "live":
        if latest:
            try:
                stamp = datetime.fromisoformat(latest.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                is_stale = (
                    datetime.now(timezone.utc) - stamp
                ).total_seconds() > config.STALE_AFTER_SECONDS
            except ValueError:
                is_stale = True
        else:
            is_stale = True
    state.update({
        "source": source,
        "last_data_at": latest,
        "is_stale": is_stale,
        "stale_after_seconds": config.STALE_AFTER_SECONDS,
        "configured_watchlist": config.WATCHLIST,
    })
    if source == "demo":
        state["connection_status"] = "demo"
    elif is_stale and state["connection_status"] == "connected":
        state["connection_status"] = "stale"
    return state


@app.get("/api/symbols")
def symbols():
    source = _source()
    available = database.list_symbols(source)
    if source == "live" and not available:
        available = config.WATCHLIST
    return {"source": source, "symbols": available}


@app.get("/api/overview")
def overview(symbol: str | None = None, limit: int = Query(default=240, ge=1, le=2000)):
    source = _source()
    available = database.list_symbols(source)
    selected = symbol.upper() if symbol and symbol.upper() not in ("__ALL__", "ALL") else None
    groups = {}
    for ticker in ([selected] if selected else available):
        rows = list(reversed(database.fetch_observations(source, symbol=ticker, limit=limit)))
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
def symbol_detail(symbol: str, limit: int = Query(default=300, ge=1, le=2000)):
    rows = list(reversed(database.fetch_observations(_source(), symbol=symbol, limit=limit)))
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
def rankings(limit: int = Query(default=500, ge=1, le=2000)):
    source = _source()
    results = []
    for symbol in database.list_symbols(source):
        rows = database.fetch_observations(source, symbol=symbol, limit=limit)
        valid = [row for row in rows if row["validation_status"] == "valid"]
        if not valid:
            continue
        spreads = _numeric([row["spread_bps"] for row in valid])
        depths = _numeric([row["total_depth_value"] for row in valid])
        results.append({
            "symbol": symbol,
            "snapshot_count": len(rows),
            "median_spread_bps": median(spreads) if spreads else None,
            "median_total_depth_value": median(depths) if depths else None,
            "latest_spread_bps": valid[0]["spread_bps"],
            "latest_total_depth_value": valid[0]["total_depth_value"],
        })
    return {
        "source": source,
        "sample_window": limit,
        "lowest_spread": sorted(
            results,
            key=lambda row: row["median_spread_bps"] if row["median_spread_bps"] is not None else float("inf"),
        ),
        "highest_depth": sorted(
            results,
            key=lambda row: row["median_total_depth_value"] if row["median_total_depth_value"] is not None else float("-inf"),
            reverse=True,
        ),
        "symbols": results,
    }


@app.get("/api/alerts")
def alerts(limit: int = Query(default=100, ge=1, le=500)):
    return {
        "source": _source(),
        "baseline_min_observations": config.BASELINE_MIN_OBSERVATIONS,
        "alerts": database.fetch_alerts(_source(), limit),
    }


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
            "baseline_min_observations": config.BASELINE_MIN_OBSERVATIONS,
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