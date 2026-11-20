import os, sqlite3, re

# 1. Reset fresh USD paper_trades.db with full schema
if os.path.exists("paper_trades.db"):
    os.remove("paper_trades.db")
conn = sqlite3.connect("paper_trades.db")
conn.execute("""
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    side TEXT,
    status TEXT,
    qty REAL,
    entry_price REAL,
    exit_price REAL,
    high_water REAL,
    final_stop REAL,
    target_price REAL,
    timemachine_prob REAL,
    entry_reason TEXT,
    exit_reason TEXT,
    entry_time TEXT,
    exit_time TEXT,
    pnl REAL
)
""")
conn.commit()
conn.close()

# 2. Create clean 3-asset scanner (BTC-USD, GC=F, SI=F)
scanner_code = '''import requests
import pandas as pd
import numpy as np

ASSETS = {
    "BTC-USD": "Bitcoin (BTC)",
    "GC=F": "Gold Futures (XAU)",
    "SI=F": "Silver Futures (XAG)"
}

def fetch_candles(ticker, interval="5m", range_str="5d"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval={interval}&range={range_str}"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6).json()
    res = r["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({
        "open": q["open"],
        "high": q["high"],
        "low": q["low"],
        "close": q["close"],
        "volume": q["volume"]
    }).dropna()
    return df

def evaluate_asset(ticker):
    df = fetch_candles(ticker, "5m", "5d")
    if len(df) < 30:
        return None
    df["ema9"] = df["close"].ewm(span=9).mean()
    df["ema21"] = df["close"].ewm(span=21).mean()
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])

    last = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(last["close"])
    vol_ratio = float(last["volume"] / max(df["volume"].tail(20).mean(), 1))

    prob = 50.0
    side = None
    reasons = []

    if last["ema9"] > last["ema21"] and price > last["ema9"] and 52 <= last["rsi"] <= 72:
        side = "LONG"
        prob += 12.0 + min(vol_ratio * 4.0, 10.0)
        reasons.append(f"EMA 9/21 Bullish + RSI {last['rsi']:.1f}")
        if price > prev["high"]:
            prob += 5.0
            reasons.append("5m High Breakout")
    elif last["ema9"] < last["ema21"] and price < last["ema9"] and 28 <= last["rsi"] <= 48:
        side = "SHORT"
        prob += 12.0 + min(vol_ratio * 4.0, 10.0)
        reasons.append(f"EMA 9/21 Bearish + RSI {last['rsi']:.1f}")
        if price < prev["low"]:
            prob += 5.0
            reasons.append("5m Low Breakdown")

    if not side:
        return None

    stop_dist = max(atr * 1.5, price * 0.004)
    target_dist = stop_dist * 2.0
    return {
        "symbol": ticker,
        "name": ASSETS[ticker],
        "side": side,
        "price": round(price, 2),
        "stop": round(price - stop_dist if side == "LONG" else price + stop_dist, 2),
        "target": round(price + target_dist if side == "LONG" else price - target_dist, 2),
        "prob": round(min(prob, 92.0), 1),
        "reason": f"{'BUY->SELL' if side=='LONG' else 'SELL->BUY'} | " + " + ".join(reasons)
    }

def scan_full_nse_universe(min_prob=65.0):
    signals = []
    for t in ASSETS:
        try:
            sig = evaluate_asset(t)
            if sig and sig["prob"] >= min_prob:
                signals.append(sig)
        except Exception:
            continue
    return sorted(signals, key=lambda x: x["prob"], reverse=True)
'''
with open("nse_scanner.py", "w", encoding="utf-8") as f:
    f.write(scanner_code)

# 3. Remove NSE holiday lock so BTC (24/7) and Gold/Silver (24/5) run continuously
with open("nse_holidays.py", "w", encoding="utf-8") as f:
    f.write("def is_nse_trading_day(*args, **kwargs):\n    return True, '24/7 Global Crypto & Metals Session'\n")

# 4. Patch cloud_cycle.py for 24/7 USD trading (no 9:15-15:30 IST cutoff or 15:20 square-off)
if os.path.exists("cloud_cycle.py"):
    with open("cloud_cycle.py", "r", encoding="utf-8") as f:
        cc = f.read()
    cc = cc.replace("₹", "$").replace(".NS", "")
    cc = re.sub(r"if\s+not\s+is_open.*?:.*?return", "", cc, flags=re.S)
    cc = cc.replace("(15, 30)", "(23, 59)").replace("(15, 5)", "(23, 59)").replace("(15, 20)", "(23, 59)")
    cc = cc.replace("(9, 0)", "(0, 0)").replace("(9, 15)", "(0, 0)")
    with open("cloud_cycle.py", "w", encoding="utf-8") as f:
        f.write(cc)

# 5. Patch dashboard.py for BTC-USD, GC=F, SI=F in USD ($)
if os.path.exists("dashboard.py"):
    with open("dashboard.py", "r", encoding="utf-8") as f:
        db = f.read()
    db = db.replace("₹", "$").replace("NSE Alpha Terminal", "Global 3-Asset Terminal (BTC • Gold • Silver)")
    db = db.replace('"NIFTY 50": "%5ENSEI"', '"BITCOIN": "BTC-USD"').replace('"NIFTY BANK": "%5ENSEBANK"', '"GOLD": "GC=F"')
    db = db.replace("NIFTY 50", "BITCOIN (BTC)").replace("NIFTY BANK", "GOLD (XAU)")
    db = db.replace('f"{clean}.NS"', 'clean').replace('".NS"', '""')
    db = re.sub(r'\["RELIANCE".*?\]', '["BTC-USD", "GC=F", "SI=F"]', db, flags=re.S)
    with open("dashboard.py", "w", encoding="utf-8") as f:
        f.write(db)

# 6. Update workflow schedule to run continuously every 15 minutes (24/7)
wf_path = os.path.join(".github", "workflows", "nse_autonomous_bot.yml")
if os.path.exists(wf_path):
    with open(wf_path, "r", encoding="utf-8") as f:
        wf = f.read()
    wf = wf.replace("NSE Autonomous Paper Trader", "Global 3-Asset Paper Trader (BTC-Gold-Silver)")
    wf = re.sub(r"cron:.*", "cron: '*/15 * * * *'", wf)
    with open(wf_path, "w", encoding="utf-8") as f:
        f.write(wf)

print("3ASSET_USD_CLONE_READY")
