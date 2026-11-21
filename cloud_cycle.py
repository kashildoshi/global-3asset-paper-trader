import os, time, sqlite3, requests
from datetime import datetime, timezone
import nse_scanner

DB_PATH = "paper_trades.db"
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
STARTING_CAPITAL = 1000.0
MAX_OPEN_TRADES = 3
COOLDOWN_SECONDS = 14400  # 4-hour cooldown per symbol (~4-6 high-conviction scalps/day)

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

def get_current_equity(cur):
    row = cur.execute("SELECT COALESCE(SUM(pnl), 0.0) FROM trades WHERE status='CLOSED'").fetchone()
    return max(STARTING_CAPITAL + float(row[0] or 0.0), 200.0)

def get_live_price(ticker):
    for yf_ticker in nse_scanner.get_feed_candidates(ticker):
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}?interval=1m&range=1d"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5).json()
            results = r.get("chart", {}).get("result")
            if not results:
                continue
            price = results[0]["meta"].get("regularMarketPrice")
            if price is not None:
                return float(price)
        except Exception:
            continue
    raise RuntimeError(f"Live price unavailable for {ticker}")

def is_in_cooldown(cur, symbol):
    row = cur.execute("SELECT exit_time FROM trades WHERE symbol=? AND status='CLOSED' ORDER BY id DESC LIMIT 1", (symbol,)).fetchone()
    if not row or not row[0]:
        return False
    try:
        last_exit = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last_exit).total_seconds() < COOLDOWN_SECONDS
    except Exception:
        return False

def run_cycle(run_scan=True, log_status=False):
    conn = init_db()
    cur = conn.cursor()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Check open SL/TP every 15 seconds
    open_rows = cur.execute("SELECT id, symbol, side, qty, entry_price, high_water, final_stop, target_price FROM trades WHERE status='OPEN'").fetchall()
    for tid, sym, side, qty, entry, hw, stop, target in open_rows:
        try:
            ltp = get_live_price(sym)
        except Exception:
            continue
        if side == "LONG":
            new_hw = max(hw or entry, ltp)
            cur.execute("UPDATE trades SET high_water=? WHERE id=?", (new_hw, tid))
            if ltp >= target or ltp <= stop:
                reason = "Target Hit" if ltp >= target else "Stop-Loss Hit"
                pnl = round((ltp - entry) * qty, 2)
                cur.execute("UPDATE trades SET status='CLOSED', exit_price=?, exit_reason=?, exit_time=?, pnl=? WHERE id=?",
                            (ltp, reason, now_utc, pnl, tid))
                send_tg(f"🔔 *Scalp Exit ({sym})*\nSide: `{side}` | Reason: *{reason}*\nEntry: `${entry:,.4f}` → Exit: `${ltp:,.4f}`\nNet P&L: `${pnl:+,.2f}`")
        else:
            new_hw = min(hw or entry, ltp)
            cur.execute("UPDATE trades SET high_water=? WHERE id=?", (new_hw, tid))
            if ltp <= target or ltp >= stop:
                reason = "Target Hit" if ltp <= target else "Stop-Loss Hit"
                pnl = round((entry - ltp) * qty, 2)
                cur.execute("UPDATE trades SET status='CLOSED', exit_price=?, exit_reason=?, exit_time=?, pnl=? WHERE id=?",
                            (ltp, reason, now_utc, pnl, tid))
                send_tg(f"🔔 *Scalp Exit ({sym})*\nSide: `{side}` | Reason: *{reason}*\nEntry: `${entry:,.4f}` → Exit: `${ltp:,.4f}`\nNet P&L: `${pnl:+,.2f}`")
    conn.commit()

    # Scan 15m + 5m candles every 60 seconds to avoid Yahoo rate limits
    if run_scan:
        open_syms = {r[1] for r in cur.execute("SELECT id, symbol FROM trades WHERE status='OPEN'").fetchall()}
        signals = nse_scanner.scan_full_nse_universe(min_prob=80.0)
        if log_status:
            print(f"[{now_utc}] Open={list(open_syms)} | Strict-Scalp Signals={[(s['symbol'], s['side'], s['price']) for s in signals]}", flush=True)

        if len(open_syms) < MAX_OPEN_TRADES:
            equity = get_current_equity(cur)
            for sig in signals:
                if sig["symbol"] in open_syms or len(open_syms) >= MAX_OPEN_TRADES:
                    continue
                if is_in_cooldown(cur, sig["symbol"]):
                    continue
                price = sig["price"]
                risk_dollars = equity * sig["risk_pct"]
                risk_per_unit = max(abs(price - sig["stop"]), price * 0.003)
                qty = round(risk_dollars / risk_per_unit, 6)
                cur.execute("""
                    INSERT INTO trades (symbol, side, status, qty, entry_price, high_water, final_stop, target_price, timemachine_prob, entry_reason, entry_time)
                    VALUES (?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?)
                """, (sig["symbol"], sig["side"], qty, price, price, sig["stop"], sig["target"], sig["prob"], sig["reason"], now_utc))
                conn.commit()
                open_syms.add(sig["symbol"])
                send_tg(f"⚡ *Strict 5m Scalp ({sig['symbol']})*\nAction: *{sig['side']}* | Setup: *{sig['reason']}*\nEntry: `${price:,.4f}` | SL: `${sig['stop']:,.4f}` | Target: `${sig['target']:,.4f}`\nRisk: `${risk_dollars:,.2f}` (2.0%)")
    conn.close()

if __name__ == "__main__":
    start = time.time()
    step = 0
    while time.time() - start < 870:
        try:
            run_cycle(run_scan=(step % 4 == 0), log_status=(step % 12 == 0))
        except Exception as e:
            print("Cycle warning:", e, flush=True)
        step += 1
        time.sleep(15)
