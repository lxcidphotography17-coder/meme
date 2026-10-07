"""
memecoin_desk.py - PAPER-TRADING Solana memecoin desk.
Pipeline: scanned -> cleared -> confirmed -> executed -> closed

pip install requests
python memecoin_desk.py            # paper mode (default)
Optional env: TG_TOKEN, TG_CHAT_ID for Telegram alerts.

NO private keys anywhere in this file. Live mode only stages an order and
requires you to type APPROVE <id>; real signing is intentionally not included.
Verify API endpoints against current docs (DexScreener, RugCheck) before use.
"""
import os, time, json, sqlite3, requests
from dataclasses import dataclass

# ---------------- CONFIG (only a human edits these) ----------------
@dataclass
class Cfg:
    paper: bool = True
    bankroll: float = 1000.0          # paper USD
    risk_per_trade: float = 0.02      # 2% of bankroll per position
    max_open: int = 4
    daily_loss_halt: float = 0.05     # halt floor at -5% on the day
    min_liq: float = 20_000           # USD liquidity
    min_vol_h1: float = 25_000
    min_age_min: int = 30             # avoid first-minutes sniper zone
    max_age_h: int = 24
    min_buy_ratio: float = 1.2        # h1 buys / sells
    max_pump_h1: float = 150.0        # skip tokens already +150% in 1h
    slippage: float = 0.03            # simulated per side
    stop_loss: float = -0.15
    trail_start: float = 0.30         # start trailing after +30%
    trail_gap: float = 0.15           # trail 15% below peak
    scale_out_at: float = 1.00        # sell half at 2x
    max_hold_h: float = 12
    scan_every: int = 60
    pos_check_every: int = 15

C = Cfg()
DB = sqlite3.connect("desk.db")
DB.execute("""CREATE TABLE IF NOT EXISTS trades(
 id INTEGER PRIMARY KEY, mint TEXT, symbol TEXT, entry REAL, exit REAL,
 size REAL, pnl REAL, reason TEXT, opened REAL, closed REAL)""")
DB.commit()

DEX = "https://api.dexscreener.com"
RUG = "https://api.rugcheck.xyz/v1/tokens/{}/report/summary"

def get(url, **kw):
    try:
        r = requests.get(url, timeout=10, **kw)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[warn] {url[:60]}... {e}")
        return None

def notify(msg):
    print(msg)
    try:
        with open("alerts.jsonl", "a") as f:
            f.write(json.dumps({"ts": time.time(), "msg": msg}) + "\n")
    except Exception:
        pass
    t, c = os.getenv("TG_TOKEN"), os.getenv("TG_CHAT_ID")
    if t and c:
        try:
            requests.post(f"https://api.telegram.org/bot{t}/sendMessage",
                          data={"chat_id": c, "text": msg}, timeout=10)
        except Exception:
            pass

# ---------------- SEARCH: discover leads ----------------
def scan():
    prof = get(f"{DEX}/token-profiles/latest/v1") or []
    mints = [p["tokenAddress"] for p in prof if p.get("chainId") == "solana"][:30]
    leads = []
    for m in mints:
        d = get(f"{DEX}/latest/dex/tokens/{m}")
        pairs = [p for p in ((d or {}).get("pairs") or []) if p.get("chainId") == "solana"]
        if not pairs:
            continue
        p = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd", 0))
        leads.append((m, p))
    return leads

# ---------------- RISK: clearance (fail closed) ----------------
def risk_clear(mint, p):
    liq = (p.get("liquidity") or {}).get("usd", 0)
    vol = (p.get("volume") or {}).get("h1", 0)
    tx = (p.get("txns") or {}).get("h1", {})
    buys, sells = tx.get("buys", 0), max(tx.get("sells", 0), 1)
    chg = (p.get("priceChange") or {}).get("h1", 0) or 0
    created = p.get("pairCreatedAt")
    if not created:
        return False, "no age"
    age_min = (time.time() * 1000 - created) / 60000
    if liq < C.min_liq: return False, f"liq {liq:.0f}"
    if vol < C.min_vol_h1: return False, f"vol {vol:.0f}"
    if not (C.min_age_min <= age_min <= C.max_age_h * 60): return False, f"age {age_min:.0f}m"
    if buys / sells < C.min_buy_ratio: return False, "weak buy ratio"
    if chg > C.max_pump_h1: return False, f"already +{chg:.0f}%"
    rc = get(RUG.format(mint))
    if rc is None:
        return False, "rugcheck unavailable (fail closed)"
    risks = rc.get("risks") or []
    if any((r.get("level") or "").lower() == "danger" for r in risks):
        return False, "rugcheck danger: " + ",".join(r.get("name", "?") for r in risks[:3])
    return True, "cleared"

# ---------------- WHALE / SHILL: confirmation hooks ----------------
def confirm_whale(mint):
    """Plug in a real source (Helius/Birdeye wallet feeds). Returns True/False/None."""
    return None  # None = no data; does NOT block, does NOT count as confirm

def confirm_social(mint):
    """Social hype alone is never a confirm. Stub for organic-vs-paid analysis."""
    return None

def confirmations(mint, p):
    # Confirm 1: on-chain flow (buy pressure over 5m and 1h agree)
    tx5 = (p.get("txns") or {}).get("m5", {})
    flow = tx5.get("buys", 0) > tx5.get("sells", 0)
    w = confirm_whale(mint)
    score = int(flow) + int(bool(w))
    return score >= 1, f"flow={flow} whale={w}"

# ---------------- SNIPER: execution (paper by default) ----------------
open_pos = {}

def save_open_pos():
    """Persist open positions to JSON so the dashboard can display them."""
    try:
        with open("open_positions.json", "w") as f:
            json.dump(open_pos, f)
    except Exception:
        pass

def day_pnl():
    start = time.mktime(time.localtime()[:3] + (0,) * 6)
    r = DB.execute("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE closed>=?", (start,)).fetchone()
    return r[0]

def execute(mint, p):
    price = float(p["priceUsd"])
    size = C.bankroll * C.risk_per_trade
    fill = price * (1 + C.slippage)
    if not C.paper:
        ans = input(f"Type 'APPROVE {mint[:6]}' to stage live order for {p['baseToken']['symbol']}: ")
        if ans.strip() != f"APPROVE {mint[:6]}":
            print("not approved"); return
        raise NotImplementedError("Live signing intentionally not included. "
                                  "Use Jupiter API with a throwaway wallet only after paper results.")
    open_pos[mint] = dict(symbol=p["baseToken"]["symbol"], entry=fill, size=size,
                          peak=fill, opened=time.time(), half_sold=False, realized=0.0)
    save_open_pos()
    notify(f"EXECUTED(paper) {p['baseToken']['symbol']} {mint}\nentry {fill:.8g} size ${size:.2f}")

# ---------------- EXIT: position manager ----------------
def close(mint, price, reason):
    pos = open_pos.pop(mint)
    save_open_pos()
    remaining = 0.5 if pos["half_sold"] else 1.0
    exit_px = price * (1 - C.slippage)
    pnl = pos["realized"] + pos["size"] * remaining * (exit_px / pos["entry"] - 1)
    DB.execute("INSERT INTO trades(mint,symbol,entry,exit,size,pnl,reason,opened,closed) VALUES(?,?,?,?,?,?,?,?,?)",
               (mint, pos["symbol"], pos["entry"], exit_px, pos["size"], pnl, reason, pos["opened"], time.time()))
    DB.commit()
    C.bankroll += pnl
    notify(f"CLOSED {pos['symbol']} {reason} pnl ${pnl:.2f}")

def manage_positions():
    for mint in list(open_pos):
        d = get(f"{DEX}/latest/dex/tokens/{mint}")
        pairs = (d or {}).get("pairs") or []
        if not pairs:
            continue
        p = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd", 0))
        price = float(p["priceUsd"])
        pos = open_pos[mint]
        pos["peak"] = max(pos["peak"], price)
        pos["last_price"] = price
        gain = price / pos["entry"] - 1
        pos["gain"] = gain
        liq = (p.get("liquidity") or {}).get("usd", 0)
        if liq < C.min_liq * 0.4:
            close(mint, price, "RUG/liquidity pulled"); continue   # pre-approved fast exit
        if gain <= C.stop_loss:
            close(mint, price, "stop"); continue
        if not pos["half_sold"] and gain >= C.scale_out_at:
            sell_px = price * (1 - C.slippage)
            pos["realized"] += pos["size"] * 0.5 * (sell_px / pos["entry"] - 1)
            pos["half_sold"] = True
            notify(f"SCALED OUT half of {pos['symbol']} at +{gain*100:.0f}%")
        if gain >= C.trail_start and price <= pos["peak"] * (1 - C.trail_gap):
            close(mint, price, "trailing stop"); continue
        if time.time() - pos["opened"] > C.max_hold_h * 3600:
            close(mint, price, "time stop")
    save_open_pos()

# ---------------- HEAD OF DESK: orchestration + reporting ----------------
def stats():
    rows = DB.execute("SELECT pnl FROM trades").fetchall()
    if not rows:
        return "no closed trades yet"
    pnls = [r[0] for r in rows]
    wins = [x for x in pnls if x > 0]; losses = [x for x in pnls if x <= 0]
    wr = len(wins) / len(pnls)
    aw = sum(wins) / len(wins) if wins else 0
    al = sum(losses) / len(losses) if losses else 0
    exp = wr * aw + (1 - wr) * al
    peak = run = dd = 0
    for x in pnls:
        run += x; peak = max(peak, run); dd = max(dd, peak - run)
    return (f"trades {len(pnls)} | win rate {wr*100:.0f}% | avg win ${aw:.2f} | avg loss ${al:.2f} | "
            f"EXPECTANCY ${exp:.2f}/trade | max drawdown ${dd:.2f} | bankroll ${C.bankroll:.2f}")


def daily_summary():
    """Comprehensive daily performance summary sent to Telegram once per day."""
    start_of_day = time.mktime(time.localtime()[:3] + (0,) * 6)
    today = DB.execute(
        "SELECT pnl, symbol, reason FROM trades WHERE closed >= ?", (start_of_day,)
    ).fetchall()
    today_pnl = sum(r[0] for r in today)
    today_count = len(today)
    wins_today = len([r for r in today if r[0] > 0])
    date_str = time.strftime("%Y-%m-%d", time.localtime())
    lines = [
        f"📊 DAILY SUMMARY — {date_str}",
        "───────────────────",
        f"💰 Bankroll: ${C.bankroll:.2f}",
        f"📈 Today: {today_count} trades, PnL ${today_pnl:.2f}",
    ]
    if today_count:
        lines.append(f"✅ Wins: {wins_today}/{today_count}")
        best = max(today, key=lambda r: r[0])
        worst = min(today, key=lambda r: r[0])
        lines.append(f"🚀 Best: {best[1]} +${best[0]:.2f} ({best[2]})")
        lines.append(f"💀 Worst: {worst[1]} ${worst[0]:.2f} ({worst[2]})")
    lines.append(f"📊 All-time: {stats()}")
    lines.append(f"🔓 Open positions: {len(open_pos)}")
    lines.append("───────────────────")
    return "\n".join(lines)


seen = set()

def main():
    notify("Desk online (PAPER)" if C.paper else "Desk online (LIVE-STAGED)")
    last_scan = last_report = last_daily = 0
    start_bank = C.bankroll
    while True:
        halted = day_pnl() <= -C.daily_loss_halt * start_bank
        manage_positions()
        if time.time() - last_scan > C.scan_every:
            last_scan = time.time()
            if halted:
                print("[HALT] daily loss limit hit; no new entries")
            else:
                for mint, p in scan():
                    if mint in seen or mint in open_pos or len(open_pos) >= C.max_open:
                        continue
                    seen.add(mint)
                    ok, why = risk_clear(mint, p)
                    print(f"[{p['baseToken']['symbol']}] {mint[:8]} -> {why}")
                    if not ok:
                        continue
                    ok2, why2 = confirmations(mint, p)
                    if ok2:
                        execute(mint, p)
        if time.time() - last_report > 6 * 3600:
            last_report = time.time()
            notify("DESK REPORT: " + stats() + f" | open {len(open_pos)}")
        if time.time() - last_daily > 24 * 3600:
            last_daily = time.time()
            notify(daily_summary())
        time.sleep(C.pos_check_every)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n" + stats())
