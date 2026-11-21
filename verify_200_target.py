import pandas as pd
import nse_scanner

STARTING_EQUITY = 1000.0

def run_verification():
    print("Simulating Dual-Regime 10-Asset Portfolio with Live Compounding over 90 days...\n")
    data = {}
    for sym in nse_scanner.ASSETS:
        data[sym] = nse_scanner.add_indicators(nse_scanner.fetch_candles(sym, "1h", "90d")).reset_index(drop=True)

    # Build chronological event timeline across all 10 assets for true compounding equity
    all_times = sorted(set().union(*(df["time"].tolist() for df in data.values())))
    idx_map = {sym: {t: i for i, t in enumerate(df["time"])} for sym, df in data.items()}

    equity = STARTING_EQUITY
    open_pos = {}
    last_exit_time = {}
    closed_trades = []

    for ts in all_times:
        # 1. Check exits first
        for sym in list(open_pos.keys()):
            i = idx_map[sym].get(ts)
            if i is None:
                continue
            b = data[sym].iloc[i]
            hi, lo = float(b["high"]), float(b["low"])
            pos = open_pos[sym]
            pnl = None
            if pos["side"] == "LONG":
                if lo <= pos["stop"]: pnl = (pos["stop"] - pos["entry"]) * pos["qty"]
                elif hi >= pos["target"]: pnl = (pos["target"] - pos["entry"]) * pos["qty"]
            else:
                if hi >= pos["stop"]: pnl = (pos["entry"] - pos["stop"]) * pos["qty"]
                elif lo <= pos["target"]: pnl = (pos["entry"] - pos["target"]) * pos["qty"]

            if pnl is not None:
                equity += pnl
                closed_trades.append({"time": ts, "symbol": sym, "pnl": pnl})
                last_exit_time[sym] = ts
                del open_pos[sym]

        # 2. Check new entries (up to 5 concurrent slots)
        if len(open_pos) >= 5:
            continue
        for sym in nse_scanner.ASSETS:
            if sym in open_pos or len(open_pos) >= 5:
                continue
            if sym in last_exit_time and (ts - last_exit_time[sym]).total_seconds() < 7200:
                continue
            i = idx_map[sym].get(ts)
            if i is None or i < 205:
                continue
            sig = nse_scanner.evaluate_slice(sym, data[sym].iloc[i-204:i+1])
            if sig:
                risk_usd = equity * sig["risk_pct"]
                qty = risk_usd / abs(sig["price"] - sig["stop"])
                open_pos[sym] = {"side": sig["side"], "entry": sig["price"], "stop": sig["stop"], "target": sig["target"], "qty": qty}

    res = pd.DataFrame(closed_trades)
    wins = int((res["pnl"] > 0).sum())
    losses = int((res["pnl"] <= 0).sum())
    net_90d = float(res["pnl"].sum())
    est_60d = net_90d * (60.0 / 90.0)
    gw = float(res[res["pnl"] > 0]["pnl"].sum())
    gl = abs(float(res[res["pnl"] < 0]["pnl"].sum()))
    pf = gw / gl if gl > 0 else gw
    eq_series = STARTING_EQUITY + res["pnl"].cumsum()
    max_dd = (eq_series.cummax() - eq_series).max()

    print("=" * 74)
    print("DUAL-REGIME COMPOUNDING 10-ASSET VERIFICATION (90 DAYS)")
    print("=" * 74)
    print(f"Total Trades (90d)    : {len(res)}")
    print(f"Overall Win Rate      : {wins/len(res)*100:.1f}% ({wins}W / {losses}L)")
    print(f"90-Day Net Profit     : ${net_90d:+,.2f} (Ending Balance: ${STARTING_EQUITY + net_90d:,.2f})")
    print(f"60-Day (2-Mo) Pace    : ${est_60d:+,.2f}")
    print(f"Profit Factor         : {pf:.2f}x | Max Drawdown: -${max_dd:,.2f}")
    print("-" * 74)
    for sym, grp in res.groupby("symbol"):
        w = int((grp["pnl"] > 0).sum())
        mode = nse_scanner.ASSET_CONFIG[sym]["mode"]
        print(f"  {sym:<8} ({mode:<8}) | Trades: {len(grp):>3} | Win Rate: {w/len(grp)*100:>5.1f}% | 90d P&L: ${grp['pnl'].sum():>+8.2f}")
    print("=" * 74)

if __name__ == "__main__":
    run_verification()
