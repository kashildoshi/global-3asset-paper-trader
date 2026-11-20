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
.insight-box {
    background: linear-gradient(145deg, #131824, #0f141e);
    border: 1px solid #222b3d;
    border-left: 4px solid #38bdf8;
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 10px;
}
.insight-title { font-size: 13px; font-weight: 800; color: #38bdf8; letter-spacing: 0.5px; margin-bottom: 6px; }
.insight-item { font-size: 13px; color: #d0d7de; margin: 4px 0; }
</style>
""", unsafe_allow_html=True)

STARTING_CAPITAL = 1000.0

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
    try:
        conn = sqlite3.connect("paper_trades.db")
        df = pd.read_sql_query("SELECT * FROM trades ORDER BY id DESC", conn)
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()

def enrich_trades_with_pnl(df, quotes):
    if df.empty:
        return df
    df = df.copy()
    mark_prices = []
    trade_pnls = []
    pnl_pcts = []
    notionals = []

    for _, row in df.iterrows():
        sym = row.get("symbol", "")
        side = str(row.get("side", "LONG")).upper()
        status = str(row.get("status", "OPEN")).upper()
        qty = float(row.get("qty") or 0.0)
        entry = float(row.get("entry_price") or 0.0)
        notional = entry * qty
        notionals.append(round(notional, 2))

        if status == "CLOSED":
            exit_p = float(row.get("exit_price") or entry)
            realized = row.get("pnl")
            if pd.isna(realized):
                realized = (exit_p - entry) * qty if side == "LONG" else (entry - exit_p) * qty
            realized = float(realized)
            mark_prices.append(round(exit_p, 2))
            trade_pnls.append(round(realized, 2))
            pnl_pcts.append(round((realized / notional * 100.0) if notional > 0 else 0.0, 2))
        else:
            live_p = float(quotes.get(sym, {}).get("ltp") or entry)
            if live_p <= 0:
                live_p = entry
            unrealized = (live_p - entry) * qty if side == "LONG" else (entry - live_p) * qty
            mark_prices.append(round(live_p, 2))
            trade_pnls.append(round(unrealized, 2))
            pnl_pcts.append(round((unrealized / notional * 100.0) if notional > 0 else 0.0, 2))

    df["notional_usd"] = notionals
    df["current_or_exit"] = mark_prices
    df["net_pnl_usd"] = trade_pnls
    df["return_pct"] = pnl_pcts
    return df

# 1. Zero-Buffering Client-Side JS Live Ticker (Updates ONLY price digits every 1s)
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
  .card {{ background: linear-gradient(145deg, #131824, #0f141e); border: 1px solid #222b3d; border-radius: 12px; padding: 14px 18px; }}
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
    <div class="card">
      <div class="card-title">BITCOIN (BTC/USD) • 24/7 NON-STOP CRYPTO</div>
      <div class="price" id="p-BTC-USD">$0.00</div>
      <div class="chg pos" id="c-BTC-USD">+0.00 (0.00%)</div>
    </div>
    <div class="card">
      <div class="card-title">GOLD FUTURES (XAU) • 24/5 COMEX GLOBEX</div>
      <div class="price" id="p-GC=F">$0.00</div>
      <div class="chg pos" id="c-GC=F">+0.00 (0.00%)</div>
    </div>
    <div class="card">
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
""", height=160)

# 2. Compute Overall & Per-Trade P&L
raw_trades = load_trades()
trades_df = enrich_trades_with_pnl(raw_trades, quote_state["quotes"])

if not trades_df.empty:
    closed_df = trades_df[trades_df["status"] == "CLOSED"]
    open_df = trades_df[trades_df["status"] == "OPEN"]
    realized_pnl = float(closed_df["net_pnl_usd"].sum())
    unrealized_pnl = float(open_df["net_pnl_usd"].sum())
    overall_pnl = realized_pnl + unrealized_pnl
    total_equity = STARTING_CAPITAL + overall_pnl
    overall_pct = (overall_pnl / STARTING_CAPITAL) * 100.0

    wins = int((closed_df["net_pnl_usd"] > 0).sum())
    losses = int((closed_df["net_pnl_usd"] < 0).sum())
    win_rate = (wins / len(closed_df) * 100.0) if len(closed_df) > 0 else 0.0
    gross_profit = float(closed_df[closed_df["net_pnl_usd"] > 0]["net_pnl_usd"].sum())
    gross_loss = abs(float(closed_df[closed_df["net_pnl_usd"] < 0]["net_pnl_usd"].sum()))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)
else:
    closed_df = pd.DataFrame()
    open_df = pd.DataFrame()
    realized_pnl = 0.0
    unrealized_pnl = 0.0
    overall_pnl = 0.0
    total_equity = STARTING_CAPITAL
    overall_pct = 0.0
    wins = losses = 0
    win_rate = 0.0
    profit_factor = 0.0

# 3. Overall P&L & Account KPI Strip
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total Account Equity", f"${total_equity:,.2f}", f"{overall_pct:+.2f}% Overall Return")
k2.metric("Overall Net P&L", f"${overall_pnl:+,.2f}", f"Realized: ${realized_pnl:+,.2f}")
k3.metric("Open Floating P&L", f"${unrealized_pnl:+,.2f}", f"{len(open_df)} / 3 Active Positions")
k4.metric("Closed Win Rate", f"{win_rate:.1f}%", f"{wins}W / {losses}L ({len(closed_df)} Closed)")
k5.metric("Profit Factor", f"{profit_factor:.2f}x", "AI Gate: >= 55% Prob")

st.write("")

# 4. Chart Controls & Live Candlestick Chart
c1, c2, c3 = st.columns([1.5, 1.0, 0.6])
selected = c1.selectbox("Active Chart Asset", list(ASSETS.keys()), format_func=lambda k: f"{k} — {ASSETS[k]['label']}")
tf = c2.selectbox("Candle Interval", ["5m", "15m", "1h"], index=0)
with c3:
    st.write("")
    if st.button("🔄 Refresh P&L & Chart", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

df = get_candles(selected, tf, "5d" if tf != "1h" else "1mo")
fig = go.Figure()
fig.add_trace(go.Candlestick(x=df["time"], open=df["open"], high=df["high"], low=df["low"], close=df["close"], name=selected))
fig.add_trace(go.Scatter(x=df["time"], y=df["ema9"], line=dict(color="#38bdf8", width=1.4), name="EMA 9"))
fig.add_trace(go.Scatter(x=df["time"], y=df["ema21"], line=dict(color="#f59e0b", width=1.4), name="EMA 21"))
fig.update_layout(
    template="plotly_dark", height=390, margin=dict(l=10, r=10, t=15, b=10),
    xaxis_rangeslider_visible=False, paper_bgcolor="#0f141e", plot_bgcolor="#0f141e",
    yaxis_title="Price (USD $)"
)
st.plotly_chart(fig, use_container_width=True)

# 5. AI Trading Insights & Equity Curve Section
st.markdown("### 🧠 AI Strategy Insights & Performance Analytics")
ins_col1, ins_col2 = st.columns([1.15, 1.0])

with ins_col1:
    if trades_df.empty:
        st.markdown("""
        <div class="insight-box">
            <div class="insight-title">LIVE ENGINE STATUS & MARKET INSIGHTS</div>
            <div class="insight-item">• <b>Capital Allocation:</b> $1,000.00 USD total ($250.00 per trade, max 3 concurrent slots).</div>
            <div class="insight-item">• <b>Entry Filter:</b> 5m EMA 9/21 trend alignment + RSI momentum confirmation clearing <b>≥ 55%</b> probability.</div>
            <div class="insight-item">• <b>Risk Management:</b> ATR trailing stop-loss + automatic 2:1 Reward-to-Risk target on every trade.</div>
            <div class="insight-item">• <b>Sync Tip:</b> Run <code>git pull origin main</code> after receiving a Telegram alert to load new cloud trades here.</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        asset_perf = trades_df.groupby("symbol").agg(
            Trades=("id", "count"),
            Wins=("net_pnl_usd", lambda s: int((s > 0).sum())),
            Net_PnL=("net_pnl_usd", "sum"),
            Avg_PnL=("net_pnl_usd", "mean")
        ).reset_index()
        asset_perf["Win_Rate_%"] = (asset_perf["Wins"] / asset_perf["Trades"] * 100.0).round(1)
        asset_perf["Net_PnL"] = asset_perf["Net_PnL"].round(2)
        asset_perf["Avg_PnL"] = asset_perf["Avg_PnL"].round(2)

        best_row = asset_perf.sort_values("Net_PnL", ascending=False).iloc[0]
        long_pnl = float(trades_df[trades_df["side"] == "LONG"]["net_pnl_usd"].sum())
        short_pnl = float(trades_df[trades_df["side"] == "SHORT"]["net_pnl_usd"].sum())
        best_trade = trades_df.sort_values("net_pnl_usd", ascending=False).iloc[0]
        worst_trade = trades_df.sort_values("net_pnl_usd", ascending=True).iloc[0]

        st.markdown(f"""
        <div class="insight-box">
            <div class="insight-title">AUTOMATED PORTFOLIO INSIGHTS</div>
            <div class="insight-item">• <b>Top Performing Asset:</b> <b>{best_row['symbol']}</b> with <b>${best_row['Net_PnL']:+,.2f}</b> net P&L ({best_row['Win_Rate_%']}% win rate across {int(best_row['Trades'])} trades).</div>
            <div class="insight-item">• <b>Directional Edge:</b> LONG P&L: <b>${long_pnl:+,.2f}</b> | SHORT P&L: <b>${short_pnl:+,.2f}</b>.</div>
            <div class="insight-item">• <b>Best / Worst Trade:</b> Best <b>#{int(best_trade['id'])} {best_trade['symbol']}</b> (<b>${best_trade['net_pnl_usd']:+,.2f}</b>) vs. Worst <b>#{int(worst_trade['id'])} {worst_trade['symbol']}</b> (<b>${worst_trade['net_pnl_usd']:+,.2f}</b>).</div>
            <div class="insight-item">• <b>Active Exposure:</b> ${len(open_df) * 250:,.2f} deployed across {len(open_df)} open position(s); ${max(0.0, 1000.0 - len(open_df) * 250):,.2f} free margin available.</div>
        </div>
        """, unsafe_allow_html=True)

        st.dataframe(
            asset_perf.style.format({"Net_PnL": "${:+,.2f}", "Avg_PnL": "${:+,.2f}", "Win_Rate_%": "{:.1f}%"}),
            use_container_width=True,
            hide_index=True
        )

with ins_col2:
    chron = trades_df.sort_values("id", ascending=True).copy() if not trades_df.empty else pd.DataFrame()
    if not chron.empty:
        chron["cum_equity"] = STARTING_CAPITAL + chron["net_pnl_usd"].cumsum()
        eq_x = ["Start"] + [f"#{int(i)} {s}" for i, s in zip(chron["id"], chron["symbol"])]
        eq_y = [STARTING_CAPITAL] + chron["cum_equity"].tolist()
    else:
        eq_x = ["Start", "Now"]
        eq_y = [STARTING_CAPITAL, STARTING_CAPITAL]

    eq_fig = go.Figure()
    eq_fig.add_trace(go.Scatter(
        x=eq_x, y=eq_y, mode="lines+markers",
        line=dict(color="#00e676" if eq_y[-1] >= STARTING_CAPITAL else "#ff5252", width=2.5),
        fill="tozeroy", fillcolor="rgba(0, 230, 118, 0.08)" if eq_y[-1] >= STARTING_CAPITAL else "rgba(255, 82, 82, 0.08)",
        name="Equity ($)"
    ))
    eq_fig.update_layout(
        title="Cumulative Account Equity Curve (USD)",
        template="plotly_dark", height=270, margin=dict(l=10, r=10, t=35, b=10),
        paper_bgcolor="#0f141e", plot_bgcolor="#0f141e",
        yaxis_title="Equity ($)"
    )
    st.plotly_chart(eq_fig, use_container_width=True)

# 6. Per-Trade P&L & Rationale Ledger (Every Single Trade)
st.markdown("### 📋 Trade-by-Trade P&L & Execution Ledger (USD)")
if trades_df.empty:
    st.info("No trades in local ledger yet. Once GitHub Actions executes a trade and you run `git pull origin main`, every open and closed trade's individual P&L ($ and %) and AI rationale will appear here.")
else:
    display_cols = [
        "id", "symbol", "side", "status", "entry_price", "current_or_exit",
        "qty", "net_pnl_usd", "return_pct", "final_stop", "target_price",
        "timemachine_prob", "entry_reason", "exit_reason", "entry_time"
    ]
    avail_cols = [c for c in display_cols if c in trades_df.columns]
    view_df = trades_df[avail_cols].rename(columns={
        "id": "ID",
        "symbol": "Asset",
        "side": "Side",
        "status": "Status",
        "entry_price": "Entry ($)",
        "current_or_exit": "Mark / Exit ($)",
        "qty": "Qty",
        "net_pnl_usd": "Trade P&L ($)",
        "return_pct": "Return (%)",
        "final_stop": "Stop-Loss ($)",
        "target_price": "Target ($)",
        "timemachine_prob": "AI Prob (%)",
        "entry_reason": "Entry Rationale",
        "exit_reason": "Exit Reason",
        "entry_time": "Entry Time (UTC)"
    })

    def color_pnl(val):
        if isinstance(val, (int, float)):
            if val > 0:
                return "color: #00e676; font-weight: 700;"
            elif val < 0:
                return "color: #ff5252; font-weight: 700;"
        return ""

    styled_ledger = (
        view_df.style
        .map(color_pnl, subset=["Trade P&L ($)", "Return (%)"])
        .format({
            "Entry ($)": "${:,.2f}",
            "Mark / Exit ($)": "${:,.2f}",
            "Qty": "{:.6f}",
            "Trade P&L ($)": "${:+,.2f}",
            "Return (%)": "{:+.2f}%",
            "Stop-Loss ($)": "${:,.2f}",
            "Target ($)": "${:,.2f}",
            "AI Prob (%)": "{:.1f}%"
        })
    )
    st.dataframe(styled_ledger, use_container_width=True, hide_index=True)
