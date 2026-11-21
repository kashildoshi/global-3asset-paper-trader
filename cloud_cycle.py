import os, time, sqlite3, requests
from datetime import datetime, timezone
import nse_scanner

DB_PATH = "paper_trades.db"
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
COOLDOWN_SECONDS = 1800  # 30-minute cooldown per asset after closing a trade

def send_tg(msg):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=5
        )
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

def is_in_cooldown(cur, symbol):
    row = cur.execute("SELECT exit_time FROM trades WHERE symbol=? AND status='CLOSED' ORDER BY id DESC LIMIT 1", (symbol,)).fetchone()
    if not row or not row[0]:
        return False
    try:
        last_exit = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last_exit).total_seconds() < COOLDOWN_SECONDS
    except Exception:
        return False

def run_cycle(log_status=False):
    conn = init_db()
    cur = conn.cursor()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    open_rows = cur.execute("SELECT id, symbol, side, qty, entry_price, high_water, final_stop, target_price FROM trades WHERE status='OPEN'").fetchall()
    for tid, sym, side, qty, entry, hw, stop, target in open_rows:
        try:
            ltp = get_live_price(sym)
        except Exception:
            continue
        init_risk = abs(entry - stop)
        if side == "LONG":
            new_hw = max(hw or entry, ltp)
            # Only trail stop after trade moves at least +1R in profit
            if (new_hw - entry) >= init_risk:
                trail_stop = max(stop, round(new_hw - init_risk * 0.8, 2))
            else:
                trail_stop = stop
            cur.execute("UPDATE trades SET high_water=?, final_stop=? WHERE id=?", (new_hw, trail_stop, tid))
            if ltp >= target or ltp <= trail_stop:
                reason = "Target Hit" if ltp >= target else ("Trailing Lock" if trail_stop > entry else "Stop-Loss Hit")
                pnl = round((ltp - entry) * qty, 2)
                cur.execute("UPDATE trades SET status='CLOSED', exit_price=?, exit_reason=?, exit_time=?, pnl=? WHERE id=?",
                            (ltp, reason, now_utc, pnl, tid))
                send_tg(f"🔔 *3-Asset Exit ({sym})*\nSide: `{side}` | Reason: *{reason}*\nEntry: `${entry:,.2f}` → Exit: `${ltp:,.2f}`\nNet P&L: `${pnl:+,.2f}`")
        else:
            new_hw = min(hw or entry, ltp)
            if (entry - new_hw) >= init_risk:
                trail_stop = min(stop, round(new_hw + init_risk * 0.8, 2))
            else:
                trail_stop = stop
            cur.execute("UPDATE trades SET high_water=?, final_stop=? WHERE id=?", (new_hw, trail_stop, tid))
            if ltp <= target or ltp >= trail_stop:
                reason = "Target Hit" if ltp <= target else ("Trailing Lock" if trail_stop < entry else "Stop-Loss Hit")
                pnl = round((entry - ltp) * qty, 2)
                cur.execute("UPDATE trades SET status='CLOSED', exit_price=?, exit_reason=?, exit_time=?, pnl=? WHERE id=?",
                            (ltp, reason, now_utc, pnl, tid))
                send_tg(f"🔔 *3-Asset Exit ({sym})*\nSide: `{side}` | Reason: *{reason}*\nEntry: `${entry:,.2f}` → Exit: `${ltp:,.2f}`\nNet P&L: `${pnl:+,.2f}`")
    conn.commit()

    open_syms = {r[1] for r in cur.execute("SELECT id, symbol FROM trades WHERE status='OPEN'").fetchall()}
    signals = nse_scanner.scan_full_nse_universe(min_prob=68.0)
    if log_status:
        print(f"[{now_utc}] Open={list(open_syms)} | 11-Skill Signals={[(s['symbol'], s['side'], s['prob'], s['reason']) for s in signals]}", flush=True)

    if len(open_syms) < 3:
        for sig in signals:
            if sig["symbol"] in open_syms or len(open_syms) >= 3:
                continue
            if is_in_cooldown(cur, sig["symbol"]):
                continue
            price = sig["price"]
            qty = round(250.0 / price, 6)
            cur.execute("""
                INSERT INTO trades (symbol, side, status, qty, entry_price, high_water, final_stop, target_price, timemachine_prob, entry_reason, entry_time)
                VALUES (?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?)
            """, (sig["symbol"], sig["side"], qty, price, price, sig["stop"], sig["target"], sig["prob"], sig["reason"], now_utc))
            conn.commit()
            open_syms.add(sig["symbol"])
            send_tg(f"🚀 *New 11-Skill Trade ({sig['symbol']})*\nAction: *{sig['side']}* | Prob: *{sig['prob']}%*\nEntry: `${price:,.2f}` | SL: `${sig['stop']:,.2f}` | Target: `${sig['target']:,.2f}`\nSkills: {sig['reason']}")
    conn.close()

if __name__ == "__main__":
    start = time.time()
    step = 0
    while time.time() - start < 870:
        try:
            run_cycle(log_status=(step % 12 == 0))
        except Exception as e:
            print("Cycle warning:", e, flush=True)
        step += 1
        time.sleep(10)
