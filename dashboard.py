import os, json, time, sqlite3, threading, requests
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import plotly.graph_objects as go

st.set_page_config(page_title="Global 3-Asset Pro — BTC • Gold • Silver", layout="wide")

st.markdown("""
<style>
.stApp { background-color: #0b0e14; color: #e6edf3; }
header[data-testid="stHeader"] { background: transparent; }
div[data-testid="stStatusWidget"] { display: none !important; }
</style>
""", unsafe_allow_html=True)

ASSETS = {
    "BTC-USD": {"label": "BITCOIN (BTC/USD)", "session": "24/7 NON-STOP CRYPTO"},
    "GC=F":    {"label": "GOLD FUTURES (XAU)", "session": "24/5 COMEX GLOBEX"},
    "SI=F":    {"label": "SILVER FUTURES (XAG)", "session": "24/5 COMEX GLOBEX"},
}

@st.cache_resource
def start_quote_feed():
    state = {
        "quotes": {
            "BTC-USD": {"ltp": 0.0, "chg": 0.0, "pct": 0.0},
            "GC=F":    {"ltp": 0.0, "chg": 0.0, "pct": 0.0},
            "SI=F":    {"ltp": 0.0, "chg": 0.0, "pct": 0.0},
        }
    }

    def fetch_one(ticker):
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=2d"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3).json()
            meta = r["chart"]["result"][0]["meta"]
            ltp = float(meta.get("regularMarketPrice", 0))
            prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or ltp)
            chg = ltp - prev
            pct = (chg / prev * 100.0) if prev else 0.0
            if ltp > 0:
                state["quotes"][ticker] = {"ltp": ltp, "chg": chg, "pct": pct}
        except Exception:
            pass

    def poll_loop():
        while True:
            for t in ASSETS:
                fetch_one(t)
            time.sleep(1.0)

    class QuoteHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(state["quotes"]).encode("utf-8"))
        def log_message(self, *args):
            pass

    for t in ASSETS:
        fetch_one(t)
    threading.Thread(target=poll_loop, daemon=True).start()
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", 8503), QuoteHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    except OSError:
        pass
    return state

quote_state = start_quote_feed()

@st.cache_data(ttl=15)
def get_candles(ticker, interval="15m", rng="5d"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval={interval}&range={rng}"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5).json()
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

# Smooth Client-Side JS Live Ticker (Updates ONLY price digits every 1s with zero page rerun)
init_json = json.dumps(quote_state["quotes"])
components.html(f"""
<!DOCTYPE html>
<html>
<head>
<style>
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: transparent; color: #e6edf3; }}
  .topbar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
  .title {{ font-size: 20px; font-weight: 800; color: #ffffff; }}
  .badge {{ background: rgba(0,230,118,0.14); color: #00e676; border: 1px solid rgba(0,230,118,0.4); padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 700; }}
  .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }}
  .card {{ background: linear-gradient(145deg, #131824, #0f141e); border: 1px solid #222b3d; border-radius: 12px; padding: 14px 18px; transition: border-color 0.25s; }}
  .card-title {{ font-size: 11px; letter-spacing: 0.8px; color: #8b949e; font-weight: 700; }}
  .price {{ font-size: 26px; font-weight: 800; color: #ffffff; margin: 5px 0; font-variant-numeric: tabular-nums; transition: color 0.2s; }}
  .chg {{ font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .pos {{ color: #00e676; }}
  .neg {{ color: #ff5252; }}
  .flash-up {{ color: #00e676 !important; }}
  .flash-dn {{ color: #ff5252 !important; }}
</style>
</head>
<body>
  <div class="topbar">
    <div class="title">🌐 Global 3-Asset AI Paper Terminal — USD ($)</div>
    <div class="badge" id="clock">● LIVE STREAM • --:--:-- UTC</div>
  </div>
  <div class="grid">
    <div class="card" id="card-BTC-USD">
      <div class="card-title">BITCOIN (BTC/USD) • 24/7 NON-STOP CRYPTO</div>
      <div class="price" id="p-BTC-USD">$0.00</div>
      <div class="chg pos" id="c-BTC-USD">+0.00 (0.00%)</div>
    </div>
    <div class="card" id="card-GC=F">
      <div class="card-title">GOLD FUTURES (XAU) • 24/5 COMEX GLOBEX</div>
      <div class="price" id="p-GC=F">$0.00</div>
      <div class="chg pos" id="c-GC=F">+0.00 (0.00%)</div>
    </div>
    <div class="card" id="card-SI=F">
      <div class="card-title">SILVER FUTURES (XAG) • 24/5 COMEX GLOBEX</div>
      <div class="price" id="p-SI=F">$0.00</div>
      <div class="chg pos" id="c-SI=F">+0.00 (0.00%)</div>
    </div>
  </div>
<script>
  let lastPrices = {{}};
  function fmtUSD(v) {{
    return '$' + Number(v).toLocaleString('en-US', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
  }}
  function renderQuotes(data) {{
    for (const [sym, q] of Object.entries(data)) {{
      const pEl = document.getElementById('p-' + sym);
      const cEl = document.getElementById('c-' + sym);
      if (!pEl || !cEl || !q.ltp) continue;
      const prev = lastPrices[sym];
      pEl.textContent = fmtUSD(q.ltp);
      if (prev !== undefined && q.ltp !== prev) {{
        pEl.classList.remove('flash-up', 'flash-dn');
        void pEl.offsetWidth;
        pEl.classList.add(q.ltp > prev ? 'flash-up' : 'flash-dn');
        setTimeout(() => pEl.classList.remove('flash-up', 'flash-dn'), 450);
      }}
      lastPrices[sym] = q.ltp;
      const sign = q.chg >= 0 ? '+' : '';
      cEl.textContent = `${{sign}}${{fmtUSD(q.chg)}} (${{sign}}${{q.pct.toFixed(2)}}%)`;
      cEl.className = 'chg ' + (q.chg >= 0 ? 'pos' : 'neg');
    }}
  }}
  function updateClock() {{
    const now = new Date();
    document.getElementById('clock').textContent = '● LIVE STREAM • ' + now.toISOString().substring(11, 19) + ' UTC';
  }}
  renderQuotes({init_json});
  updateClock();
  setInterval(updateClock, 1000);
  setInterval(async () => {{
    try {{
      const res = await fetch('http://127.0.0.1:8503/quotes', {{ cache: 'no-store' }});
      if (res.ok) renderQuotes(await res.json());
    }} catch (e) {{}}
  }}, 1000);
</script>
</body>
</html>
""", height=165)

# Static Account Summary & Controls (No 1s buffering)
trades_df = load_trades()
realized_pnl = float(trades_df[trades_df["status"] == "CLOSED"]["pnl"].fillna(0).sum()) if not trades_df.empty and "pnl" in trades_df.columns else 0.0
open_count = int((trades_df["status"] == "OPEN").sum()) if not trades_df.empty and "status" in trades_df.columns else 0
capital = 1000.0 + realized_pnl

c1, c2, c3, c4, c5 = st.columns([1.3, 0.9, 1, 1, 0.7])
selected = c1.selectbox("Active Chart Asset", list(ASSETS.keys()), format_func=lambda k: f"{k} — {ASSETS[k]['label']}")
tf = c2.selectbox("Candle Interval", ["5m", "15m", "1h"], index=1)
c3.metric("Paper Account Balance (USD)", f"${capital:,.2f}", f"${realized_pnl:+,.2f} Net P&L")
c4.metric("Active Open Positions", f"{open_count} / 3", "AI Gate: > 55% Prob")
with c5:
    st.write("")
    if st.button("🔄 Sync Chart"):
        st.cache_data.clear()
        st.rerun()

df = get_candles(selected, tf, "5d" if tf != "1h" else "1mo")
fig = go.Figure()
fig.add_trace(go.Candlestick(x=df["time"], open=df["open"], high=df["high"], low=df["low"], close=df["close"], name=selected))
fig.add_trace(go.Scatter(x=df["time"], y=df["ema9"], line=dict(color="#38bdf8", width=1.4), name="EMA 9"))
fig.add_trace(go.Scatter(x=df["time"], y=df["ema21"], line=dict(color="#f59e0b", width=1.4), name="EMA 21"))
fig.update_layout(
    template="plotly_dark", height=430, margin=dict(l=10, r=10, t=20, b=10),
    xaxis_rangeslider_visible=False, paper_bgcolor="#0f141e", plot_bgcolor="#0f141e",
    yaxis_title="Price (USD $)"
)
st.plotly_chart(fig, use_container_width=True)

st.markdown("#### 📋 Autonomous 24/7 Trade Ledger (USD)")
if trades_df.empty:
    st.info("No trades logged yet. Cloud engine scans BTC-USD (24/7), GC=F, and SI=F continuously and enters when Time-Machine probability > 55%.")
else:
    st.dataframe(trades_df, use_container_width=True)
