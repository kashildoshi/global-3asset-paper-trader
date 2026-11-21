import requests
import pandas as pd
import numpy as np

ASSET_CONFIG = {
    "ADA-USD":  {"name": "Cardano (ADA)",        "mode": "SNAPBACK", "risk_pct": 0.032, "atr_mult": 2.1, "rr": 1.35},
    "AVAX-USD": {"name": "Avalanche (AVAX)",     "mode": "SNAPBACK", "risk_pct": 0.032, "atr_mult": 2.1, "rr": 1.35},
    "XAGT":     {"name": "Silver Token (XAGT)",  "mode": "SNAPBACK", "risk_pct": 0.032, "atr_mult": 2.1, "rr": 1.35},
    "DOGE-USD": {"name": "Dogecoin (DOGE)",      "mode": "SNAPBACK", "risk_pct": 0.030, "atr_mult": 2.1, "rr": 1.30},
    "SOL-USD":  {"name": "Solana (SOL)",         "mode": "SNAPBACK", "risk_pct": 0.028, "atr_mult": 2.2, "rr": 1.25},
    "XAUT":     {"name": "Tether Gold (XAUT)",   "mode": "SNAPBACK", "risk_pct": 0.025, "atr_mult": 2.2, "rr": 1.25},
    "ETH-USD":  {"name": "Ethereum (ETH)",       "mode": "SNAPBACK", "risk_pct": 0.022, "atr_mult": 2.3, "rr": 1.25},
    "BTC-USD":  {"name": "Bitcoin (BTC)",        "mode": "SNAPBACK", "risk_pct": 0.022, "atr_mult": 2.3, "rr": 1.25},
    "BNB-USD":  {"name": "BNB (BNB)",            "mode": "MOMENTUM", "risk_pct": 0.022, "atr_mult": 2.2, "rr": 1.45},
    "XRP-USD":  {"name": "XRP (XRP)",            "mode": "MOMENTUM", "risk_pct": 0.022, "atr_mult": 2.2, "rr": 1.45},
}

ASSETS = {k: v["name"] for k, v in ASSET_CONFIG.items()}

# Primary 24/7 token tickers with 1oz physical-backed fallback if Yahoo lacks 1h bars
YAHOO_FEEDS = {
    "XAUT": ["XAUT-USD", "PAXG-USD"],
    "XAGT": ["XAGT-USD", "KAG-USD"],
}

def get_feed_candidates(ticker):
    return YAHOO_FEEDS.get(ticker, [ticker])

def fetch_candles(ticker, interval="1h", range_str="30d"):
    last_err = None
    for yf_ticker in get_feed_candidates(ticker):
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}?interval={interval}&range={range_str}"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8).json()
            results = r.get("chart", {}).get("result")
            if not results:
                continue
            res = results[0]
            q = res["indicators"]["quote"][0]
            df = pd.DataFrame({
                "time": pd.to_datetime(res["timestamp"], unit="s", utc=True),
                "open": q["open"], "high": q["high"], "low": q["low"],
                "close": q["close"], "volume": q["volume"]
            }).dropna(subset=["open", "high", "low", "close"])
            if len(df) >= 205:
                df["volume"] = df["volume"].fillna(1.0)
                return df
        except Exception as e:
            last_err = e
    raise RuntimeError(f"No valid 24/7 candles for {ticker}: {last_err}")

def add_indicators(df):
    df = df.copy()
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["ema200"] = df["close"].ewm(span=200).mean()

    sma20 = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std().replace(0, np.nan)
    df["bb_u"] = sma20 + 2.0 * std20
    df["bb_l"] = sma20 - 2.0 * std20

    d = df["close"].diff()
    g = d.clip(lower=0).rolling(14).mean()
    l = (-d.clip(upper=0)).rolling(14).mean()
    df["rsi14"] = (100 - (100 / (1 + g / l.replace(0, np.nan)))).fillna(50.0)

    ema12 = df["close"].ewm(span=12).mean()
    ema26 = df["close"].ewm(span=26).mean()
    df["macd"] = ema12 - ema26
    df["macd_sig"] = df["macd"].ewm(span=9).mean()

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    up_m = df["high"].diff()
    dn_m = -df["low"].diff()
    pdm = np.where((up_m > dn_m) & (up_m > 0), up_m, 0.0)
    mdm = np.where((dn_m > up_m) & (dn_m > 0), dn_m, 0.0)
    pdi = 100 * pd.Series(pdm, index=df.index).rolling(14).mean() / df["atr"].replace(0, np.nan)
    mdi = 100 * pd.Series(mdm, index=df.index).rolling(14).mean() / df["atr"].replace(0, np.nan)
    dx = (100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)).fillna(0)
    df["adx"] = dx.rolling(14).mean().fillna(0)
    return df

def evaluate_slice(ticker, df):
    if len(df) < 205:
        return None
    cfg = ASSET_CONFIG[ticker]
    b = df.iloc[-1]
    p = df.iloc[-2]
    cl = float(b["close"])
    atr = float(b["atr"])
    if np.isnan(atr) or atr <= 0:
        return None

    side = None
    skills = []

    if cfg["mode"] == "SNAPBACK":
        if cl > b["ema200"] and b["ema50"] > b["ema200"] and float(p["low"]) < float(p["bb_l"]) and cl > float(b["bb_l"]) and cl > float(b["open"]):
            side = "LONG"
            skills = ["S1:Above-EMA200", "S2:EMA50>200", "S3:Lower-BB-Pierce", "S4:Reversal-Snap-In"]
            if b["rsi14"] < 47.0: skills.append("S5:RSI-Recovery")
            if cl > p["high"]: skills.append("S6:Prior-High-Break")
        elif cl < b["ema200"] and b["ema50"] < b["ema200"] and float(p["high"]) > float(p["bb_u"]) and cl < float(b["bb_u"]) and cl < float(b["open"]):
            side = "SHORT"
            skills = ["S1:Below-EMA200", "S2:EMA50<200", "S3:Upper-BB-Pierce", "S4:Reversal-Snap-In"]
            if b["rsi14"] > 53.0: skills.append("S5:RSI-Reject")
            if cl < p["low"]: skills.append("S6:Prior-Low-Break")
    else:
        vol_avg = max(float(df["volume"].tail(20).mean()), 1.0)
        if b["adx"] >= 24.0 and float(b["volume"]) >= vol_avg * 1.15:
            if cl > b["ema200"] and b["ema20"] > b["ema50"] > b["ema200"] and p["low"] <= p["ema20"] and cl > b["ema20"] and cl > p["high"] and b["macd"] > b["macd_sig"] and 50.0 <= b["rsi14"] <= 68.0:
                side = "LONG"
                skills = ["S1:EMA20>50>200", "S2:ADX>=24", "S3:EMA20-Spring", "S4:MACD-Bull", "S5:Vol-Surge", "S6:Breakout-Close"]
            elif cl < b["ema200"] and b["ema20"] < b["ema50"] < b["ema200"] and p["high"] >= p["ema20"] and cl < b["ema20"] and cl < p["low"] and b["macd"] < b["macd_sig"] and 32.0 <= b["rsi14"] <= 50.0:
                side = "SHORT"
                skills = ["S1:EMA20<50<200", "S2:ADX>=24", "S3:EMA20-Reject", "S4:MACD-Bear", "S5:Vol-Surge", "S6:Breakdown-Close"]

    if not side:
        return None

    risk_dist = max(atr * cfg["atr_mult"], cl * 0.008)
    target_dist = risk_dist * cfg["rr"]
    prob = round(min(63.0 + len(skills) * 4.5, 93.0), 1)

    return {
        "symbol": ticker,
        "name": cfg["name"],
        "mode": cfg["mode"],
        "risk_pct": cfg["risk_pct"],
        "rr": cfg["rr"],
        "side": side,
        "price": round(cl, 4),
        "stop": round(cl - risk_dist if side == "LONG" else cl + risk_dist, 4),
        "target": round(cl + target_dist if side == "LONG" else cl - target_dist, 4),
        "prob": prob,
        "reason": f"{cfg['mode']} ({len(skills)}/6 | {cfg['rr']}R) | " + ", ".join(skills[:4])
    }

def scan_full_nse_universe(min_prob=65.0):
    signals = []
    for t in ASSETS:
        try:
            df = add_indicators(fetch_candles(t, "1h", "30d")).iloc[:-1]
            sig = evaluate_slice(t, df)
            if sig and sig["prob"] >= min_prob:
                signals.append(sig)
        except Exception as e:
            print(f"Scan error on {t}: {e}")
    return sorted(signals, key=lambda x: x["prob"], reverse=True)
