"""Local investor watchlist read model built only from persisted SSI data."""
from __future__ import annotations
from datetime import datetime, timedelta
from statistics import median
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from . import config, database
from .analytics import percentile_rank, quantile
from .detail_service import build_detail
from .bookwalk import book_walk_by_value, max_order_within_threshold
from .market_rules import tick_size


def _widget(detail,name):
    widget=(detail.get("widgets") or {}).get(name) or {}
    return widget.get("data") if widget.get("status")=="ready" else None


def fmt_tick(value):
    return "không rõ bước giá" if value is None else f"{int(value):,}đ".replace(",", ".")


def _session_label(value):
    parts=str(value or "").replace("/","-").split("-")
    return f"Phiên {parts[2]}/{parts[1]}/{parts[0]}" if len(parts)==3 else "Phiên chưa xác định"


def _select_peer_group(current, market_rows):
    if not current:return [],"chưa có nhóm so sánh"
    other=[item for item in market_rows if item["symbol"]!=current["symbol"]]
    same_band=([item for item in other if item["board"]==current["board"] and item["tick"]==current["tick"]]
               if current.get("tick") is not None else [])
    same_board=[item for item in other if item["board"]==current["board"]]
    if len(same_band)>=config.PEER_MIN_SIZE:
        return same_band,f"{current['board']} · cùng nhóm bước giá {fmt_tick(current['tick'])}"
    if len(same_board)>=config.PEER_MIN_SIZE:
        return same_board,f"{current['board']} · mọi nhóm bước giá"
    return other,"toàn thị trường · mọi sàn"


def _percentile_rank(values, value):
    return percentile_rank(values,value)


def _market_level(current, peers, peer_basis, tick):
    """Compare a stock with peers using the same valid N3 bar window."""
    if not current or not peers:
        return {"level":None,"peer_basis":peer_basis,"n_peers":len(peers),"spread_pctile":None,
                "depth_pctile":None,"spread_ticks":current.get("spread_ticks") if current else None,
                "tick_size":tick,"median_spread_bps":None,"median_depth_value":None}
    spreads=[float(item["spread"]) for item in peers if item.get("spread") is not None]
    depths=[float(item["depth"]) for item in peers if item.get("depth") is not None]
    spread=float(current["spread"]);depth=float(current["depth"])
    med_spread=median(spreads) if spreads else None;med_depth=median(depths) if depths else None
    if med_spread is None or med_depth is None:level=None
    elif spread<med_spread and depth>med_depth:level="Cao"
    elif spread>med_spread and depth<med_depth:level="Thấp"
    else:level="Trung bình"
    return {"level":level,"peer_basis":peer_basis,"n_peers":len(peers),
            "spread_pctile":_percentile_rank(spreads,spread),"depth_pctile":_percentile_rank(depths,depth),
            "spread_ticks":current.get("spread_ticks"),"tick_size":tick,
            "median_spread_bps":med_spread,"median_depth_value":med_depth}


def _own_baseline_state(alerts, baseline, quote_stale=False, in_session=False):
    if quote_stale and in_session:
        return {"state":"quote_stale","baseline_mode":baseline.get("baseline_mode","insufficient"),
                "sessions_have":baseline.get("sessions_have",0),"sessions_need":config.MIN_BASELINE_SESSIONS,
                "bars_have":baseline.get("bars_have",0),"bars_need":config.BASELINE_MIN_BARS,
                "label":"Quote cũ","basis":"quote_stale"}
    if not baseline.get("has_snapshot"):
        return {"state":"insufficient","baseline_mode":baseline.get("baseline_mode","insufficient"),
                "sessions_have":baseline.get("sessions_have",0),"sessions_need":config.MIN_BASELINE_SESSIONS,
                "bars_have":baseline.get("bars_have",0),"bars_need":config.BASELINE_MIN_BARS,
                "label":"Chưa có snapshot","basis":"chưa có spread/độ sâu SSI hợp lệ"}
    if "combined_liquidity_stress" in alerts:
        state,label,basis="stress","Căng thẳng","A4 đang hoạt động"
    elif alerts.intersection({"spread_widening","depth_drop"}):
        state,label,basis="caution","Cần lưu ý","A1/A2 đang hoạt động"
    elif not baseline.get("eligible"):
        return {"state":"insufficient","baseline_mode":baseline.get("baseline_mode","insufficient"),
                "sessions_have":baseline.get("sessions_have",0),"sessions_need":config.MIN_BASELINE_SESSIONS,
                "bars_have":baseline.get("bars_have",0),"bars_need":config.BASELINE_MIN_BARS,
                "label":"Chưa đủ lịch sử","basis":"baseline riêng của mã"}
    else:
        state,label,basis="normal","Bình thường","không có A1/A2/A4 đang hoạt động"
    return {"state":state,"baseline_mode":baseline.get("baseline_mode"),
            "sessions_have":baseline.get("sessions_have",0),"sessions_need":config.MIN_BASELINE_SESSIONS,
            "bars_have":baseline.get("bars_have",0),"bars_need":config.BASELINE_MIN_BARS,
            "label":label,"basis":basis}


def _levels(snapshot,side):
    prefix="ask" if side=="buy" else "bid"
    return [(snapshot.get(f"{prefix}_price_{i}"),snapshot.get(f"{prefix}_volume_{i}")) for i in range(1,config.ORDER_COST_LEVELS+1)]


def _simulate(snapshot,side,amount_million,mid,tolerance_bps):
    walk=book_walk_by_value(_levels(snapshot,side),int(amount_million)*1_000_000,mid,side,lot=100)
    maximum=max_order_within_threshold(_levels(snapshot,side),mid,side,tolerance_bps,lot=100)
    walk["max_order"]={"status":"ready" if maximum.get("visible_depth_value",0)>0 else "unavailable","value":maximum["value"],"value_million":maximum["value"]/1_000_000,"shares":maximum["shares"],"full_depth":maximum["full_depth"],"zero":maximum["zero"],"levels_used":maximum["levels_used"]}
    return walk


@lru_cache(maxsize=4)
def _cached_market_cross_section(market_as_of):
    """Cache the shared SSI market window until a newer saved bar minute exists."""
    market_rows=[];market_by_symbol={}
    if not market_as_of:return market_rows,market_by_symbol
    try:
        latest_minute=datetime.fromisoformat(str(market_as_of).replace("Z","+00:00"))
        if latest_minute.tzinfo is None:latest_minute=latest_minute.replace(tzinfo=config.TIMEZONE)
        latest_minute=latest_minute.astimezone(config.TIMEZONE)
        latest_phase="CONTINUOUS_AM" if latest_minute.hour<12 else "CONTINUOUS_PM"
        grouped=database.fetch_bars_window_by_symbol("ssi",(latest_minute-timedelta(minutes=59)).isoformat(timespec="minutes"),(latest_minute+timedelta(minutes=1)).isoformat(timespec="minutes"),60)
        stock_symbols=set(database.list_stock_symbols("ssi"));metadata_by_symbol=database.fetch_security_metadata("ssi")
        for points in grouped.values():
            ticker=points[0].get("symbol") if points else None
            if ticker not in stock_symbols:continue
            values=[p for p in points if p.get("coverage_status")=="ok" and p.get("session_phase")==latest_phase
                    and p.get("spread_bps") is not None and p.get("total_depth_value") is not None]
            if values:
                metadata=metadata_by_symbol.get(ticker,{})
                board=str(metadata.get("board") or "").upper();price=metadata.get("latest_close")
                tick=tick_size(float(price),board) if price is not None else None
                ticks=[float(p["spread_ticks"]) for p in values if p.get("spread_ticks") is not None]
                current={"spread":median([float(p["spread_bps"]) for p in values]),
                         "depth":median([float(p["total_depth_value"]) for p in values]),
                         "spread_ticks":median(ticks) if ticks else None,"board":board,"tick":tick,"symbol":ticker}
                market_rows.append(current);market_by_symbol[ticker]=current
    except (TypeError,ValueError):
        return None,{}
    return market_rows,market_by_symbol


def build_watchlist_live(watchlist,market_meta,as_of:datetime|None=None,order_value_million=None,tolerance_bps=None,side=None):
    """Build watchlist analysis from SSI quotes and bars on one shared clock."""
    clock_time=as_of or datetime.now(config.TIMEZONE)
    if clock_time.tzinfo is None:clock_time=clock_time.replace(tzinfo=config.TIMEZONE)
    clock_time=clock_time.astimezone(config.TIMEZONE)
    settings=database.get_local_settings()
    amount=int(order_value_million if order_value_million is not None else settings.get("order_value_million",100))
    tolerance=int(tolerance_bps if tolerance_bps is not None else settings.get("tolerance_bps",30))
    selected_side=str(side if side is not None else settings.get("order_side","buy")).lower()
    items=watchlist.get("items") or [];symbols=[str(i["symbol"]).upper() for i in items]
    alert_counts=database.count_active_alerts_by_symbol("ssi",symbols);alert_types=database.fetch_active_alert_types_by_symbol("ssi",symbols)
    date_now=clock_time.date().isoformat();details=[]
    in_session=market_meta.get("state")=="IN_SESSION"
    def build_item_detail(item):
        symbol=str(item["symbol"]).upper()
        return item,build_detail(symbol,source="ssi",as_of=clock_time,window_sessions=1,watchlist_mode=True)
    # Each security detail uses independent read-only SQLite connections. Run
    # a few at once so a cold page load does not add the seven disk-read times.
    with ThreadPoolExecutor(max_workers=min(4,len(items)) or 1) as pool:
        item_details=list(pool.map(build_item_detail,items))
    for item,detail in item_details:
        symbol=str(item["symbol"]).upper()
        company=_widget(detail,"company") or {};kpis=_widget(detail,"kpis") or {};charts=_widget(detail,"charts") or {};history=_widget(detail,"history") or {}
        latest=history.get("latest") or {};prior=history.get("prior") or {};order_widget=(detail.get("widgets") or {}).get("orderbook") or {}
        snapshot=order_widget.get("data") if order_widget.get("status")=="ready" else None
        valid=bool(snapshot and snapshot.get("validation_status")=="valid")
        age=detail.get("quote_age_seconds");state=detail.get("state")
        quote_stale=state=="stale" or (age is not None and float(age)>config.STALE_AFTER_SECONDS and state in {"quote","one_sided"})
        close=latest.get("close_price");previous=prior.get("close_price")
        change_pct=((float(close)-float(previous))*100/float(previous) if close is not None and previous not in (None,0) else None)
        depth_top3=(float(kpis["bid_depth_value"] or 0)+float(kpis["ask_depth_value"] or 0) if kpis.get("bid_depth_value") is not None or kpis.get("ask_depth_value") is not None else None)
        baseline={"eligible":bool(detail.get("baseline_eligible")),"has_snapshot":valid and kpis.get("spread_bps") is not None and depth_top3 is not None,
                  "baseline_mode":detail.get("baseline_mode","insufficient"),
                  "sessions_have":int(detail.get("baseline_sessions_have") or 0),"bars_have":int(detail.get("baseline_bars_have") or 0)}
        row={"symbol":symbol,"name":company.get("name"),"exchange":company.get("board"),"note":item.get("note","")[:200],"position":item.get("position",0),
             "price":close,"price_date":latest.get("trading_date"),"change_pct":change_pct,"spread_bps":kpis.get("spread_bps"),"spread_ticks":kpis.get("spread_ticks"),
             "bid_depth_value":kpis.get("bid_depth_value"),"ask_depth_value":kpis.get("ask_depth_value"),"depth_top3_value":depth_top3,
             "active_alerts":alert_counts.get(symbol,0),"alert_types":sorted(alert_types.get(symbol,set())),"spread_percentile":kpis.get("spread_percentile"),
              "depth_percentile":kpis.get("depth_percentile"),"baseline_session_count":kpis.get("baseline_session_count"),"spread_baseline_count":kpis.get("spread_baseline_count"),"depth_baseline_count":kpis.get("depth_baseline_count"),"baseline_eligible":detail.get("baseline_eligible",False),
              "own_state":_own_baseline_state(alert_types.get(symbol,set()),baseline,quote_stale,in_session),
              "window_label":_session_label((charts.get("session_dates") or [date_now])[-1]),
             "bookwalk_snapshot_at":((snapshot or {}).get("source_ts_vn") or (snapshot or {}).get("received_at_vn") or (snapshot or {}).get("received_at_utc")),
             "quote_time":detail.get("quote_time"),"quote_age_s":age,"quote_stale":quote_stale,"as_of":clock_time.isoformat(timespec="seconds"),"data_state":state,
             "spread_history":[{"minute":r.get("minute"),"spread_bps":r.get("spread_bps")} for r in (charts.get("spread") or [])[-60:]]}
        row["snapshot_ts"]=row["bookwalk_snapshot_at"]
        row["bookwalk"]={};row["order_cost"]={"status":"unavailable","reason":"Chưa có quote SSI hợp lệ."};row["max_order"]={"status":"unavailable","value_million":None,"shares":0,"full_depth":False,"zero":False}
        if valid and not quote_stale:
            mid=(float(snapshot["bid_price_1"])+float(snapshot["ask_price_1"]))/2 if snapshot.get("bid_price_1") and snapshot.get("ask_price_1") else None
            if mid:
                sizes=sorted(set([50,100,200,500,amount]))
                for size in sizes:row["bookwalk"][str(size*1_000_000)]=_simulate(snapshot,selected_side,size,mid,tolerance)
                row["order_cost"]=row["bookwalk"][str(amount*1_000_000)]
                row["max_order"]={k:row["order_cost"].get("max_order",{}).get(k) for k in ("status","value_million","shares","full_depth","zero","levels_used")}
                row["order_cost"].update({"requested_value_vnd":amount*1_000_000,"tolerance_bps":tolerance,"side":selected_side})
        elif quote_stale:
            row["order_cost"]={"status":"quote_stale","reason":f"Quote đã cũ {fmt_age(age)}; không mô phỏng như dữ liệu mới."}
        details.append((row,latest))
    # Build a common N3 cross-section from latest saved SSI minute bars.
    market_as_of=database.latest_bar_minute("ssi")
    market_rows,market_by_symbol=_cached_market_cross_section(market_as_of)
    if market_rows is None:market_as_of=None;market_rows=[]
    market_values={"spread":[row["spread"] for row in market_rows],"depth":[row["depth"] for row in market_rows]}
    market_spread_p75=quantile(market_values["spread"],75)
    market_depth_p25=quantile(market_values["depth"],25)
    activities=database.fetch_watchlist_daily_activity(symbols,clock_time,source="ssi",in_session=in_session)
    for row,_ in details:
        types=set(row["alert_types"])
        row["daily_activity"]={**activities.get(row["symbol"],{"ratio":None,"avg20_value":None,"today_value":None,"basis":"history_unavailable","sessions_used":0}),"window_label":row["window_label"]}
        current=market_by_symbol.get(row["symbol"])
        peers,peer_basis=_select_peer_group(current,market_rows)
        row["market_level"]=_market_level(current,peers,peer_basis,current.get("tick") if current else None)
        own=row["own_state"]
        row["condition"]={"label":own["label"],"key":own["state"],"label_basis":own["basis"],
                          "baseline_summary":f"Lịch sử sổ lệnh: {own['sessions_have']} phiên · {own['bars_have']} bar (cần ≥ {own['sessions_need']} phiên và ≥ {own['bars_need']} bar)",
                          "explanation":f"{own['basis']} · {row['window_label']}"}
        row["label"]=own["label"];row["label_basis"]=own["basis"]
        spread=row.get("spread_bps");depth=row.get("depth_top3_value")
        n=len(market_rows)
        position={"n_symbols":n,"spread_better_pct":(sum(v>float(spread) for v in market_values["spread"])/n*100 if spread is not None and n else None),
                  "depth_better_pct":(sum(v<float(depth) for v in market_values["depth"])/n*100 if depth is not None and n else None),
                  "note":"Spread thấp hơn được xem là tốt hơn; bước giá có thể làm spread bps khác nhau giữa các mã."}
        row["market_position"]=position;row["market_spread_percentile"]=position["spread_better_pct"];row["market_depth_percentile"]=position["depth_better_pct"]
    valid_spreads=[float(row["spread_bps"]) for row,_ in details if row.get("spread_bps") is not None]
    conditions={key:sum(row.get("own_state",{}).get("state")==key for row,_ in details) for key in ("normal","caution","stress")}
    insufficient=sum(row.get("condition",{}).get("key")=="insufficient" for row,_ in details)
    alert_symbols=sum(bool(row.get("active_alerts")) for row,_ in details)
    eligible=[row for row,_ in details if row.get("own_state",{}).get("state") in {"stress","caution"}]
    priority={"stress":0,"caution":1}
    worst=min(eligible,key=lambda row:(priority[row["own_state"]["state"]],-float(row.get("spread_ticks") or 0)),default=None)
    market_clock=dict(market_meta or {})
    return {"watchlist_id":watchlist["id"],"watchlist_name":watchlist["name"],"source":"ssi","market_clock":market_clock,
            "meta":{"as_of":clock_time.isoformat(timespec="seconds"),"market_state":market_clock.get("state"),"session_date":market_clock.get("session_date")},
            "as_of":clock_time.isoformat(timespec="seconds"),"items":[row for row,_ in details],"count":len(details),
             "settings":{"order_value_million":amount,"tolerance_bps":tolerance,"order_side":selected_side},
             "methodology":{"min_baseline_sessions":config.MIN_BASELINE_SESSIONS,"baseline_min_bars":config.BASELINE_MIN_BARS,"peer_min_size":config.PEER_MIN_SIZE},
             "analysis":{"condition_counts":conditions,"insufficient_count":insufficient,"portfolio_spread_median":median(valid_spreads) if valid_spreads else None,
                "market_spread_median":median(market_values["spread"]) if market_values["spread"] else None,"market_sample_count":len(market_values["spread"]),"market_as_of":market_as_of,
                "market_spread_p75":market_spread_p75,"market_depth_p25":market_depth_p25,"worst_symbol":worst.get("symbol") if worst else None,
                "alert_count":sum(row.get("active_alerts",0) for row,_ in details),"alert_symbols":alert_symbols,
                "bookwalk_sizes":[size*1_000_000 for size in sorted(set([50,100,200,500,amount]))],"order_cost_levels":config.ORDER_COST_LEVELS},
            "note":"Chỉ dùng quote và bar SSI đã lưu. Mô phỏng tĩnh, không gửi lệnh; dữ liệu không đủ hoặc quote cũ được ghi riêng."}


def fmt_age(age):
    return "không rõ tuổi" if age is None else f"{int(float(age))} giây"

def build_positions_live(participation_pct=10, as_of:datetime|None=None, market_meta=None):
    """Mark local positions using batched SSI reads and estimate exit horizon from ADV20."""
    from .bookwalk import estimate as estimate_bookwalk
    clock=as_of or datetime.now(config.TIMEZONE)
    if clock.tzinfo is None:clock=clock.replace(tzinfo=config.TIMEZONE)
    clock=clock.astimezone(config.TIMEZONE)
    participation=max(5,min(30,int(participation_pct))) / 100
    in_session=(market_meta or {}).get("state")=="IN_SESSION"
    positions=database.fetch_local_positions()
    symbols=sorted({str(row["symbol"]).upper() for row in positions})
    quotes_by_symbol={};history_by_symbol={symbol:[] for symbol in symbols}
    if symbols:
        placeholders=",".join("?" for _ in symbols)
        with database.connect(timeout=15) as db:
            quotes=db.execute(f"""WITH ranked AS (
                SELECT o.*,m.spread_bps,m.spread_ticks,m.bid_depth_value,m.ask_depth_value,
                  ROW_NUMBER() OVER(PARTITION BY o.symbol ORDER BY COALESCE(o.received_at_vn,o.received_at_utc) DESC,o.id DESC) rn
                FROM observations o LEFT JOIN metrics m ON m.observation_id=o.id
                WHERE o.source='ssi' AND o.validation_status='valid' AND o.symbol IN ({placeholders})
            ) SELECT * FROM ranked WHERE rn=1""",symbols).fetchall()
            daily=db.execute(f"""WITH ranked AS (
                SELECT symbol,replace(trading_date,'/','-') day,close_price,volume,value,
                  ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY replace(trading_date,'/','-') DESC) rn
                FROM historical_prices WHERE source='ssi' AND timeframe='1d'
                  AND replace(trading_date,'/','-'){'<' if in_session else '<='} ?
                  AND symbol IN ({placeholders}) AND volume IS NOT NULL
            ) SELECT * FROM ranked WHERE rn<=20 ORDER BY symbol,day DESC""",[clock.date().isoformat(),*symbols]).fetchall()
        quotes_by_symbol={row["symbol"]:dict(row) for row in quotes}
        for row in daily:history_by_symbol.setdefault(row["symbol"],[]).append(dict(row))
    result=[]
    for position in positions:
        symbol=position["symbol"];quote=quotes_by_symbol.get(symbol);daily=history_by_symbol.get(symbol,[])
        volumes=[float(row["volume"]) for row in daily if row.get("volume") is not None]
        adv20=(sum(volumes)/20) if len(volumes)==20 else None
        last_daily=daily[0] if daily else {}
        quote_price=(quote or {}).get("last_price")
        price=quote_price if quote_price is not None else last_daily.get("close_price")
        price_as_of=(((quote or {}).get("source_ts_vn") or (quote or {}).get("received_at_vn"))
                     if quote_price is not None else last_daily.get("day"))
        quote_time=(quote or {}).get("source_ts_vn") or (quote or {}).get("received_at_vn")
        age=None
        if quote_time:
            try:
                stamp=datetime.fromisoformat(str(quote_time).replace("Z","+00:00"))
                if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=config.TIMEZONE)
                age=max(0,(clock-stamp.astimezone(config.TIMEZONE)).total_seconds())
            except ValueError:pass
        quote_stale=bool(in_session and age is not None and age>config.STALE_AFTER_SECONDS)
        shares=int(position["shares"]);cost=float(position["average_cost"])
        market_value=(shares*float(price) if price is not None else None);cost_value=shares*cost
        sessions_to_exit=(shares/(adv20*participation) if adv20 and adv20>0 else None)
        mid=None
        if quote and quote.get("bid_price_1") and quote.get("ask_price_1"):
            mid=(float(quote["bid_price_1"])+float(quote["ask_price_1"]))/2
        bookwalk={"status":"quote_stale" if quote_stale else "unavailable","reason":"Quote cũ hoặc chưa có snapshot SSI hợp lệ."}
        if quote and not quote_stale and mid and shares>=100 and shares%100==0:
            try:bookwalk=estimate_bookwalk(quote,"sell",quantity=shares,lot_size=100)
            except (TypeError,ValueError) as exc:bookwalk={"status":"unavailable","reason":str(exc)}
        result.append({**position,"price":float(price) if price is not None else None,"price_as_of":price_as_of,
            "market_value":market_value,"cost_value":cost_value,"unrealized_pnl":market_value-cost_value if market_value is not None else None,
            "unrealized_pnl_pct":((float(price)-cost)*100/cost if price is not None and cost>0 else None),
            "adv20_volume":adv20,"adv20_sessions":len(volumes),"participation_pct":participation*100,
            "estimated_sessions_to_exit":sessions_to_exit,"exit_bookwalk":bookwalk,"quote_age_seconds":age,"quote_stale":quote_stale})
    marked=[row for row in result if row["market_value"] is not None]
    totals={"positions":len(result),"cost_value":sum(row["cost_value"] for row in result),
        "market_value":sum(row["market_value"] for row in marked) if len(marked)==len(result) else None,
        "unrealized_pnl":sum(row["unrealized_pnl"] for row in marked) if len(marked)==len(result) else None,
        "positions_without_price":len(result)-len(marked),
        "positions_without_adv20":sum(row["adv20_volume"] is None for row in result),
        "max_estimated_sessions_to_exit":max((row["estimated_sessions_to_exit"] for row in result if row["estimated_sessions_to_exit"] is not None),default=None)}
    return {"source":"ssi","market_state":(market_meta or {}).get("state"),"as_of":clock.isoformat(timespec="seconds"),"participation_pct":participation*100,
        "methodology":{"adv_sessions":20,"adv_basis":"20 prior daily OHLC volume records; current session excluded while market is open","participation_range_pct":[5,30],"bookwalk_levels":config.ORDER_COST_LEVELS},
        "positions":result,"summary":totals,"disclaimer":"Số phiên thoát vị thế là ước tính theo ADV20 × tỷ lệ tham gia, không phải cam kết khớp. Book-walk dùng snapshot bid SSI đang lưu và độ sâu hiển thị; không gửi lệnh."}

