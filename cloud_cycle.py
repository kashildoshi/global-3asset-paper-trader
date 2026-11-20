import os, time, sqlite3, requests
from datetime import datetime, timezone
import nse_scanner

DB_PATH = "paper_trades.db"
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def send_tg(msg):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=5)
    except Exception:
        pass

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT, side TEXT, status TEXT, qty REAL,
        entry_price REAL, exit_price REAL, high_water REAL,
        final_stop REAL, target_price REAL, timemachine_prob REAL,
        entry_reason TEXT, exit_reason TEXT, entry_time TEXT, exit_time TEXT, pnl REAL
    )""")
    conn.commit()
    return conn

def get_live_price(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5).json()
    return float(r["chart"]["result"][0]["meta"]["regularMarketPrice"])

def run_cycle():
    conn = init_db()
    cur = conn.cursor()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # 1. Monitor & manage OPEN positions (24/7, no forced daily square-off)
    open_rows = cur.execute("SELECT id, symbol, side, qty, entry_price, high_water, final_stop, target_price FROM trades WHERE status='OPEN'").fetchall()
    for tid, sym, side, qty, entry, hw, stop, target in open_rows:
        try:
            ltp = get_live_price(sym)
        except Exception:
            continue
        if side == "LONG":
            new_hw = max(hw or entry, ltp)
            trail_stop = max(stop, round(new_hw - (entry - stop), 2)) if new_hw > entry else stop
            cur.execute("UPDATE trades SET high_water=?, final_stop=? WHERE id=?", (new_hw, trail_stop, tid))
            if ltp >= target or ltp <= trail_stop:
                reason = "Target Hit" if ltp >= target else "Stop-Loss Hit"
                pnl = round((ltp - entry) * qty, 2)
                cur.execute("UPDATE trades SET status='CLOSED', exit_price=?, exit_reason=?, exit_time=?, pnl=? WHERE id=?",
                            (ltp, reason, now_utc, pnl, tid))
                send_tg(f"🔔 *3-Asset Exit ({sym})*\nSide: `{side}` | Reason: *{reason}*\nEntry: `${entry:,.2f}` → Exit: `${ltp:,.2f}`\nNet P&L: `${pnl:+,.2f}`")
        else:
            new_hw = min(hw or entry, ltp)
            trail_stop = min(stop, round(new_hw + (stop - entry), 2)) if new_hw < entry else stop
            cur.execute("UPDATE trades SET high_water=?, final_stop=? WHERE id=?", (new_hw, trail_stop, tid))
            if ltp <= target or ltp >= trail_stop:
                reason = "Target Hit" if ltp <= target else "Stop-Loss Hit"
                pnl = round((entry - ltp) * qty, 2)
                cur.execute("UPDATE trades SET status='CLOSED', exit_price=?, exit_reason=?, exit_time=?, pnl=? WHERE id=?",
                            (ltp, reason, now_utc, pnl, tid))
                send_tg(f"🔔 *3-Asset Exit ({sym})*\nSide: `{side}` | Reason: *{reason}*\nEntry: `${entry:,.2f}` → Exit: `${ltp:,.2f}`\nNet P&L: `${pnl:+,.2f}`")
    conn.commit()

    # 2. Scan BTC-USD (24/7), GC=F, and SI=F for new setups (> 55% probability)
    open_syms = {r[1] for r in cur.execute("SELECT id, symbol FROM trades WHERE status='OPEN'").fetchall()}
    if len(open_syms) < 3:
        signals = nse_scanner.scan_full_nse_universe(min_prob=55.0)
        for sig in signals:
            if sig["symbol"] in open_syms or len(open_syms) >= 3:
                continue
            price = sig["price"]
            qty = round(250.0 / price, 6)  # $250 allocation per position out of $1,000 paper capital
            cur.execute("""
                INSERT INTO trades (symbol, side, status, qty, entry_price, high_water, final_stop, target_price, timemachine_prob, entry_reason, entry_time)
                VALUES (?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?)
            """, (sig["symbol"], sig["side"], qty, price, price, sig["stop"], sig["target"], sig["prob"], sig["reason"], now_utc))
            conn.commit()
            open_syms.add(sig["symbol"])
            send_tg(f"🚀 *New 3-Asset Trade ({sig['symbol']})*\nAction: *{sig['side']}* | Prob: *{sig['prob']}%*\nEntry: `${price:,.2f}` | SL: `${sig['stop']:,.2f}` | Target: `${sig['target']:,.2f}`\nReason: {sig['reason']}")
    conn.close()

if __name__ == "__main__":
    start = time.time()
    # Run continuous 5-second checks for 14.5 minutes per cron cycle (24/7, no holidays, no market close)
    while time.time() - start < 870:
        try:
            run_cycle()
        except Exception as e:
            print("Cycle warning:", e)
        time.sleep(5)
