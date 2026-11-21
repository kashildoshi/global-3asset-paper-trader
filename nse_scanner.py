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
        "open": q["open"], "high": q["high"], "low": q["low"],
        "close": q["close"], "volume": q["volume"]
    }).dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(1.0)
    return df

def add_indicators(df):
    df = df.copy()
    df["ema9"] = df["close"].ewm(span=9).mean()
    df["ema21"] = df["close"].ewm(span=21).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = (100 - (100 / (1 + rs))).fillna(50.0)

    ema12 = df["close"].ewm(span=12).mean()
    ema26 = df["close"].ewm(span=26).mean()
    df["macd"] = ema12 - ema26
    df["macd_sig"] = df["macd"].ewm(span=9).mean()

    sma20 = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std().replace(0, np.nan)
    df["bb_upper"] = sma20 + 2 * std20
    df["bb_lower"] = sma20 - 2 * std20

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    up_move = df["high"].diff()
    dn_move = -df["low"].diff()
    plus_dm = np.where((up_move > dn_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((dn_move > up_move) & (dn_move > 0), dn_move, 0.0)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(14).mean() / df["atr"].replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(14).mean() / df["atr"].replace(0, np.nan)
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0)
    df["adx"] = dx.rolling(14).mean().fillna(0)
    return df

def evaluate_asset(ticker):
    df5 = add_indicators(fetch_candles(ticker, "5m", "5d"))
    df15 = add_indicators(fetch_candles(ticker, "15m", "1mo"))
    if len(df5) < 55 or len(df15) < 55:
        return None

    l5, p5 = df5.iloc[-1], df5.iloc[-2]
    l15 = df15.iloc[-1]
    price = float(l5["close"])
    atr = float(l5["atr"])
    if np.isnan(atr) or atr <= 0:
        return None

    # Skill 1: ADX Chop Filter (Reject dead/sideways markets)
    if float(l5["adx"]) < 20.0:
        return None

    # Skill 2: Volatility Regime Check (Reject micro-range noise)
    if (atr / price) < 0.0012:
        return None

    vol_avg = max(float(df5["volume"].tail(20).mean()), 1.0)
    vol_ratio = float(l5["volume"]) / vol_avg
    vwap_proxy = float((df5["close"].tail(24) * df5["volume"].tail(24)).sum() / max(df5["volume"].tail(24).sum(), 1.0))

    skills_passed = []
    side = None

    # Evaluate LONG across 11 Skills
    if l5["ema9"] > l5["ema21"] and price > l5["ema9"]:
        side = "LONG"
        skills_passed.append("S1:5m-EMA9>21")
        if price > l5["ema50"]: skills_passed.append("S2:Above-EMA50")
        if l15["ema9"] > l15["ema21"] and l15["close"] > l15["ema21"]: skills_passed.append("S3:15m-HTF-Bull")
        if 52.0 <= l5["rsi"] <= 68.0: skills_passed.append("S4:RSI-SweetSpot")
        if l5["macd"] > l5["macd_sig"]: skills_passed.append("S5:MACD-Bull")
        if price > vwap_proxy: skills_passed.append("S6:Above-VWAP")
        if vol_ratio >= 1.15: skills_passed.append("S7:Vol-Surge")
        if price < l5["bb_upper"]: skills_passed.append("S8:Not-BB-Overext")
        if price > p5["high"]: skills_passed.append("S9:Candle-Breakout")
        if l5["adx"] >= 25.0: skills_passed.append("S10:Strong-ADX")
        if l5["close"] > l5["open"]: skills_passed.append("S11:Green-Body")

    # Evaluate SHORT across 11 Skills
    elif l5["ema9"] < l5["ema21"] and price < l5["ema9"]:
        side = "SHORT"
        skills_passed.append("S1:5m-EMA9<21")
        if price < l5["ema50"]: skills_passed.append("S2:Below-EMA50")
        if l15["ema9"] < l15["ema21"] and l15["close"] < l15["ema21"]: skills_passed.append("S3:15m-HTF-Bear")
        if 32.0 <= l5["rsi"] <= 48.0: skills_passed.append("S4:RSI-SweetSpot")
        if l5["macd"] < l5["macd_sig"]: skills_passed.append("S5:MACD-Bear")
        if price < vwap_proxy: skills_passed.append("S6:Below-VWAP")
        if vol_ratio >= 1.15: skills_passed.append("S7:Vol-Surge")
        if price > l5["bb_lower"]: skills_passed.append("S8:Not-BB-Oversold")
        if price < p5["low"]: skills_passed.append("S9:Candle-Breakdown")
        if l5["adx"] >= 25.0: skills_passed.append("S10:Strong-ADX")
        if l5["close"] < l5["open"]: skills_passed.append("S11:Red-Body")

    if not side or "S3:15m-HTF-Bull" not in skills_passed and "S3:15m-HTF-Bear" not in skills_passed:
        return None

    # Require at least 7 of 11 skills aligned
    if len(skills_passed) < 7:
        return None

    prob = round(min(45.0 + len(skills_passed) * 4.5, 92.0), 1)
    stop_dist = max(atr * 2.0, price * 0.006)
    target_dist = stop_dist * 2.2

    return {
        "symbol": ticker,
        "name": ASSETS[ticker],
        "side": side,
        "price": round(price, 2),
        "stop": round(price - stop_dist if side == "LONG" else price + stop_dist, 2),
        "target": round(price + target_dist if side == "LONG" else price - target_dist, 2),
        "prob": prob,
        "reason": f"{len(skills_passed)}/11 Skills | " + ", ".join(skills_passed[:5])
    }

def scan_full_nse_universe(min_prob=68.0):
    signals = []
    for t in ASSETS:
        try:
            sig = evaluate_asset(t)
            if sig and sig["prob"] >= min_prob:
                signals.append(sig)
        except Exception as e:
            print(f"Scan error on {t}: {e}")
    return sorted(signals, key=lambda x: x["prob"], reverse=True)
