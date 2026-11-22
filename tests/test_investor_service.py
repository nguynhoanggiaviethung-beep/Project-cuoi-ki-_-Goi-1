import json
import pytest
from datetime import datetime,timedelta
from pathlib import Path

from src.sli import config, database
from fastapi import HTTPException
from src.sli import api
from src.sli.investor_service import _market_level, _own_baseline_state, _select_peer_group


def test_own_baseline_state_uses_only_active_ssi_alerts_and_valid_own_baseline():
    baseline={"eligible":True,"has_snapshot":True,"baseline_mode":"same_phase","sessions_have":120,"bars_have":120}
    assert _own_baseline_state(set(),baseline)["state"]=="normal"
    assert _own_baseline_state({"spread_widening"},baseline)["state"]=="caution"
    assert _own_baseline_state({"combined_liquidity_stress"},baseline)["state"]=="stress"
    assert _own_baseline_state(set(),{**baseline,"eligible":False,"sessions_have":119,"bars_have":120})["state"]=="insufficient"
    assert _own_baseline_state(set(),baseline,quote_stale=True,in_session=True)["state"]=="quote_stale"
    assert _own_baseline_state(set(),{**baseline,"has_snapshot":False})["state"]=="insufficient"


def test_market_level_uses_independent_peer_medians_and_percentiles():
    current={"symbol":"AAA","spread":10,"depth":200,"spread_ticks":1}
    peers=[{"spread":20,"depth":100},{"spread":30,"depth":300},{"spread":5,"depth":50}]
    result=_market_level(current,peers,"HOSE · cùng nhóm bước giá 10đ",10)
    assert result["level"]=="Cao"
    assert result["n_peers"]==3
    assert result["spread_pctile"]==pytest.approx(16.666666)
    assert result["depth_pctile"]==pytest.approx(75)
    assert result["spread_ticks"]==1


def test_peer_group_expands_by_exchange_then_all_market_and_respects_tick_band(monkeypatch):
    monkeypatch.setattr(config,"PEER_MIN_SIZE",3)
    current={"symbol":"AAA","board":"HOSE","tick":10}
    rows=[{"symbol":f"H{i}","board":"HOSE","tick":10} for i in range(2)]
    rows += [{"symbol":f"B{i}","board":"HOSE","tick":50} for i in range(1)]
    rows += [{"symbol":f"X{i}","board":"HNX","tick":100} for i in range(2)]
    peers,basis=_select_peer_group(current,rows)
    assert len(peers)==3 and "HOSE" in basis and "mọi nhóm" in basis
    peers,basis=_select_peer_group(current,rows[:1])
    assert len(peers)==1 and "toàn thị trường" in basis


def test_daily_activity_uses_20_prior_daily_ohlc_rows_and_reports_missing_intraday(tmp_path,monkeypatch):
    path=tmp_path/"activity.sqlite3"
    monkeypatch.setattr(config,"DB_PATH",path)
    monkeypatch.setattr(database,"seed_demo_if_empty",lambda:None)
    database.initialize()
    with database.connect() as db:
        for offset in range(20,0,-1):
            day=(datetime(2026,11,6)-timedelta(days=offset)).date().isoformat()
            db.execute("""INSERT INTO historical_prices(source,symbol,trading_date,timeframe,value,fetched_at)
                VALUES('ssi','FPT',?,'1d',1000,'2026-11-06T15:00:00+07:00')""",(day,))
        db.execute("""INSERT INTO historical_prices(source,symbol,trading_date,timeframe,value,fetched_at)
            VALUES('ssi','FPT','2026-11-06','1d',2000,'2026-11-06T15:00:00+07:00')""")
    result=database.fetch_watchlist_daily_activity(["FPT"],datetime.fromisoformat("2026-11-06T15:00:00+07:00"),in_session=False)["FPT"]
    assert result["ratio"]==pytest.approx(200)
    assert result["sessions_used"]==20
    with database.connect() as db:
        for offset in range(20,0,-1):
            day=(datetime(2026,11,6)-timedelta(days=offset)).date().isoformat()
            db.execute("""INSERT INTO trade_ticks(source,symbol,received_at_utc,received_at_vn,price,quantity,payload_hash)
                VALUES('ssi','FPT',?, ?,100,10,?)""",(f"{day}T03:00:00+00:00",f"{day}T10:00:00+07:00",f"prior-{day}"))
        db.execute("""INSERT INTO trade_ticks(source,symbol,received_at_utc,received_at_vn,price,quantity,payload_hash)
            VALUES('ssi','FPT','2026-11-06T03:00:00+00:00','2026-11-06T10:00:00+07:00',100,20,'today')""")
    same_time=database.fetch_watchlist_daily_activity(["FPT"],datetime.fromisoformat("2026-11-06T10:01:00+07:00"),in_session=True)["FPT"]
    assert same_time["ratio"]==pytest.approx(200)
    assert same_time["basis"]=="running_vs_same_time_20_sessions"
    missing=database.fetch_watchlist_daily_activity(["FPT"],datetime.fromisoformat("2026-11-07T10:00:00+07:00"),in_session=True)["FPT"]
    assert missing["ratio"] is None
    assert missing["basis"]=="intraday_trade_value_unavailable"


def test_live_api_passes_the_three_watchlist_analysis_objects(monkeypatch):
    item={"symbol":"FPT","market_level":{"level":"Trung bình"},
          "own_state":{"state":"normal"},"daily_activity":{"ratio":85},
          "window_label":"Phiên 20/11/2026","as_of":"2026-11-20T10:00:00+07:00"}
    payload={"items":[item],"as_of":item["as_of"]}
    monkeypatch.setattr(api,"_local_watchlist",lambda _id:{"id":1,"items":[]})
    monkeypatch.setattr(api,"_market_meta",lambda _source:{"state":"IN_SESSION"})
    monkeypatch.setattr(api,"get_clock_now",lambda:datetime.fromisoformat(item["as_of"]))
    monkeypatch.setattr(api,"build_watchlist_live",lambda *args:payload)
    result=api.local_watchlist_live(1)
    row=result["items"][0]
    assert row["market_level"]["level"]=="Trung bình"
    assert row["own_state"]["state"]=="normal"
    assert row["daily_activity"]["ratio"]==85
    assert row["window_label"]=="Phiên 20/11/2026"
    ui=Path("frontend/watchlist.js").read_text(encoding="utf-8")
    assert "row.market_level" in ui and "row.own_state" in ui and "row.daily_activity" in ui
    assert "Mức so với thị trường:" in ui and "So với bình thường của mã:" in ui


def test_empty_watchlist_live_build_includes_market_thresholds(monkeypatch):
    from src.sli import investor_service
    monkeypatch.setattr(database,"get_local_settings",lambda:{"order_value_million":100,"tolerance_bps":30,"order_side":"buy"})
    monkeypatch.setattr(database,"count_active_alerts_by_symbol",lambda *_:{})
    monkeypatch.setattr(database,"fetch_active_alert_types_by_symbol",lambda *_:{})
    monkeypatch.setattr(database,"latest_bar_minute",lambda *_:"2026-11-20T14:59:00+07:00")
    monkeypatch.setattr(investor_service,"_cached_market_cross_section",lambda *_:([{"spread":10.0,"depth":100.0}],{}))
    monkeypatch.setattr(database,"fetch_watchlist_daily_activity",lambda *args,**kwargs:{})
    result=investor_service.build_watchlist_live({"id":1,"name":"Danh mục của tôi","items":[]},{"state":"OUTSIDE"},datetime.fromisoformat("2026-11-20T15:00:00+07:00"))
    assert result["analysis"]["market_spread_p75"]==pytest.approx(10)
    assert result["analysis"]["market_depth_p25"]==pytest.approx(100)


def test_inv_d_settings_schema_migration_backs_up_local_profile(tmp_path, monkeypatch):
    path=tmp_path/"old-profile.sqlite3"
    import sqlite3
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE user_settings(profile_id TEXT PRIMARY KEY,standard_order_shares INTEGER NOT NULL DEFAULT 10000,favorable_spread_pctl REAL NOT NULL DEFAULT 25,stress_spread_pctl REAL NOT NULL DEFAULT 90,favorable_depth_pctl REAL NOT NULL DEFAULT 75,stress_depth_pctl REAL NOT NULL DEFAULT 10,quiet_start TEXT,quiet_end TEXT,notification_channel TEXT NOT NULL DEFAULT 'in_app',updated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE watchlists(id INTEGER PRIMARY KEY,name TEXT NOT NULL)")
        db.execute("CREATE TABLE watchlist_items(watchlist_id INTEGER,symbol TEXT,note TEXT,position INTEGER)")
        db.execute("INSERT INTO user_settings(profile_id,updated_at) VALUES('local','2026-11-20T09:00:00+07:00')")
    monkeypatch.setattr(config,"DB_PATH",path)
    assert database.migrate_inv_d_settings() is True
    assert database.migrate_inv_d_settings() is False
    with database.connect() as db:
        row=db.execute("SELECT order_value_million,tolerance_bps,order_side FROM user_settings WHERE profile_id='local'").fetchone()
    assert tuple(row)==(100,30,"buy")
    backups=list(tmp_path.glob("local-profile-before-inv-d-*.json"))
    assert len(backups)==1
    assert json.loads(backups[0].read_text(encoding="utf-8"))["settings"]["standard_order_shares"]==10000


def test_local_watchlist_schema_is_additive_and_item_order_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "profile.sqlite3")
    monkeypatch.setattr(database, "seed_demo_if_empty", lambda: None)
    database.initialize()
    with database.connect() as db:
        db.execute("CREATE TABLE preserved_user_data(value TEXT)")
        db.execute("INSERT INTO preserved_user_data VALUES('kept')")
    list_id = database.create_local_watchlist("Theo dõi")
    database.add_local_watchlist_item(list_id, "FPT", "Mã cần quan sát")
    database.add_local_watchlist_item(list_id, "HPG", "")
    database.reorder_local_watchlist(list_id, ["HPG", "FPT"])
    rows = database.fetch_local_watchlists()
    assert [item["symbol"] for item in rows[0]["items"]] == ["HPG", "FPT"]
    assert rows[0]["items"][1]["note"] == "Mã cần quan sát"
    backup_name = database.backup_local_profile_json()
    backup = json.loads((tmp_path / backup_name).read_text(encoding="utf-8"))
    assert backup["watchlists"][0]["items"][0]["symbol"] == "HPG"
    database.delete_local_watchlist(list_id)
    assert database.fetch_local_watchlists() == []
    with database.connect() as db:
        assert db.execute("SELECT value FROM preserved_user_data").fetchone()[0] == "kept"


def test_empty_local_watchlists_get_a_persistent_default_list(monkeypatch):
    state={"lists":[]}
    def fetch():return list(state["lists"])
    def create(name):state["lists"].append({"id":1,"name":name,"items":[],"item_count":0});return 1
    monkeypatch.setattr(database,"fetch_local_watchlists",fetch)
    monkeypatch.setattr(database,"create_local_watchlist",create)
    result=api.local_watchlists()
    assert result["watchlists"][0]["name"]=="Danh mục của tôi"
    assert api.local_watchlists()["watchlists"][0]["id"]==1
    assert len(state["lists"])==1


def test_deepest_starter_symbols_come_only_from_saved_valid_ssi_bars(tmp_path,monkeypatch):
    monkeypatch.setattr(config,"DB_PATH",tmp_path/"starter.sqlite3")
    monkeypatch.setattr(database,"seed_demo_if_empty",lambda:None)
    database.initialize()
    with database.connect() as db:
        for symbol,depth in (("AAA",1000),("BBB",5000),("CCC",3000)):
            db.execute("INSERT INTO securities_catalog(source,symbol,board,name_vi,stock_type,updated_at) VALUES('ssi',?,'HOSE',?,'stock','now')",(symbol,symbol))
            db.execute("INSERT INTO bars_1m(source,symbol,minute_vn,session_date,market,session_phase,baseline_bucket,bid_depth_value,ask_depth_value,n_snapshots,coverage_status) VALUES('ssi',?,'2026-11-20T14:59:00+07:00','2026-11-20','HOSE','CONTINUOUS_PM','CLOSE',?,0,1,'ok')",(symbol,depth))
        db.execute("INSERT INTO securities_catalog(source,symbol,board,name_vi,stock_type,updated_at) VALUES('demo','ZZZ','HOSE','ZZZ','stock','now')")
        db.execute("INSERT INTO bars_1m(source,symbol,minute_vn,session_date,market,session_phase,baseline_bucket,bid_depth_value,ask_depth_value,n_snapshots,coverage_status) VALUES('demo','ZZZ','2026-11-20T14:59:00+07:00','2026-11-20','HOSE','CONTINUOUS_PM','CLOSE',99999,0,1,'ok')")
    assert [row["symbol"] for row in database.fetch_deepest_saved_symbols("ssi",10)]==["BBB","CCC","AAA"]


def test_portfolio_client_has_bounded_requests_retry_tabs_and_locale_independent_date():
    from pathlib import Path
    ui=Path("frontend/watchlist.js").read_text(encoding="utf-8")
    html=Path("frontend/index.html").read_text(encoding="utf-8")
    assert "REQUEST_TIMEOUT_MS=8000" in ui and "new AbortController()" in ui
    assert "data-portfolio-retry" in ui and "Không kết nối được máy chủ" in ui
    assert "portfolioTab" in ui and all(f'data-portfolio-tab="{tab}"' in html for tab in ("watch","cost","positions"))
    assert 'id="position-risk-date" type="text"' in html and "dd/mm/yyyy" in html
    assert "type=\"date\"" not in html[html.index('id="position-risk-date"')-20:html.index('id="position-risk-date"')+100]
    assert "parsePositionDate" in ui and "Ngày mua không thể ở tương lai" in ui
    assert 'id="position-risk-date-error"' in html and 'id="position-risk-form-feedback"' in html
    assert "Sau khi lưu, xem dòng vị thế trong bảng bên dưới" in html


def test_simulation_settings_validation_bounds():
    valid={"standard_order_shares":10000,"order_value_million":250,"tolerance_bps":80,"order_side":"sell","quiet_start":None,"quiet_end":None,"notification_channel":"in_app"}
    assert api._validate_local_settings(valid,base=valid)==valid
    for bad in ({**valid,"order_value_million":9},{**valid,"tolerance_bps":201},{**valid,"order_side":"hold"}):
        try:api._validate_local_settings(bad,base=valid)
        except HTTPException as exc:assert exc.status_code==422
        else:assert False,"Expected settings validation error"


def test_local_profile_import_checks_limits_stock_symbols_and_notes(monkeypatch):
    monkeypatch.setattr(api, "_validate_local_symbol", lambda value: str(value).upper())
    valid = {"format": "sli-local-profile-v1", "settings": {},
             "watchlists": [{"name": "Theo dõi", "items": [{"symbol": "fpt", "note": "ok"}]}]}
    _, lists = api._validate_profile_document(valid)
    assert lists[0]["items"][0]["symbol"] == "FPT"
    too_many = {**valid, "watchlists": [{"name": "Theo dõi", "items": [{"symbol": f"M{i}", "note": ""} for i in range(51)]}]}
    try:
        api._validate_profile_document(too_many)
        assert False, "Expected validation error for more than 50 symbols"
    except HTTPException as exc:
        assert exc.status_code == 422
    too_long_note = {**valid, "watchlists": [{"name": "Theo dõi", "items": [{"symbol": "FPT", "note": "x" * 201}]}]}
    try:
        api._validate_profile_document(too_long_note)
        assert False, "Expected validation error for note longer than 200 characters"
    except HTTPException as exc:
        assert exc.status_code == 422


def test_local_watchlist_api_limits_validation_and_backup_before_import(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "profile-api.sqlite3")
    monkeypatch.setattr(database, "seed_demo_if_empty", lambda: None)
    database.initialize()
    catalog = [("HOSE", {"symbol": f"S{i:02d}", "stock_type": "stock", "symbol_name_vi": f"Cổ phiếu {i}"})
               for i in range(51)]
    database.upsert_securities_catalog(catalog)
    first = api.create_local_watchlist({"name": "Danh sách 1"})
    for index in range(2, 6):
        api.create_local_watchlist({"name": f"Danh sách {index}"})
    try:
        api.create_local_watchlist({"name": "Danh sách thứ 6"})
        assert False, "Expected the five-list limit"
    except HTTPException as exc:
        assert exc.status_code == 409
    for index in range(50):
        api.add_local_watchlist_item(first["id"], {"symbol": f"S{index:02d}", "note": ""})
    try:
        api.add_local_watchlist_item(first["id"], {"symbol": "S50", "note": ""})
        assert False, "Expected the fifty-symbol limit"
    except HTTPException as exc:
        assert exc.status_code == 409
    try:
        api.add_local_watchlist_item(first["id"], {"symbol": "UNKNOWN", "note": ""})
        assert False, "Expected unknown symbols to be rejected"
    except HTTPException as exc:
        assert exc.status_code == 422
    imported = api.import_local_profile({"format": "sli-local-profile-v1", "settings": {},
        "watchlists": [{"name": "Nhập vào", "items": [{"symbol": "S50", "note": "Ghi chú"}]}]})
    assert imported["ok"] is True
    assert imported["watchlists"][0]["name"] == "Nhập vào"
    backup = json.loads((tmp_path / imported["backup_file"]).read_text(encoding="utf-8"))
    assert len(backup["watchlists"]) == 5
    assert backup["watchlists"][0]["items"][0]["symbol"] == "S00"



def test_positions_live_uses_twenty_prior_sessions_and_persists_position(tmp_path,monkeypatch):
    from src.sli.investor_service import build_positions_live
    path=tmp_path/"positions.sqlite3"
    monkeypatch.setattr(config,"DB_PATH",path)
    monkeypatch.setattr(database,"seed_demo_if_empty",lambda:None)
    database.initialize()
    as_of=datetime.fromisoformat("2026-11-20T10:00:00+07:00")
    with database.connect() as db:
        db.execute("""INSERT INTO securities_catalog(source,symbol,board,name_vi,stock_type,updated_at)
            VALUES('ssi','FPT','HOSE','FPT','stock',?)""",(as_of.isoformat(),))
        for offset in range(1,21):
            day=(as_of.date()-timedelta(days=offset)).isoformat()
            db.execute("""INSERT INTO historical_prices
                (source,symbol,trading_date,timeframe,close_price,volume,fetched_at)
                VALUES('ssi','FPT',?,'1d',100000,1000000,?)""",(day,as_of.isoformat()))
        # Today's OHLC must not enter ADV20 while the market is open.
        db.execute("""INSERT INTO historical_prices
            (source,symbol,trading_date,timeframe,close_price,volume,fetched_at)
            VALUES('ssi','FPT','2026-11-20','1d',100000,999999999,?)""",(as_of.isoformat(),))
        db.execute("""INSERT INTO observations
            (symbol,source_timestamp,source_ts_vn,received_at_utc,received_at_vn,
             market,validation_status,raw_payload,payload_hash,source,last_price,
             bid_price_1,bid_volume_1,ask_price_1,ask_volume_1)
            VALUES('FPT',?,?,?,?,?,'valid','{}','position-quote','ssi',101000,
                   100900,100000,101100,100000)""",
            ("2026-11-20T09:59:59+07:00","2026-11-20T09:59:59+07:00",
             "2026-11-20T02:59:59+00:00","2026-11-20T09:59:59+07:00","HOSE"))
    position_id=database.add_local_position("FPT",10000,100000,"2026-11-19","test position")
    payload=build_positions_live(10,as_of,{"state":"IN_SESSION"})
    row=payload["positions"][0]
    assert row["id"]==position_id
    assert row["adv20_sessions"]==20
    assert row["adv20_volume"]==1_000_000
    assert row["estimated_sessions_to_exit"]==pytest.approx(0.1)
    assert row["price"]==101000
    assert row["unrealized_pnl"]==10_000_000
    assert row["quote_stale"] is False
    assert row["exit_bookwalk"]["status"]=="full"
    assert database.fetch_local_positions()[0]["note"]=="test position"


def test_inv_c_position_api_crud_after_additive_schema_migration(tmp_path,monkeypatch):
    monkeypatch.setattr(config,"DB_PATH",tmp_path/"positions-api.sqlite3")
    monkeypatch.setattr(database,"fetch_company_metadata",lambda source,symbol:{"stock_type":"stock"})
    assert database.migrate_inv_c_positions()
    today=datetime.now(config.TIMEZONE).date().isoformat()
    created=api.add_local_position({"symbol":"FPT","shares":1000,"average_cost":100000,"opened_on":today,"note":"test"})
    position_id=created["position_id"]
    assert created["positions"][0]["symbol"]=="FPT"
    updated=api.update_local_position(position_id,{"symbol":"FPT","shares":2000,"average_cost":101000,"opened_on":today,"note":"updated"})
    assert updated["positions"][0]["shares"]==2000
    deleted=api.delete_local_position(position_id)
    assert deleted["positions"]==[]
