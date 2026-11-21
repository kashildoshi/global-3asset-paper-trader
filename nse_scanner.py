import requests
import pandas as pd
import numpy as np

ASSET_CONFIG = {
    "ADA-USD":  {"name": "Cardano (ADA)",        "risk_pct": 0.020, "atr_mult": 2.2, "rr": 1.35},
    "AVAX-USD": {"name": "Avalanche (AVAX)",     "risk_pct": 0.020, "atr_mult": 2.2, "rr": 1.35},
    "XAGT":     {"name": "Silver Token (XAGT)",  "risk_pct": 0.020, "atr_mult": 2.2, "rr": 1.35},
    "DOGE-USD": {"name": "Dogecoin (DOGE)",      "risk_pct": 0.020, "atr_mult": 2.2, "rr": 1.35},
    "SOL-USD":  {"name": "Solana (SOL)",         "risk_pct": 0.020, "atr_mult": 2.2, "rr": 1.35},
    "XAUT":     {"name": "Tether Gold (XAUT)",   "risk_pct": 0.020, "atr_mult": 2.2, "rr": 1.35},
    "ETH-USD":  {"name": "Ethereum (ETH)",       "risk_pct": 0.020, "atr_mult": 2.2, "rr": 1.35},
    "BTC-USD":  {"name": "Bitcoin (BTC)",        "risk_pct": 0.020, "atr_mult": 2.2, "rr": 1.35},
    "BNB-USD":  {"name": "BNB (BNB)",            "risk_pct": 0.020, "atr_mult": 2.2, "rr": 1.35},
    "XRP-USD":  {"name": "XRP (XRP)",            "risk_pct": 0.020, "atr_mult": 2.2, "rr": 1.35},
}

ASSETS = {k: v["name"] for k, v in ASSET_CONFIG.items()}

YAHOO_FEEDS = {
    "XAUT": ["XAUT-USD", "PAXG-USD"],
    "XAGT": ["XAGT-USD", "KAG-USD"],
}

def get_feed_candidates(ticker):
    return YAHOO_FEEDS.get(ticker, [ticker])

def fetch_candles(ticker, interval="5m", range_str="7d"):
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
    raise RuntimeError(f"No valid {interval} candles for {ticker}: {last_err}")

def add_indicators(df):
    df = df.copy()
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["ema200"] = df["close"].ewm(span=200).mean()

    sma20 = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std().replace(0, np.nan)
    df["bb_u"] = sma20 + 2.2 * std20
    df["bb_l"] = sma20 - 2.2 * std20

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

def evaluate_mtf_scalp(ticker, df_15m, df_5m):
    if len(df_15m) < 205 or len(df_5m) < 205:
        return None
    cfg = ASSET_CONFIG[ticker]

    m15 = df_15m.iloc[-1]
    b = df_5m.iloc[-1]
    p = df_5m.iloc[-2]

    cl = float(b["close"])
    atr = float(b["atr"])
    if np.isnan(atr) or atr <= 0 or (atr / cl) < 0.0025:
        return None

    vol_avg = max(float(df_5m["volume"].tail(20).iloc[:-1].mean()), 1.0)
    vol_surge = float(b["volume"]) >= vol_avg * 1.50
    if not vol_surge:
        return None

    # Gate 1 & 2: 15m Macro Trend + Strong ADX (>= 26)
    bull_15m = (m15["close"] > m15["ema50"] > m15["ema200"]) and (m15["adx"] >= 26.0)
    bear_15m = (m15["close"] < m15["ema50"] < m15["ema200"]) and (m15["adx"] >= 26.0)

    side = None
    skills = []

    # Gates 3–6: 5m Liquidity Sweep + Reversal Break + RSI Sweet Spot
    if bull_15m:
        bb_snap = (float(p["low"]) < float(p["bb_l"])) and (cl > float(b["bb_l"]))
        ema_spring = (float(p["low"]) <= float(p["ema20"])) and (cl > float(b["ema20"])) and (b["macd"] > b["macd_sig"])
        candle_confirm = (cl > float(b["open"])) and (cl > float(p["high"])) and (30.0 <= b["rsi14"] <= 46.0)
        if (bb_snap or ema_spring) and candle_confirm:
            side = "LONG"
            skills = [
                "S1:15m-EMA50>200",
                f"S2:15m-ADX({m15['adx']:.0f})",
                "S3:5m-BB-Sweep" if bb_snap else "S3:5m-EMA20-Spring",
                "S4:5m-Vol-1.5x",
                "S5:5m-PriorHigh-Break",
                f"S6:5m-RSI({b['rsi14']:.0f})"
            ]
    elif bear_15m:
        bb_snap = (float(p["high"]) > float(p["bb_u"])) and (cl < float(b["bb_u"]))
        ema_reject = (float(p["high"]) >= float(p["ema20"])) and (cl < float(b["ema20"])) and (b["macd"] < b["macd_sig"])
        candle_confirm = (cl < float(b["open"])) and (cl < float(p["low"])) and (54.0 <= b["rsi14"] <= 70.0)
        if (bb_snap or ema_reject) and candle_confirm:
            side = "SHORT"
            skills = [
                "S1:15m-EMA50<200",
                f"S2:15m-ADX({m15['adx']:.0f})",
                "S3:5m-BB-Reject" if bb_snap else "S3:5m-EMA20-Reject",
                "S4:5m-Vol-1.5x",
                "S5:5m-PriorLow-Break",
                f"S6:5m-RSI({b['rsi14']:.0f})"
            ]

    if not side:
        return None

    risk_dist = max(atr * cfg["atr_mult"], cl * 0.0045)
    target_dist = risk_dist * cfg["rr"]

    return {
        "symbol": ticker,
        "name": cfg["name"],
        "mode": "MTF-SCALP-5M",
        "risk_pct": cfg["risk_pct"],
        "rr": cfg["rr"],
        "side": side,
        "price": round(cl, 4),
        "stop": round(cl - risk_dist if side == "LONG" else cl + risk_dist, 4),
        "target": round(cl + target_dist if side == "LONG" else cl - target_dist, 4),
        "prob": 84.0,
        "reason": f"7-Gate Scalp ({cfg['rr']}R) | " + ", ".join(skills[:4])
    }

def scan_full_nse_universe(min_prob=80.0):
    signals = []
    for t in ASSETS:
        try:
            df_15m = add_indicators(fetch_candles(t, "15m", "15d")).iloc[:-1]
            df_5m = add_indicators(fetch_candles(t, "5m", "5d")).iloc[:-1]
            sig = evaluate_mtf_scalp(t, df_15m, df_5m)
            if sig and sig["prob"] >= min_prob:
                signals.append(sig)
        except Exception as e:
            print(f"Scan error on {t}: {e}")
    return sorted(signals, key=lambda x: x["prob"], reverse=True)
