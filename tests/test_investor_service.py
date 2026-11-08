import json

from src.sli import config, database
from fastapi import HTTPException
from src.sli import api
from src.sli.investor_service import _condition


def test_watchlist_condition_requires_both_own_symbol_baselines():
    settings = {
        "favorable_spread_pctl": 50,
        "stress_spread_pctl": 90,
        "favorable_depth_pctl": 50,
        "stress_depth_pctl": 10,
    }
    assert _condition({"spread_percentile": None, "depth_percentile": 80}, settings)["key"] == "insufficient"
    assert _condition({"spread_percentile": 95, "depth_percentile": 80}, settings)["key"] == "stress"
    assert _condition({"spread_percentile": 20, "depth_percentile": 75}, settings)["key"] == "favorable"
    assert _condition({"spread_percentile": 65, "depth_percentile": 40}, settings)["key"] == "caution"


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
