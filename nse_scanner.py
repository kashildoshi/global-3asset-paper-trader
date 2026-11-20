import requests
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

def scan_full_nse_universe(min_prob=55.0):
    signals = []
    for t in ASSETS:
        try:
            sig = evaluate_asset(t)
            if sig and sig["prob"] >= min_prob:
                signals.append(sig)
        except Exception:
            continue
    return sorted(signals, key=lambda x: x["prob"], reverse=True)
