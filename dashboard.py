import os, sqlite3, requests
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from datetime import datetime, timezone

st.set_page_config(page_title="Global 3-Asset Pro — BTC • Gold • Silver", layout="wide")

st.markdown("""
<style>
.stApp { background-color: #0b0e14; color: #e6edf3; }
.ticker-card {
    background: linear-gradient(145deg, #131824, #0f141e);
    border: 1px solid #222b3d;
    border-radius: 12px;
    padding: 14px 18px;
}
.ticker-title { font-size: 12px; letter-spacing: 1px; color: #8b949e; font-weight: 700; }
.ticker-price { font-size: 24px; font-weight: 800; color: #ffffff; margin: 4px 0; }
.pos { color: #00e676; font-weight: 700; font-size: 13px; }
.neg { color: #ff5252; font-weight: 700; font-size: 13px; }
.badge-247 {
    background: rgba(0, 230, 118, 0.14);
    color: #00e676;
    border: 1px solid rgba(0, 230, 118, 0.4);
    padding: 4px 10px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 700;
}
</style>
""", unsafe_allow_html=True)

ASSETS = {
    "BTC-USD": {"label": "BITCOIN (BTC/USD)", "session": "24/7 NON-STOP CRYPTO"},
    "GC=F":    {"label": "GOLD FUTURES (XAU)", "session": "24/5 COMEX GLOBEX"},
    "SI=F":    {"label": "SILVER FUTURES (XAG)", "session": "24/5 COMEX GLOBEX"},
}

@st.cache_data(ttl=1)
def get_quote(ticker):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=2d"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3).json()
        res = r["chart"]["result"][0]
        meta = res["meta"]
        ltp = float(meta.get("regularMarketPrice", 0))
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or ltp)
        chg = ltp - prev
        pct = (chg / prev * 100.0) if prev else 0.0
        return ltp, chg, pct
    except Exception:
        return 0.0, 0.0, 0.0

@st.cache_data(ttl=2)
def get_candles(ticker, interval="15m", rng="5d"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval={interval}&range={rng}"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=4).json()
    res = r["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({
        "time": pd.to_datetime(ts, unit="s", utc=True),
        "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"], "volume": q["volume"]
    }).dropna()
    df["ema9"] = df["close"].ewm(span=9).mean()
    df["ema21"] = df["close"].ewm(span=21).mean()
    return df

def load_trades():
    if not os.path.exists("paper_trades.db"):
        return pd.DataFrame()
    conn = sqlite3.connect("paper_trades.db")
    df = pd.read_sql_query("SELECT * FROM trades ORDER BY id DESC", conn)
    conn.close()
    return df

c_sel1, c_sel2 = st.columns([1.4, 1])
selected = c_sel1.selectbox("Select Active Chart", list(ASSETS.keys()), format_func=lambda k: f"{k} — {ASSETS[k]['label']}")
tf = c_sel2.selectbox("Candle Interval", ["5m", "15m", "1h"], index=1)

@st.fragment(run_every=1)
def live_terminal_stream():
    h1, h2 = st.columns([3, 1])
    with h1:
        st.markdown("### 🌐 Global 3-Asset AI Paper Terminal — USD (`$`)")
    with h2:
        utc_now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        st.markdown(f'<div style="text-align:right;padding-top:10px;"><span class="badge-247">● 1s LIVE FEED • {utc_now}</span></div>', unsafe_allow_html=True)

    cols = st.columns(3)
    live_prices = {}
    for idx, (tkr, info) in enumerate(ASSETS.items()):
        ltp, chg, pct = get_quote(tkr)
        live_prices[tkr] = ltp
        cls = "pos" if chg >= 0 else "neg"
        sign = "+" if chg >= 0 else ""
        with cols[idx]:
            st.markdown(f"""
            <div class="ticker-card">
                <div class="ticker-title">{info['label']} • {info['session']}</div>
                <div class="ticker-price">${ltp:,.2f}</div>
                <div class="{cls}">{sign}${chg:,.2f} ({sign}{pct:.2f}%)</div>
            </div>
            """, unsafe_allow_html=True)

    st.write("")
    trades_df = load_trades()
    realized_pnl = float(trades_df[trades_df["status"] == "CLOSED"]["pnl"].fillna(0).sum()) if not trades_df.empty and "pnl" in trades_df.columns else 0.0
    open_count = int((trades_df["status"] == "OPEN").sum()) if not trades_df.empty and "status" in trades_df.columns else 0
    capital = 1000.0 + realized_pnl

    m1, m2, m3 = st.columns(3)
    m1.metric("Paper Account Balance (USD)", f"${capital:,.2f}", f"${realized_pnl:+,.2f} Net P&L")
    m2.metric("Active Open Positions", f"{open_count} / 3", "AI Gate: > 65% Prob")
    m3.metric(f"{selected} Live Tick", f"${live_prices.get(selected, 0):,.2f}", "Refreshing every 1s")

    df = get_candles(selected, tf, "5d" if tf != "1h" else "1mo")
    if not df.empty and live_prices.get(selected, 0) > 0:
        df.iloc[-1, df.columns.get_loc("close")] = live_prices[selected]
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df["time"], open=df["open"], high=df["high"], low=df["low"], close=df["close"], name=selected))
    fig.add_trace(go.Scatter(x=df["time"], y=df["ema9"], line=dict(color="#38bdf8", width=1.4), name="EMA 9"))
    fig.add_trace(go.Scatter(x=df["time"], y=df["ema21"], line=dict(color="#f59e0b", width=1.4), name="EMA 21"))
    fig.update_layout(
        template="plotly_dark", height=430, margin=dict(l=10, r=10, t=20, b=10),
        xaxis_rangeslider_visible=False, paper_bgcolor="#0f141e", plot_bgcolor="#0f141e",
        yaxis_title="Price (USD $)", uirevision=f"{selected}-{tf}"
    )
    st.plotly_chart(fig, use_container_width=True, key="live_3asset_chart")

    st.markdown("#### 📋 Autonomous 24/7 Trade Ledger (USD)")
    if trades_df.empty:
        st.info("No trades logged yet. Cloud engine scans BTC-USD (24/7), GC=F, and SI=F continuously and enters when Time-Machine probability > 65%.")
    else:
        st.dataframe(trades_df, use_container_width=True)

live_terminal_stream()
