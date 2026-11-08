"""Local investor watchlist read model built only from persisted SSI data."""
from __future__ import annotations

from datetime import datetime

from . import config, database
from .detail_service import build_detail


def _widget(detail, name):
    widget=(detail.get("widgets") or {}).get(name) or {}
    return widget.get("data") if widget.get("status")=="ready" else None


def _condition(kpis, settings):
    spread=kpis.get("spread_percentile")
    depth=kpis.get("depth_percentile")
    if spread is None or depth is None:
        return {"label":"Chưa đủ dữ liệu","key":"insufficient","explanation":"Cần đủ bar lịch sử của chính mã để so sánh spread và độ sâu với mốc tham chiếu."}
    if spread>=float(settings["stress_spread_pctl"]) or depth<=float(settings["stress_depth_pctl"]):
        return {"label":"Căng thẳng","key":"stress","explanation":f"Spread ở phân vị {spread:.0f}% hoặc độ sâu ở phân vị {depth:.0f}%, vượt ngưỡng người dùng đặt."}
    if spread<=float(settings["favorable_spread_pctl"]) and depth>=float(settings["favorable_depth_pctl"]):
        return {"label":"Thuận lợi","key":"favorable","explanation":f"Spread ở phân vị {spread:.0f}% và độ sâu ở phân vị {depth:.0f}% so với lịch sử của mã."}
    return {"label":"Cần lưu ý","key":"caution","explanation":f"Spread phân vị {spread:.0f}%, độ sâu phân vị {depth:.0f}%; chưa đồng thời nằm trong ngưỡng thuận lợi hoặc căng thẳng."}


def build_watchlist_live(watchlist, market_meta, as_of: datetime | None = None):
    """Build every row with one page clock; no quote or financial values are synthesized."""
    clock_time=as_of or datetime.now(config.TIMEZONE)
    if clock_time.tzinfo is None:
        clock_time=clock_time.replace(tzinfo=config.TIMEZONE)
    clock_time=clock_time.astimezone(config.TIMEZONE)
    settings=database.get_local_settings()
    items=watchlist.get("items") or []
    symbols=[str(item["symbol"]).upper() for item in items]
    alerts_by_symbol=database.count_active_alerts_by_symbol("ssi",symbols)
    rows=[]
    for item in items:
        symbol=str(item["symbol"]).upper()
        detail=build_detail(symbol,source="ssi",as_of=clock_time,window_sessions=20)
        company=_widget(detail,"company") or {}
        kpis=_widget(detail,"kpis") or {}
        charts=_widget(detail,"charts") or {}
        history=_widget(detail,"history") or {}
        latest=history.get("latest") or {}
        prior=history.get("prior") or {}
        close=latest.get("close_price")
        previous=prior.get("close_price")
        change_pct=((float(close)-float(previous))*100/float(previous)
                    if close is not None and previous not in (None,0) else None)
        rows.append({
            "symbol":symbol,"name":company.get("name"),"exchange":company.get("board"),
            "note":item.get("note","")[:200],"position":item.get("position",0),
            "price":close,"price_date":latest.get("trading_date"),"change_pct":change_pct,
            "spread_bps":kpis.get("spread_bps"),"spread_ticks":kpis.get("spread_ticks"),
            "bid_depth_value":kpis.get("bid_depth_value"),"ask_depth_value":kpis.get("ask_depth_value"),
            "depth_top3_value":(float(kpis["bid_depth_value"] or 0)+float(kpis["ask_depth_value"] or 0)
                                 if kpis.get("bid_depth_value") is not None or kpis.get("ask_depth_value") is not None else None),
            "condition":_condition(kpis,settings),"active_alerts":alerts_by_symbol.get(symbol,0),
            "spread_history":[{"minute":r.get("minute"),"spread_bps":r.get("spread_bps")} for r in (charts.get("spread") or [])[-60:]],
            "quote_time":detail.get("quote_time"),"as_of":clock_time.isoformat(timespec="seconds"),
            "data_state":detail.get("state"),"baseline_eligible":detail.get("baseline_eligible",False),
        })
    return {"watchlist_id":watchlist["id"],"watchlist_name":watchlist["name"],"source":"ssi",
            "market_clock":market_meta,"as_of":clock_time.isoformat(timespec="seconds"),
            "items":rows,"count":len(rows),
            "note":"Giá lấy từ OHLC SSI gần nhất; spread/độ sâu chỉ có khi có quote SSI đã lưu. Mốc tham chiếu và nhãn điều kiện dùng dữ liệu của chính mã; thiếu lịch sử được ghi rõ."}
