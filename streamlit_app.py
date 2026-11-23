from __future__ import annotations

import os
import html
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import requests
import streamlit as st

API_BASE = os.getenv("SLI_API_BASE", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT = (2, 25)
NAV = ["Tổng quan", "Danh mục", "Chi tiết mã", "Xếp hạng", "Cảnh báo", "Dữ liệu"]

st.set_page_config(page_title="Stock Liquidity Intelligence", page_icon="📈", layout="wide")

# The original dashboard is still the source of truth for the full interactive
# UI, including its animated market background. Keep it intact inside the
# Streamlit shell rather than approximating its many views and interactions.
if os.getenv("SLI_NATIVE_STREAMLIT_UI", "0").strip() != "1":
    st.markdown("""
    <style>
    [data-testid="stHeader"], [data-testid="stToolbar"], footer { display:none!important; }
    [data-testid="stMainBlockContainer"] { max-width:none!important; padding:0!important; }
    [data-testid="stIFrame"] { border:0!important; }
    </style>
    """, unsafe_allow_html=True)

    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    @st.fragment(run_every="15m", key="dashboard-refresh")
    def dashboard_frame():
        base = os.getenv("SLI_WEB_BASE", "http://127.0.0.1:8000/")
        parts = urlsplit(base)
        query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                 if key not in {"_sli_build", "_sli_refresh"}]
        query.extend([
            ("_sli_build", "20261122-ui-filter-v2"),
            ("_sli_refresh", str(int(datetime.now().timestamp()))),
        ])
        embed_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        try:
            response = requests.get(f"{API_BASE}/api/status", timeout=(2, 8))
            response.raise_for_status()
            db_status = response.json()
            db_stamp = db_status.get("last_persisted_at") or db_status.get("last_data_at")
            if db_stamp:
                parsed = datetime.fromisoformat(str(db_stamp).replace("Z", "+00:00"))
                db_stamp = parsed.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%H:%M:%S %d/%m/%Y")
            else:
                db_stamp = "chưa có bản ghi"
        except (requests.RequestException, ValueError, TypeError):
            db_stamp = "chưa đọc được trạng thái database"

        st.caption(f"Trang tự cập nhật mỗi 15 phút · Bản ghi database gần nhất: {db_stamp}")
        st.iframe(
            embed_url,
            width="stretch",
            height=900,
            alt="Bảng điều khiển Stock Liquidity Intelligence bản gốc",
        )

    dashboard_frame()
    st.stop()

st.markdown("""
<style>
:root { --sli-forest:#031b12; --sli-forest-2:#062b1a; --sli-green:#008f4c; --sli-red:#ff5147; --sli-ink:#153432; --sli-muted:#70857c; --sli-line:#dce8df; --sli-card:rgba(249,253,249,.97); }
[data-testid="stAppViewContainer"] { background:radial-gradient(ellipse at 70% 12%,#0b472b 0,transparent 39%),linear-gradient(145deg,#03180f,#05291a 58%,#031b12); }
[data-testid="stHeader"] { background:rgba(2,28,18,.82); }
[data-testid="stMainBlockContainer"] { max-width:1480px; padding:0 2rem 3rem; }
[data-testid="stSidebar"] { background:#062719; }
[data-testid="stSidebar"] * { color:#e6f5e9; }
[data-testid="stMarkdownContainer"] h1,[data-testid="stMarkdownContainer"] h2,[data-testid="stMarkdownContainer"] h3 { color:#f1fff4; letter-spacing:-.025em; }
[data-testid="stMarkdownContainer"] p { color:#c6dfcc; }
.sli-masthead { padding:22px 18px 18px; margin:0 -1rem 0; text-align:center; color:#e6f5e9; background:linear-gradient(180deg,rgba(2,28,18,.82),rgba(3,34,22,.74)); border-bottom:1px solid rgba(112,225,151,.22); }
.sli-masthead .kicker { color:#8cebae; font:800 10px/1.4 'Segoe UI',sans-serif; letter-spacing:.14em; text-transform:uppercase; }
.sli-masthead h1 { margin:5px 0 2px; color:#f0fff4; font:800 clamp(24px,3vw,36px)/1.12 'Segoe UI',sans-serif; letter-spacing:-.04em; }
.sli-masthead p { margin:0; color:#b4d3bc; font-size:13px; }
.sli-masthead .accent { width:56px; height:3px; margin:14px auto 0; border-radius:8px; background:#ff5147; }
[data-testid="stRadio"] > div[role="radiogroup"] { display:flex; flex-wrap:nowrap; gap:3px; overflow-x:auto; padding:0 12px; background:rgba(3,34,22,.92); border-bottom:1px solid rgba(116,218,151,.22); }
[data-testid="stRadio"] label { min-width:max-content; margin:0!important; padding:10px 9px!important; border-bottom:3px solid transparent; border-radius:0!important; color:#c6dfcc!important; font-size:12px!important; font-weight:750!important; }
[data-testid="stRadio"] label:has(input:checked) { color:white!important; background:linear-gradient(110deg,#00a650,#08733e); border-bottom-color:var(--sli-red); box-shadow:0 5px 22px rgba(0,225,111,.18); }
[data-testid="stRadio"] label p { color:inherit!important; }
[data-testid="stRadio"] label > div:first-child { display:none!important; }
[data-testid="stMetric"] { min-height:112px; padding:16px 17px; border:1px solid rgba(205,235,214,.8); border-radius:14px; background:var(--sli-card); box-shadow:0 6px 24px rgba(0,12,7,.12); }
[data-testid="stMetricLabel"] p,[data-testid="stMetricLabel"] * { color:#647a6d!important; font-size:12px!important; }
[data-testid="stMetricValue"],[data-testid="stMetricValue"] * { color:#17432d!important; font-size:clamp(20px,2.3vw,30px)!important; font-weight:800!important; }
[data-testid="stVerticalBlockBorderWrapper"] { padding:18px 20px; border:1px solid rgba(205,235,214,.8); border-radius:16px; background:var(--sli-card); box-shadow:0 8px 28px rgba(0,12,7,.12); }
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stMarkdownContainer"] h2,[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stMarkdownContainer"] h3 { color:#17432d; }
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stMarkdownContainer"] p { color:#587166; }
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stMetric"] { box-shadow:none; }
.st-key-market_status_panel { padding:0!important; border:0!important; background:transparent!important; box-shadow:none!important; }
.st-key-overview-spread-card { background:rgba(249,253,249,.97)!important; color:#153432!important; }
.st-key-overview-spread-card h2,.st-key-overview-spread-card h3 { color:#17432d!important; }
.st-key-overview-spread-card [data-testid="stMarkdownContainer"] p { color:#587166!important; }
.st-key-overview-hero { padding:10px 6px 14px; }
.st-key-overview-hero [data-testid="stCaptionContainer"] p { color:#a8e5bb!important; font-size:11px; font-weight:750; letter-spacing:.04em; }
.st-key-overview-controls { padding:12px 14px 4px; border:1px solid rgba(112,225,151,.35); border-radius:14px; background:rgba(3,34,22,.92); box-shadow:0 8px 22px rgba(0,12,7,.18); }
.st-key-overview-controls label p,.st-key-overview-controls [data-testid="stMarkdownContainer"] p { color:#d3ead9!important; }
[data-testid="stTextInput"] input,[data-testid="stSelectbox"] div[data-baseweb="select"] { background:#f7fff9; color:#153b2a; border-radius:9px; }
[data-testid="stButton"] button { border-radius:10px; font-weight:750; }
[data-testid="stDataFrame"] { border:1px solid var(--sli-line); border-radius:11px; overflow:hidden; }
[data-testid="stAlert"] { border-radius:11px; }
.sli-status-strip { display:flex; align-items:center; gap:10px; margin:12px 0 20px; padding:11px 16px; border:1px solid rgba(112,225,151,.24); border-radius:10px; background:rgba(2,28,18,.84); color:#e7f4e9; font-size:12px; font-weight:650; }
.sli-status-strip .status-dot { width:9px; height:9px; flex:none; border-radius:50%; background:#00a650; box-shadow:0 0 0 4px rgba(0,166,80,.18); }
[data-testid="stPlotlyChart"], [data-testid="stVegaLiteChart"] { border-radius:12px; overflow:hidden; }
@media(max-width:760px) { [data-testid="stMainBlockContainer"] { padding:0 .65rem 2rem; } .sli-masthead { margin:0 -.65rem; padding:17px 10px 14px; } [data-testid="stRadio"] > div[role="radiogroup"] { padding:0 4px; } [data-testid="stRadio"] label { padding:10px 11px!important; font-size:12px!important; } }
</style>
""", unsafe_allow_html=True)


def api(method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> Any:
    response = requests.request(method, f"{API_BASE}{path}", params=params, json=body, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json() if response.content else {}


@st.cache_data(ttl=2, show_spinner=False)
def get_status() -> dict[str, Any]:
    return api("GET", "/api/status")


def _fetch_profile(window_minutes: int, symbol: str) -> dict[str, Any]:
    return api("GET", "/api/intraday-profile", params={"window_minutes": window_minutes, **({"symbol": symbol} if symbol else {})})


@st.cache_data(ttl=10, show_spinner=False)
def get_live_profile(window_minutes: int, symbol: str, refresh_key: int) -> dict[str, Any]:
    return _fetch_profile(window_minutes, symbol)


@st.cache_data(ttl=300, show_spinner=False)
def get_saved_profile(window_minutes: int, symbol: str, refresh_key: int) -> dict[str, Any]:
    return _fetch_profile(window_minutes, symbol)


@st.cache_data(ttl=30, show_spinner=False)
def get_rankings(window_minutes: int, board: str, refresh_key: int) -> dict[str, Any]:
    params = {"window_minutes": window_minutes, "limit": 500}
    if board != "Tất cả":
        params["board"] = board
    return api("GET", "/api/rankings", params=params)


@st.cache_data(ttl=10, show_spinner=False)
def get_watchlists() -> dict[str, Any]:
    return api("GET", "/api/me/watchlists")


@st.cache_data(ttl=5, show_spinner=False)
def get_watchlist_live(watchlist_id: int, size: int, tolerance: int, side: str) -> dict[str, Any]:
    return api("GET", f"/api/me/watchlists/{watchlist_id}/live", params={"order_value_million": size, "tolerance_bps": tolerance, "side": side})


@st.cache_data(ttl=30, show_spinner=False)
def get_alerts(symbol: str, refresh_key: int) -> dict[str, Any]:
    return api("GET", "/api/alerts", params={"limit": 500, **({"symbol": symbol} if symbol else {})})


@st.cache_data(ttl=30, show_spinner=False)
def get_quality() -> dict[str, Any]:
    return api("GET", "/api/quality")


@st.cache_data(ttl=120, show_spinner=False)
def get_methodology() -> dict[str, Any]:
    return api("GET", "/api/methodology")


@st.cache_data(ttl=30, show_spinner=False)
def get_collector_runs() -> dict[str, Any]:
    return api("GET", "/api/collector/runs", params={"limit": 20})


@st.cache_data(ttl=30, show_spinner=False)
def get_detail(symbol: str, sessions: int, refresh_key: int) -> dict[str, Any]:
    return api("GET", f"/api/detail/{symbol}", params={"window_sessions": sessions})


def pretty_timestamp(value: Any) -> str:
    if not value:
        return "Chưa có thời điểm ghi nhận"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%d/%m/%Y %H:%M:%S %z")
    except (TypeError, ValueError):
        return str(value)


def show_api_error(label: str, exc: Exception) -> None:
    response = getattr(exc, "response", None)
    detail = ""
    if response is not None:
        try:
            detail = str(response.json().get("detail", ""))
        except (ValueError, AttributeError):
            detail = ""
    st.error(f"{label}: {detail or exc}")


st.markdown("""
<div class="sli-masthead">
  <div class="kicker">Vi cấu trúc thị trường · Bảng điều khiển nghiên cứu</div>
  <h1>Stock Liquidity Intelligence</h1>
  <p>Đo lường và diễn giải thanh khoản niêm yết theo thời gian</p>
  <div class="accent"></div>
</div>
""", unsafe_allow_html=True)
nav_col, refresh_col = st.columns([8, 1])
with nav_col:
    page = st.radio("Trang", NAV, horizontal=True, label_visibility="collapsed", key="main_navigation")
with refresh_col:
    if st.button("↻", type="primary", use_container_width=True, help="Làm mới dữ liệu"):
        st.cache_data.clear()
        st.session_state["refresh_key"] = st.session_state.get("refresh_key", 0) + 1
        st.rerun()

refresh_key = st.session_state.get("refresh_key", 0)
try:
    status = get_status()
except requests.RequestException as exc:
    st.error(f"Backend chưa phản hồi tại {API_BASE}: {exc}")
    st.info("Khởi chạy backend bằng scripts\\run_streamlit.ps1 để mở cả API và giao diện.")
    st.stop()

market_state = str(status.get("market_state") or "UNKNOWN").upper()
worker = str(status.get("worker_status") or "unknown")
last_saved = pretty_timestamp(status.get("last_persisted_at") or status.get("last_data_at"))
last_message = pretty_timestamp(status.get("last_quote_received_at") or status.get("last_message_at"))
session_label = "TRONG PHIÊN · LIVE" if market_state in {"IN_SESSION", "LUNCH_BREAK"} else "NGOÀI PHIÊN"
status_line = f"SSI FASTCONNECT · {session_label}   |   Collector: {html.escape(worker)}   |   Quote gần nhất: {html.escape(last_message)}   |   Đã lưu DB: {html.escape(last_saved)}"
st.markdown(f"<div class='sli-status-strip'><span class='status-dot'></span>{status_line}</div>", unsafe_allow_html=True)

if page == "Tổng quan":
    with st.container(key="overview-hero"):
        hero_title, hero_controls = st.columns([1.15, 1.85], vertical_alignment="bottom")
        with hero_title:
            st.caption("BẢNG ĐIỀU KHIỂN")
            st.header("Tổng quan thanh khoản")
            st.caption("Quan sát spread, độ sâu sổ lệnh và dữ liệu SSI theo thời gian.")
        with hero_controls:
            with st.container(border=True, key="overview-controls"):
                filter_symbol, filter_window = st.columns([1, 1])
                with filter_symbol:
                    symbol = st.text_input("Mã cổ phiếu", placeholder="Tất cả mã hoặc nhập FPT").strip().upper()
                with filter_window:
                    window = st.selectbox("Cửa sổ biểu đồ", [15, 30, 60, 120, 390], index=2, format_func=lambda x: f"{x} phút" if x < 390 else "Cả phiên")

    feed_text = "Trong phiên" if market_state in {"IN_SESSION", "LUNCH_BREAK"} else "Ngoài phiên"
    lag = status.get("persistence_lag_seconds")
    metric_cols = st.columns(4)
    metric_cols[0].metric("Trạng thái phiên", feed_text, border=True)
    metric_cols[1].metric("Message nhận", int(status.get("messages_received") or 0), border=True)
    metric_cols[2].metric("Hàng đợi ghi", int(status.get("queue_depth") or 0), border=True)
    metric_cols[3].metric("Trễ lưu", "—" if market_state not in {"IN_SESSION", "LUNCH_BREAK"} or lag is None else f"{lag:.0f} giây", border=True)

    @st.fragment(run_every="5s")
    def overview_chart():
        try:
            current = get_status()
            state = str(current.get("market_state") or "UNKNOWN").upper()
            profile = get_live_profile(window, symbol, refresh_key) if state in {"IN_SESSION", "LUNCH_BREAK"} else get_saved_profile(window, symbol, refresh_key)
            rows = profile.get("profile") or []
            if rows:
                frame = pd.DataFrame(rows)
                if "minute" in frame.columns:
                    frame = frame.set_index("minute")
                if "median_spread_bps" in frame.columns:
                    plot_data = frame.reset_index().dropna(subset=["median_spread_bps"])
                    tick_step = max(1, (len(plot_data) - 1) // 7)
                    tick_values = plot_data["minute"].iloc[::tick_step].tolist()
                    if tick_values and tick_values[-1] != plot_data["minute"].iloc[-1]:
                        tick_values.append(plot_data["minute"].iloc[-1])
                    chart = (
                        alt.Chart(plot_data)
                        .mark_line(color="#008f55", strokeWidth=2.6)
                        .encode(
                            x=alt.X("minute:N", title="Thời gian", sort=None,
                                    axis=alt.Axis(values=tick_values, labelAngle=0, labelOverlap="greedy", grid=False, labelColor="#50685b", titleColor="#365344")),
                            y=alt.Y("median_spread_bps:Q", title="Spread (bps)", scale=alt.Scale(zero=False),
                                    axis=alt.Axis(gridColor="#dce8df", labelColor="#50685b", titleColor="#365344")),
                            tooltip=[alt.Tooltip("minute:N", title="Thời điểm"), alt.Tooltip("median_spread_bps:Q", title="Spread", format=".2f")],
                        )
                        .properties(height=320, background="#f9fdf9", padding={"left": 10, "right": 20, "top": 8, "bottom": 10})
                        .configure_view(strokeWidth=0)
                    )
                    st.altair_chart(chart, width="stretch", theme=None, key="spread-chart")
                st.caption(f"{len(rows):,} bar · {symbol or 'toàn thị trường'} · đến {pretty_timestamp(profile.get('data_as_of') or profile.get('as_of'))} · {state}")
            else:
                st.warning("Chưa có bar phù hợp trong cửa sổ đã chọn.")
        except requests.RequestException as exc:
            show_api_error("Không tải được biểu đồ", exc)

    with st.container(border=True, key="overview-spread-card"):
        st.subheader("Spread trung vị theo phút")
        st.caption("Ngoài phiên hiển thị bar SSI gần nhất đã lưu; biểu đồ không dựng dữ liệu giả.")
        overview_chart()
    st.subheader("Xếp hạng nhanh")
    if st.button("Tải top spread thấp"):
        st.session_state["load_overview_rankings"] = True
    if st.session_state.get("load_overview_rankings"):
        try:
            payload = get_rankings(window, "Tất cả", refresh_key)
            rows = payload.get("lowest_spread") or []
            if rows:
                st.dataframe(pd.DataFrame(rows[:10]), hide_index=True, width="stretch")
            else:
                st.info("Không có mã đủ điều kiện trong cửa sổ này.")
        except requests.RequestException as exc:
            show_api_error("Không tải được xếp hạng", exc)

elif page == "Danh mục":
    st.header("Danh mục của tôi")
    try:
        lists_payload = get_watchlists()
        watchlists = lists_payload.get("watchlists") or []
        if not watchlists:
            st.info("Chưa có danh sách theo dõi.")
        else:
            labels = {f"{item['name']} ({len(item.get('items', []))} mã)": item for item in watchlists}
            selected_label = st.selectbox("Danh sách", list(labels))
            selected = labels[selected_label]
            watchlist_id = int(selected["id"])
            with st.form("add_symbol_form", clear_on_submit=True):
                new_symbol = st.text_input("Thêm mã cổ phiếu", placeholder="Ví dụ FPT")
                add_clicked = st.form_submit_button("Thêm mã", type="primary")
            if add_clicked and new_symbol.strip():
                try:
                    api("POST", f"/api/me/watchlists/{watchlist_id}/items", body={"symbol": new_symbol.strip().upper()})
                    st.cache_data.clear()
                    st.success(f"Đã thêm {new_symbol.strip().upper()} vào danh sách.")
                    st.rerun()
                except requests.RequestException as exc:
                    show_api_error("Không thêm được mã", exc)
            items = selected.get("items") or []
            if items:
                symbols = [str(item.get("symbol", "")) for item in items]
                remove_col, note_col = st.columns([1, 2])
                with remove_col:
                    remove_symbol = st.selectbox("Chọn mã để xóa", symbols)
                    if st.button("Xóa mã đã chọn"):
                        try:
                            api("DELETE", f"/api/me/watchlists/{watchlist_id}/items/{remove_symbol}")
                            st.cache_data.clear()
                            st.success(f"Đã xóa {remove_symbol}.")
                            st.rerun()
                        except requests.RequestException as exc:
                            show_api_error("Không xóa được mã", exc)
                with note_col:
                    note_symbol = st.selectbox("Mã ghi chú", symbols, key="note_symbol")
                    note_value = st.text_input("Ghi chú cá nhân", max_chars=200)
                    if st.button("Lưu ghi chú"):
                        try:
                            api("PATCH", f"/api/me/watchlists/{watchlist_id}/items/{note_symbol}", body={"note": note_value})
                            st.cache_data.clear()
                            st.success("Đã lưu ghi chú.")
                            st.rerun()
                        except requests.RequestException as exc:
                            show_api_error("Không lưu được ghi chú", exc)
                if st.button("Tải quote và mô phỏng chi phí"):
                    st.session_state["watch_live_refresh"] = st.session_state.get("watch_live_refresh", 0) + 1
                if st.session_state.get("watch_live_refresh"):
                    size = st.slider("Quy mô lệnh (triệu đồng)", 10, 5000, 100, step=10)
                    tolerance = st.slider("Ngưỡng trượt giá (bps)", 5, 200, 30)
                    side = st.selectbox("Chiều mô phỏng", ["buy", "sell"], format_func=lambda x: "Mua theo ask" if x == "buy" else "Bán theo bid")
                    try:
                        live = get_watchlist_live(watchlist_id, size, tolerance, side)
                        live_rows = live.get("items") or []
                        if live_rows:
                            st.dataframe(pd.DataFrame(live_rows), hide_index=True, width="stretch")
                            st.caption(f"Snapshot: {pretty_timestamp(live.get('meta', {}).get('as_of'))}. Mô phỏng tĩnh, không gửi lệnh.")
                    except requests.RequestException as exc:
                        show_api_error("Không tải được quote/danh mục", exc)
                else:
                    st.dataframe(pd.DataFrame(items), hide_index=True, width="stretch")
            else:
                st.caption("Danh sách đang trống.")
    except requests.RequestException as exc:
        show_api_error("Không tải được danh mục", exc)

elif page == "Chi tiết mã":
    st.header("Chi tiết cổ phiếu")
    col1, col2 = st.columns([2, 1])
    with col1:
        detail_symbol = st.text_input("Mã cổ phiếu", value="FPT").strip().upper()
    with col2:
        sessions = st.selectbox("Số phiên lịch sử", [1, 5, 10, 20], index=1)
    if st.button("Tải phân tích mã", type="primary"):
        st.session_state["detail_requested"] = detail_symbol
    requested = st.session_state.get("detail_requested")
    if requested:
        try:
            detail = get_detail(requested, sessions, refresh_key)
            st.caption(f"{detail.get('company_name', requested)} · {detail.get('board', '—')} · trạng thái {detail.get('state', '—')} · quote {pretty_timestamp(detail.get('quote_time'))}")
            cols = st.columns(4)
            orderbook = (detail.get("widgets") or {}).get("orderbook", {}).get("data") or {}
            latest = detail.get("latest") or orderbook
            cols[0].metric("Spread", "—" if latest.get("spread_bps") is None else f"{latest['spread_bps']:.2f} bps")
            cols[1].metric("Độ sâu mua", str(latest.get("bid_depth_value", "—")))
            cols[2].metric("Độ sâu bán", str(latest.get("ask_depth_value", "—")))
            cols[3].metric("Mẫu lịch sử", str(detail.get("snapshot_count", "—")))
            if detail.get("baseline_eligible"):
                st.success(f"Đủ baseline · {detail.get('baseline_session_count', 0)} phiên · {detail.get('baseline_bars_have', 0)} bar")
            else:
                st.info(f"Chưa đủ baseline · đang có {detail.get('baseline_session_count', 0)} phiên")
            st.caption("Sổ lệnh chỉ có khi SSI đã lưu quote; ngoài phiên hệ thống không giả lập quote hiện tại.")
        except requests.RequestException as exc:
            show_api_error("Không tải được chi tiết mã", exc)

elif page == "Xếp hạng":
    st.header("Xếp hạng thanh khoản")
    c1, c2 = st.columns(2)
    with c1:
        window = st.selectbox("Cửa sổ xếp hạng", [30, 60, 120, 390], index=1, format_func=lambda x: f"{x} phút" if x < 390 else "Cả phiên")
    with c2:
        board = st.selectbox("Sàn", ["Tất cả", "HOSE", "HNX", "UPCOM"])
    if st.button("Tính bảng xếp hạng", type="primary"):
        st.session_state["ranking_request"] = (window, board)
    if st.session_state.get("ranking_request"):
        selected_window, selected_board = st.session_state["ranking_request"]
        try:
            with st.spinner("Đang tính xếp hạng từ bar SSI đã lưu…"):
                ranking = get_rankings(selected_window, selected_board, refresh_key)
            rows = ranking.get("lowest_spread") or []
            st.caption(f"{len(rows)} mã đủ điều kiện · cửa sổ {selected_window} phút · dữ liệu nguồn SSI đã lưu")
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            else:
                st.warning("Không có mã đủ điều kiện trong cửa sổ này; không suy đoán thứ hạng.")
        except requests.RequestException as exc:
            show_api_error("Không tải được bảng xếp hạng", exc)

elif page == "Cảnh báo":
    st.header("Cảnh báo")
    alert_symbol = st.text_input("Lọc theo mã (để trống = tất cả)").strip().upper()
    if st.button("Tải cảnh báo", type="primary"):
        st.session_state["alerts_request"] = alert_symbol
    if st.session_state.get("alerts_request") is not None:
        try:
            payload = get_alerts(st.session_state["alerts_request"], refresh_key)
            rows = payload.get("alerts") or []
            st.caption(f"Tổng {payload.get('alert_count', 0)} cảnh báo · đang hoạt động {payload.get('active_count', 0)} · đã kết thúc {payload.get('resolved_count', 0)}")
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            else:
                st.info("Không có cảnh báo trong dữ liệu đã lưu.")
        except requests.RequestException as exc:
            show_api_error("Không tải được cảnh báo", exc)
    st.caption("Bản chuyển giao diện hiện chỉ xem lịch sử cảnh báo; API hiện có chưa cung cấp thao tác tạo quy tắc cảnh báo qua trang này.")

else:
    st.header("Dữ liệu & phương pháp")
    st.subheader("Trạng thái collector")
    summary = {"worker": worker, "feed": status.get("feed_state"), "messages_received": status.get("messages_received"), "rows_valid": status.get("rows_valid"), "queue_dropped": status.get("queue_dropped"), "last_saved": last_saved, "last_message": last_message, "last_error": status.get("last_error")}
    st.json(summary, expanded=True)
    run_col, quality_col = st.columns(2)
    with run_col:
        if st.button("Tải lịch sử các lượt collector"):
            try:
                runs = get_collector_runs().get("runs") or []
                st.dataframe(pd.DataFrame(runs), hide_index=True, width="stretch") if runs else st.info("Chưa có lượt collector trong lịch sử.")
            except requests.RequestException as exc:
                show_api_error("Không tải được lịch sử collector", exc)
    with quality_col:
        if st.button("Kiểm tra chất lượng/độ phủ database"):
            try:
                quality = get_quality()
                st.json(quality, expanded=True)
            except requests.RequestException as exc:
                show_api_error("Không tải được báo cáo chất lượng", exc)
    if st.button("Xem phương pháp tính"):
        try:
            methodology = get_methodology()
            st.json(methodology, expanded=True)
        except requests.RequestException as exc:
            show_api_error("Không tải được phương pháp", exc)
