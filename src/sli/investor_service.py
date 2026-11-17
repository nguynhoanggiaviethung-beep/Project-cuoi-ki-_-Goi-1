"""Local investor watchlist read model built only from persisted SSI data."""
from __future__ import annotations

from datetime import datetime, timedelta

from . import config, database
from .detail_service import build_detail
from .bookwalk import estimate as estimate_bookwalk
from statistics import median


def _widget(detail, name):
    widget=(detail.get("widgets") or {}).get(name) or {}
    return widget.get("data") if widget.get("status")=="ready" else None


def _condition(kpis, settings):
    spread=kpis.get("spread_percentile")
    depth=kpis.get("depth_percentile")
    spread_count=int(kpis.get("spread_baseline_count") or 0)
    depth_count=int(kpis.get("depth_baseline_count") or 0)
    session_count=int(kpis.get("baseline_session_count") or 0)
    baseline_summary=(f"Mẫu nền: {session_count}/{config.BASELINE_SESSIONS} phiên · spread {spread_count} bar · độ sâu {depth_count} bar")
    if spread is None or depth is None:
        return {"label":"Chưa đủ dữ liệu","key":"insufficient","baseline_summary":baseline_summary,
                "explanation":f"Cần tối thiểu {config.BASELINE_SESSIONS} phiên lịch sử hợp lệ cùng mã/sàn/pha để ước lượng các phân vị. Hiện có {session_count}/{config.BASELINE_SESSIONS} phiên, {spread_count} bar spread và {depth_count} bar độ sâu. Bar realtime không thay thế baseline lịch sử."}
    if spread>=config.STRESS_SPREAD_PERCENTILE or depth<=config.STRESS_DEPTH_PERCENTILE:
        return {"label":"Căng thẳng","key":"stress","baseline_summary":baseline_summary,
                "explanation":f"Spread ở P{spread:.0f} (ngưỡng căng thẳng P90) hoặc độ sâu ở P{depth:.0f} (ngưỡng P10), so với lịch sử cùng pha của mã. {baseline_summary}."}
    if spread<=config.FAVORABLE_SPREAD_PERCENTILE and depth>=config.FAVORABLE_DEPTH_PERCENTILE:
        return {"label":"Thuận lợi","key":"favorable","baseline_summary":baseline_summary,
                "explanation":f"Spread ở P{spread:.0f} (≤P25) và độ sâu ở P{depth:.0f} (≥P75), so với lịch sử cùng pha của mã. {baseline_summary}."}
    return {"label":"Cần lưu ý","key":"caution","baseline_summary":baseline_summary,
            "explanation":f"Spread phân vị {spread:.0f}%, độ sâu phân vị {depth:.0f}%; chưa đồng thời nằm trong ngưỡng thuận lợi hoặc căng thẳng. {baseline_summary}."}

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
    bookwalk_sizes=(50_000_000,100_000_000,200_000_000,500_000_000)
    for item in items:
        symbol=str(item["symbol"]).upper()
        detail=build_detail(symbol,source="ssi",as_of=clock_time,window_sessions=1)
        company=_widget(detail,"company") or {}
        kpis=_widget(detail,"kpis") or {}
        charts=_widget(detail,"charts") or {}
        history=_widget(detail,"history") or {}
        latest=history.get("latest") or {}
        prior=history.get("prior") or {}
        book_widget=(detail.get("widgets") or {}).get("orderbook") or {}
        snapshot=book_widget.get("data") if book_widget.get("status")=="ready" else None
        simulations={}
        if snapshot and snapshot.get("validation_status")=="valid":
            observation={key:snapshot.get(key) for key in (
                "bid_price_1","bid_volume_1","bid_price_2","bid_volume_2","bid_price_3","bid_volume_3",
                "ask_price_1","ask_volume_1","ask_price_2","ask_volume_2","ask_price_3","ask_volume_3","market")}
            for amount in bookwalk_sizes:
                simulations[str(amount)]=estimate_bookwalk(observation,"buy",notional=amount)
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
            "spread_percentile":kpis.get("spread_percentile"),"depth_percentile":kpis.get("depth_percentile"),
            "baseline_session_count":kpis.get("baseline_session_count"),
            "bookwalk":simulations,"bookwalk_snapshot_at":((snapshot or {}).get("source_ts_vn") or (snapshot or {}).get("received_at_vn") or (snapshot or {}).get("received_at_utc")),
            "spread_history":[{"minute":r.get("minute"),"spread_bps":r.get("spread_bps")} for r in (charts.get("spread") or [])[-60:]],
            "quote_time":detail.get("quote_time"),"as_of":clock_time.isoformat(timespec="seconds"),
            "data_state":detail.get("state"),"baseline_eligible":detail.get("baseline_eligible",False),
        })
    # Cross-sectional comparison uses the same latest saved 60-minute window for all SSI stocks.
    # If there are no saved bars, comparisons remain null instead of being inferred from stale prices.
    market_values={"spread":[],"depth":[]}
    market_as_of=database.latest_bar_minute("ssi")
    if market_as_of:
        try:
            latest_minute=datetime.fromisoformat(str(market_as_of).replace("Z","+00:00"))
            if latest_minute.tzinfo is None: latest_minute=latest_minute.replace(tzinfo=config.TIMEZONE)
            latest_minute=latest_minute.astimezone(config.TIMEZONE)
            grouped=database.fetch_bars_window_by_symbol("ssi",(latest_minute-timedelta(minutes=60)).isoformat(timespec="minutes"),
                (latest_minute+timedelta(minutes=1)).isoformat(timespec="minutes"),60)
            stock_symbols=database.list_stock_symbols("ssi")
            for points in grouped.values():
                ticker=points[0].get("symbol") if points else None
                if not ticker or ticker not in stock_symbols:
                    continue
                values=[point for point in points if point.get("coverage_status")=="ok" and point.get("session_phase") in {"CONTINUOUS_AM","CONTINUOUS_PM"}]
                spreads=[float(point["spread_bps"]) for point in values if point.get("spread_bps") is not None and float(point["spread_bps"])>0]
                depths=[float(point["total_depth_value"]) for point in values if point.get("total_depth_value") is not None and float(point["total_depth_value"])>0]
                if spreads: market_values["spread"].append(median(spreads))
                if depths: market_values["depth"].append(median(depths))
        except (TypeError,ValueError):
            market_as_of=None
    for row in rows:
        spread,depth=row.get("spread_bps"),row.get("depth_top3_value")
        row["market_spread_percentile"]=(sum(value<=float(spread) for value in market_values["spread"])/len(market_values["spread"])*100
                                          if spread is not None and market_values["spread"] else None)
        row["market_depth_percentile"]=(sum(value<=float(depth) for value in market_values["depth"])/len(market_values["depth"])*100
                                          if depth is not None and market_values["depth"] else None)
    valid_spreads=[float(row["spread_bps"]) for row in rows if row.get("spread_bps") is not None]
    conditions={key:sum(1 for row in rows if row.get("condition",{}).get("key")==key) for key in ("favorable","caution","stress")}
    eligible=[row for row in rows if row.get("spread_percentile") is not None and row.get("depth_percentile") is not None]
    worst=max(eligible,key=lambda row:max(float(row["spread_percentile"]),100-float(row["depth_percentile"])),default=None)
    alert_symbols=sum(1 for row in rows if int(row.get("active_alerts") or 0)>0)
    return {"watchlist_id":watchlist["id"],"watchlist_name":watchlist["name"],"source":"ssi",
            "market_clock":market_meta,"as_of":clock_time.isoformat(timespec="seconds"),
            "items":rows,"count":len(rows),
            "analysis":{"condition_counts":conditions,"portfolio_spread_median":median(valid_spreads) if valid_spreads else None,
                "market_spread_median":median(market_values["spread"]) if market_values["spread"] else None,
                "market_sample_count":len(market_values["spread"]),"market_as_of":market_as_of,
                "insufficient_count":sum(1 for row in rows if row.get("condition",{}).get("key")=="insufficient"),
                "worst_symbol":worst.get("symbol") if worst else None,
                "alert_count":sum(int(row.get("active_alerts") or 0) for row in rows),"alert_symbols":alert_symbols,
                "bookwalk_sizes":list(bookwalk_sizes)},
            "note":"Giá lấy từ OHLC SSI gần nhất; spread/độ sâu chỉ có khi có quote SSI đã lưu. Mốc tham chiếu và nhãn điều kiện dùng dữ liệu của chính mã; thiếu lịch sử được ghi rõ."}
