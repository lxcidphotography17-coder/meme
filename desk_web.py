"""
desk_web.py — read-only web dashboard for the memecoin paper-trading desk.
Shows live open positions, Telegram alerts, closed-trade history, stats,
and a live log tail. Serves on port 3000. Does NOT modify the bot.
"""
import sqlite3, os, json, html, time
from flask import Flask

app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "desk.db")
LOG_PATH = os.path.join(BASE, "desk.log")
OPEN_POS_PATH = os.path.join(BASE, "open_positions.json")
ALERTS_PATH = os.path.join(BASE, "alerts.jsonl")
INITIAL_BANKROLL = 1000.0


# ---------------- data readers ----------------

def query_db(sql, args=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def tail_log(n=80):
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, "r", errors="replace") as f:
        return [l.rstrip("\n") for l in f.readlines()[-n:]]


def read_open_positions():
    if not os.path.exists(OPEN_POS_PATH):
        return []
    try:
        with open(OPEN_POS_PATH) as f:
            data = json.load(f)
        return [(mint, pos) for mint, pos in data.items()]
    except Exception:
        return []


def read_alerts(n=40):
    if not os.path.exists(ALERTS_PATH):
        return []
    try:
        with open(ALERTS_PATH, "r", errors="replace") as f:
            lines = f.readlines()
        out = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
        return out
    except Exception:
        return []


def compute_stats():
    rows = query_db("SELECT pnl FROM trades")
    pnls = [r["pnl"] for r in rows]
    if not pnls:
        return None
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    wr = len(wins) / len(pnls)
    aw = sum(wins) / len(wins) if wins else 0
    al = sum(losses) / len(losses) if losses else 0
    exp = wr * aw + (1 - wr) * al
    peak = run = dd = 0.0
    for x in pnls:
        run += x
        peak = max(peak, run)
        dd = max(dd, peak - run)
    return dict(
        trades=len(pnls), wr=wr, aw=aw, al=al, exp=exp, dd=dd,
        bankroll=INITIAL_BANKROLL + sum(pnls),
    )


# ---------------- formatters ----------------

def fmt_usd(v):
    return f"${v:,.2f}"

def fmt_pct(v):
    return f"{v * 100:.0f}%"

def fmt_time(ts):
    if not ts:
        return "—"
    return time.strftime("%H:%M:%S", time.localtime(ts))

def fmt_age(opened):
    if not opened:
        return "—"
    s = time.time() - opened
    if s >= 3600:
        return f"{s / 3600:.1f}h"
    return f"{s / 60:.0f}m"


# ---------------- renderers ----------------

def render_open_positions(positions):
    if not positions:
        return '<div class="empty">No open positions — desk is scanning for leads…</div>'
    rows = []
    for mint, pos in positions:
        symbol = html.escape(pos.get("symbol", "?"))
        entry = pos.get("entry", 0)
        last = pos.get("last_price", entry)
        gain = pos.get("gain", 0)
        peak = pos.get("peak", entry)
        size = pos.get("size", 0)
        opened = pos.get("opened", 0)
        half_sold = pos.get("half_sold", False)
        gain_cls = "green" if gain >= 0 else "red"
        status = "50% out" if half_sold else "full"
        rows.append(
            f"<tr>"
            f"<td><b>{symbol}</b></td>"
            f'<td title="{html.escape(mint)}">{html.escape(mint[:10])}…</td>'
            f'<td class="num">{entry:.8g}</td>'
            f'<td class="num">{last:.8g}</td>'
            f'<td class="num {gain_cls}">{gain * 100:+.1f}%</td>'
            f'<td class="num">{peak:.8g}</td>'
            f'<td class="num">{fmt_usd(size)}</td>'
            f'<td class="num">{fmt_age(opened)}</td>'
            f"<td>{status}</td>"
            f"</tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr>'
        "<th>Symbol</th><th>Mint</th><th>Entry</th><th>Last</th>"
        "<th>Gain</th><th>Peak</th><th>Size</th><th>Age</th><th>Status</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def render_alerts(alerts):
    if not alerts:
        return '<div class="empty">No alerts yet — waiting for the first trade…</div>'
    items = []
    for a in reversed(alerts):  # newest first
        ts = fmt_time(a.get("ts", 0))
        msg = html.escape(a.get("msg", ""))
        if msg.startswith("EXECUTED"):
            cls = "alert-exec"
        elif msg.startswith("CLOSED"):
            cls = "alert-close"
        elif msg.startswith("SCALED"):
            cls = "alert-scale"
        elif msg.startswith("DESK REPORT"):
            cls = "alert-report"
        else:
            cls = "alert-info"
        items.append(
            f'<div class="alert {cls}"><span class="alert-ts">{ts}</span>{msg}</div>'
        )
    return '<div class="scroll alerts-scroll">' + "".join(items) + "</div>"


def render_trades_table(trades):
    if not trades:
        return '<div class="empty">No closed trades yet — the desk is scanning for leads…</div>'
    rows_html = []
    for t in trades:
        pnl = t["pnl"] or 0
        cls = "green" if pnl > 0 else "red"
        rows_html.append(
            f"<tr>"
            f'<td>{html.escape(t["symbol"] or "?")}</td>'
            f'<td title="{html.escape(t["mint"] or "")}">{html.escape((t["mint"] or "")[:10])}…</td>'
            f'<td class="num">{t["entry"] or 0:.8g}</td>'
            f'<td class="num">{t["exit"] or 0:.8g}</td>'
            f'<td class="num">{fmt_usd(t["size"] or 0)}</td>'
            f'<td class="num {cls}">{fmt_usd(pnl)}</td>'
            f'<td>{html.escape(t["reason"] or "")}</td>'
            f'<td class="num">{fmt_time(t["closed"])}</td>'
            f"</tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr>'
        "<th>Symbol</th><th>Mint</th><th>Entry</th><th>Exit</th>"
        "<th>Size</th><th>PnL</th><th>Reason</th><th>Closed</th>"
        "</tr></thead><tbody>"
        + "".join(rows_html)
        + "</tbody></table></div>"
    )


# ---------------- page ----------------

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="5">
<title>Memecoin Desk</title>
<style>
  :root {{
    --bg: #0b0e14; --panel: #141925; --panel2: #1a2030;
    --border: #232b3d; --text: #e4e8f0; --muted: #7a8499;
    --green: #22c55e; --red: #ef4444; --accent: #6366f1;
    --blue: #3b82f6; --purple: #a855f7;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
    background: var(--bg); color: var(--text); padding: 24px;
    max-width: 1200px; margin: 0 auto; line-height: 1.5;
  }}
  header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }}
  header h1 {{ font-size: 22px; font-weight: 700; }}
  .badge {{
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .05em; padding: 3px 10px; border-radius: 999px;
    background: var(--accent); color: #fff;
  }}
  .sub {{ color: var(--muted); font-size: 13px; margin-bottom: 20px; }}
  .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--green); margin-right: 6px; animation: pulse 2s infinite; }}
  @keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: .3; }} }}

  .cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px; margin-bottom: 24px; }}
  .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }}
  .card .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); margin-bottom: 6px; }}
  .card .value {{ font-size: 22px; font-weight: 700; }}
  .green {{ color: var(--green); }} .red {{ color: var(--red); }}

  .two-col {{ display: grid; grid-template-columns: 1.5fr 1fr; gap: 24px; margin-bottom: 24px; }}
  @media (max-width: 900px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
  section {{ margin-bottom: 24px; }}
  section h2 {{ font-size: 14px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }}
  .count-pill {{ font-size: 11px; background: var(--panel2); border: 1px solid var(--border); border-radius: 999px; padding: 1px 8px; color: var(--text); }}

  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .wrap {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }}
  .scroll {{ max-height: 300px; overflow-y: auto; }}

  .alert {{ padding: 8px 14px; border-left: 3px solid var(--border); margin-bottom: 2px; font-size: 12px; word-break: break-all; line-height: 1.6; }}
  .alert-ts {{ color: var(--muted); margin-right: 10px; font-size: 11px; }}
  .alert-exec {{ border-color: var(--green); }}
  .alert-close {{ border-color: var(--blue); }}
  .alert-scale {{ border-color: var(--purple); }}
  .alert-report {{ border-color: var(--muted); }}
  .alert-info {{ border-color: var(--accent); }}
  .alerts-scroll {{ max-height: 300px; overflow-y: auto; padding: 8px 0; }}

  #log {{ font-size: 12px; padding: 12px 16px; white-space: pre-wrap; word-break: break-all; }}
  .empty {{ color: var(--muted); padding: 24px; text-align: center; font-size: 13px; }}
</style>
</head>
<body>
  <header>
    <h1>🚀 Memecoin Desk</h1>
    <span class="badge">Paper Trading</span>
  </header>
  <div class="sub"><span class="dot"></span>Bot running &middot; auto-refresh every 5s</div>

  <div class="cards">
    <div class="card"><div class="label">Bankroll</div><div class="value {bankroll_cls}">{bankroll}</div></div>
    <div class="card"><div class="label">Open Positions</div><div class="value">{open_count}</div></div>
    <div class="card"><div class="label">Closed Trades</div><div class="value">{trades}</div></div>
    <div class="card"><div class="label">Win Rate</div><div class="value">{wr}</div></div>
    <div class="card"><div class="label">Expectancy</div><div class="value {exp_cls}">{exp}</div></div>
    <div class="card"><div class="label">Max Drawdown</div><div class="value red">{dd}</div></div>
  </div>

  <div class="two-col">
    <section>
      <h2>Open Positions <span class="count-pill">{open_count}</span></h2>
      <div class="wrap">{positions_table}</div>
    </section>
    <section>
      <h2>Telegram Alerts</h2>
      <div class="wrap">{alerts_html}</div>
    </section>
  </div>

  <section>
    <h2>Closed Trades</h2>
    <div class="wrap">{trades_table}</div>
  </section>

  <section>
    <h2>Live Log</h2>
    <div class="wrap"><div class="scroll"><div id="log">{log_html}</div></div></div>
  </section>
</body>
</html>"""


@app.route("/")
def index():
    trades = query_db("SELECT * FROM trades ORDER BY closed DESC LIMIT 50")
    stats = compute_stats()
    log_lines = tail_log(80)
    positions = read_open_positions()
    alerts = read_alerts(40)

    bankroll = fmt_usd(stats["bankroll"]) if stats else fmt_usd(INITIAL_BANKROLL)
    bankroll_cls = "green" if (stats is None or stats["bankroll"] >= INITIAL_BANKROLL) else "red"
    open_count = len(positions)
    n_trades = stats["trades"] if stats else 0
    wr = fmt_pct(stats["wr"]) if stats else "—"
    exp = fmt_usd(stats["exp"]) if stats else "—"
    exp_cls = "green" if stats and stats["exp"] >= 0 else "red"
    dd = fmt_usd(stats["dd"]) if stats else "—"

    log_html = html.escape("\n".join(log_lines)) if log_lines else "Waiting for bot output…"

    return PAGE.format(
        bankroll=bankroll,
        bankroll_cls=bankroll_cls,
        open_count=open_count,
        trades=n_trades,
        wr=wr,
        exp=exp,
        exp_cls=exp_cls,
        dd=dd,
        positions_table=render_open_positions(positions),
        alerts_html=render_alerts(alerts),
        trades_table=render_trades_table(trades),
        log_html=log_html,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
