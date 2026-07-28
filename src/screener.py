from __future__ import annotations

import argparse
import html
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


DISCLAIMER = (
    "Research tool only—not financial advice or an instruction to trade. "
    "Scores describe rule matches, not expected returns. Verify all data before acting."
)
EASTERN_TIME = ZoneInfo("America/New_York")


@dataclass
class Result:
    ticker: str
    score: int
    close: float
    day_change_pct: float
    rsi_14: float
    ema_20: float
    ema_50: float
    atr_pct: float
    relative_volume: float
    average_dollar_volume: float
    setup: str
    signal: str
    buy_votes: int
    sell_votes: int
    neutral_votes: int
    model_votes: dict[str, str]
    model_details: dict[str, str]
    limit_entry: float
    stop_price: float
    target_price: float
    risk_per_share: float
    order_action: str
    order_note: str
    reasons: list[str]
    warnings: list[str]


def read_tickers(path: Path) -> list[str]:
    tickers = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip().upper()
        if value and not value.startswith("#"):
            tickers.append(value)
    return list(dict.fromkeys(tickers))


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy().dropna(subset=["Close"])
    close = data["Close"]
    previous_close = close.shift(1)

    data["EMA20"] = close.ewm(span=20, adjust=False).mean()
    data["EMA50"] = close.ewm(span=50, adjust=False).mean()
    data["SMA20"] = close.rolling(20).mean()
    standard_deviation = close.rolling(20).std()
    data["BB_UPPER"] = data["SMA20"] + (2 * standard_deviation)
    data["BB_LOWER"] = data["SMA20"] - (2 * standard_deviation)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    data["MACD"] = ema12 - ema26
    data["MACD_SIGNAL"] = data["MACD"].ewm(span=9, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    relative_strength = gain / loss.replace(0, np.nan)
    data["RSI14"] = (100 - (100 / (1 + relative_strength))).fillna(100)

    true_range = pd.concat(
        [
            data["High"] - data["Low"],
            (data["High"] - previous_close).abs(),
            (data["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    data["ATR14"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    data["AVG_VOLUME20"] = data["Volume"].rolling(20).mean()
    data["PRIOR_HIGH20"] = data["High"].shift(1).rolling(20).max()
    data["PRIOR_LOW20"] = data["Low"].shift(1).rolling(20).min()
    data["HIGH252"] = data["High"].rolling(252, min_periods=100).max()
    return data


def model_consensus(
    row: pd.Series,
) -> tuple[str, dict[str, str], dict[str, str]]:
    close = float(row["Close"])
    ema20 = float(row["EMA20"])
    ema50 = float(row["EMA50"])
    rsi = float(row["RSI14"])
    macd = float(row["MACD"])
    macd_signal = float(row["MACD_SIGNAL"])
    relative_volume = float(row["Volume"] / row["AVG_VOLUME20"])
    prior_high = float(row["PRIOR_HIGH20"])
    prior_low = float(row["PRIOR_LOW20"])

    votes = {
        "EMA trend": (
            "Buy"
            if close > ema20 > ema50
            else "Sell" if close < ema20 < ema50 else "Neutral"
        ),
        "MACD momentum": (
            "Buy"
            if macd > macd_signal and macd > 0
            else "Sell" if macd < macd_signal and macd < 0 else "Neutral"
        ),
        "RSI momentum": (
            "Buy"
            if 50 <= rsi <= 70
            else "Sell" if rsi < 40 or rsi > 75 else "Neutral"
        ),
        "Breakout + volume": (
            "Buy"
            if close > prior_high and relative_volume >= 1.2
            else (
                "Sell"
                if close < prior_low and relative_volume >= 1.2
                else "Neutral"
            )
        ),
    }
    details = {
        "EMA trend": (
            f"Close ${close:.2f}; EMA20 ${ema20:.2f}; EMA50 ${ema50:.2f}. "
            "Buy requires Close > EMA20 > EMA50. Sell requires "
            "Close < EMA20 < EMA50. Otherwise Neutral."
        ),
        "MACD momentum": (
            f"MACD {macd:.2f}; signal line {macd_signal:.2f}. "
            "Buy requires MACD above both its signal line and zero. Sell requires "
            "MACD below both its signal line and zero. Otherwise Neutral."
        ),
        "RSI momentum": (
            f"RSI(14) {rsi:.1f}. Buy is 50–70. Sell is below 40 or above 75; "
            "above 75 means extended, not a guaranteed reversal. Otherwise Neutral."
        ),
        "Breakout + volume": (
            f"Close ${close:.2f}; prior 20-day high ${prior_high:.2f}; "
            f"prior 20-day low ${prior_low:.2f}; relative volume "
            f"{relative_volume:.2f}×. Buy requires a close above the prior high "
            "with at least 1.20× volume. Sell requires a close below the prior low "
            "with at least 1.20× volume. Otherwise Neutral."
        ),
    }

    buy_votes = sum(vote == "Buy" for vote in votes.values())
    sell_votes = sum(vote == "Sell" for vote in votes.values())
    if buy_votes >= 3 and sell_votes == 0:
        signal = "Strong Buy"
    elif buy_votes >= 2 and buy_votes > sell_votes:
        signal = "Buy"
    elif sell_votes >= 3 and buy_votes == 0:
        signal = "Strong Sell"
    elif sell_votes >= 2 and sell_votes > buy_votes:
        signal = "Sell"
    else:
        signal = "Neutral"
    return signal, votes, details


def order_guide(row: pd.Series, signal: str) -> dict[str, float | str]:
    close = float(row["Close"])
    ema20 = float(row["EMA20"])
    atr = float(row["ATR14"])

    # A pullback guide: never above the latest close and never more than 1 ATR below it.
    limit_entry = max(close - atr, min(close, ema20))
    risk_per_share = max(atr * 1.5, limit_entry * 0.01)
    stop_price = max(0.01, limit_entry - risk_per_share)
    target_price = limit_entry + (2 * risk_per_share)
    bullish = signal in {"Buy", "Strong Buy"}

    return {
        "limit_entry": round(limit_entry, 2),
        "stop_price": round(stop_price, 2),
        "target_price": round(target_price, 2),
        "risk_per_share": round(risk_per_share, 2),
        "order_action": "Research buy limit" if bullish else "No new long setup",
        "order_note": (
            "Hypothetical pullback entry. A buy limit may fill at this price or lower, "
            "but execution is not guaranteed."
            if bullish
            else "The model vote does not support a new long entry. Prices are shown "
            "only as a risk map; wait for a fresh bullish signal."
        ),
    }


def score_ticker(ticker: str, frame: pd.DataFrame) -> Result | None:
    data = add_indicators(frame)
    if len(data) < 100:
        return None

    row = data.iloc[-1]
    prior = data.iloc[-2]
    close = float(row["Close"])
    ema20 = float(row["EMA20"])
    ema50 = float(row["EMA50"])
    rsi = float(row["RSI14"])
    atr_pct = float(row["ATR14"] / close * 100)
    relative_volume = float(row["Volume"] / row["AVG_VOLUME20"])
    average_dollar_volume = float(row["AVG_VOLUME20"] * close)
    day_change_pct = float((close / prior["Close"] - 1) * 100)
    high_252 = float(row["HIGH252"])
    signal, model_votes, model_details = model_consensus(row)
    guide = order_guide(row, signal)

    score = 0
    reasons: list[str] = []
    warnings: list[str] = []

    if close > ema20 > ema50:
        score += 25
        reasons.append("Price and moving averages are in an uptrend")
    elif close > ema50:
        score += 10
        reasons.append("Price is above its 50-day trend")
    else:
        warnings.append("Price is below its 50-day trend")

    distance_from_ema20 = (close / ema20 - 1) * 100
    if 0 <= distance_from_ema20 <= 4:
        score += 15
        reasons.append("Price is close to its 20-day trend")

    if 50 <= rsi <= 70:
        score += 20
        reasons.append("Momentum is positive without the rule-set calling it extended")
    elif rsi > 75:
        warnings.append("Momentum may be extended")

    if close > float(row["PRIOR_HIGH20"]):
        score += 15
        reasons.append("Price closed above the prior 20-day high")

    if relative_volume >= 1.2:
        score += 10
        reasons.append("Volume is at least 20% above its recent average")

    if close >= high_252 * 0.85:
        score += 10
        reasons.append("Price is within 15% of its 52-week high")

    if 0 < day_change_pct <= 5:
        score += 5
        reasons.append("Latest daily move is positive but below 5%")
    elif day_change_pct > 8:
        warnings.append("Latest daily move exceeds 8%; chasing risk is elevated")

    if close < 5:
        warnings.append("Price is below the $5 liquidity floor")
        score = min(score, 30)
    if average_dollar_volume < 20_000_000:
        warnings.append("Average daily dollar volume is below $20M")
        score = min(score, 40)
    if atr_pct > 8:
        warnings.append("Daily volatility is above the 8% risk ceiling")
        score = min(score, 45)

    if close > ema20 > ema50 and 50 <= rsi <= 70:
        setup = "Trend continuation"
    elif close > float(row["PRIOR_HIGH20"]) and relative_volume >= 1.2:
        setup = "Breakout"
    elif close > ema50 and abs(distance_from_ema20) <= 4:
        setup = "Pullback watch"
    else:
        setup = "No complete setup"

    return Result(
        ticker=ticker,
        score=int(score),
        close=round(close, 2),
        day_change_pct=round(day_change_pct, 2),
        rsi_14=round(rsi, 1),
        ema_20=round(ema20, 2),
        ema_50=round(ema50, 2),
        atr_pct=round(atr_pct, 2),
        relative_volume=round(relative_volume, 2),
        average_dollar_volume=round(average_dollar_volume, 0),
        setup=setup,
        signal=signal,
        buy_votes=sum(vote == "Buy" for vote in model_votes.values()),
        sell_votes=sum(vote == "Sell" for vote in model_votes.values()),
        neutral_votes=sum(vote == "Neutral" for vote in model_votes.values()),
        model_votes=model_votes,
        model_details=model_details,
        limit_entry=float(guide["limit_entry"]),
        stop_price=float(guide["stop_price"]),
        target_price=float(guide["target_price"]),
        risk_per_share=float(guide["risk_per_share"]),
        order_action=str(guide["order_action"]),
        order_note=str(guide["order_note"]),
        reasons=reasons,
        warnings=warnings,
    )


def extract_ticker_frame(download: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    if download.empty:
        return None
    if isinstance(download.columns, pd.MultiIndex):
        level_zero = download.columns.get_level_values(0)
        if ticker in level_zero:
            return download[ticker].dropna(how="all")
        level_one = download.columns.get_level_values(1)
        if ticker in level_one:
            return download.xs(ticker, axis=1, level=1).dropna(how="all")
    return download.dropna(how="all")


def download_prices(tickers: list[str], period: str = "18mo") -> pd.DataFrame:
    import yfinance as yf

    return yf.download(
        tickers=tickers,
        period=period,
        interval="1d",
        auto_adjust=True,
        group_by="ticker",
        threads=True,
        progress=False,
        timeout=30,
    )


def run_screen(tickers: list[str]) -> tuple[list[Result], list[str]]:
    prices = download_prices(tickers)
    results: list[Result] = []
    skipped: list[str] = []
    for ticker in tickers:
        frame = extract_ticker_frame(prices, ticker)
        if frame is None:
            skipped.append(ticker)
            continue
        try:
            result = score_ticker(ticker, frame)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            result = None
        if result is None:
            skipped.append(ticker)
        else:
            results.append(result)
    return sorted(results, key=lambda item: (-item.score, item.ticker)), skipped


def money(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    return f"${value / 1_000_000:.1f}M"


def render_html(results: list[Result], skipped: list[str], output: Path) -> None:
    generated = datetime.now(EASTERN_TIME).strftime("%Y-%m-%d %H:%M %Z")
    rows = []
    for index, item in enumerate(results):
        reason_text = "; ".join(item.reasons) or "No positive rules matched"
        warning_text = "; ".join(item.warnings) or "None"
        signal_class = item.signal.lower().replace(" ", "-")
        rows.append(
            "<tr>"
            f"<td><button class='ticker stock-open' data-index='{index}'>"
            f"{html.escape(item.ticker)}</button></td>"
            f"<td><span class='score s{min(item.score // 20, 4)}'>{item.score}</span></td>"
            f"<td><span class='signal {signal_class}'>{html.escape(item.signal)}</span>"
            f"<small>{item.buy_votes}B / {item.sell_votes}S / "
            f"{item.neutral_votes}N</small></td>"
            f"<td>{html.escape(item.setup)}</td>"
            f"<td>${item.close:,.2f}</td>"
            f"<td>{item.day_change_pct:+.2f}%</td>"
            f"<td>{item.rsi_14:.1f}</td>"
            f"<td>{item.relative_volume:.2f}×</td>"
            f"<td>{item.atr_pct:.2f}%</td>"
            f"<td>{money(item.average_dollar_volume)}</td>"
            f"<td><button class='open-guide stock-open' data-index='{index}'>"
            f"Open guide</button><span class='sr-only'>{html.escape(reason_text)} "
            f"Warnings: {html.escape(warning_text)}</span></td>"
            "</tr>"
        )

    table_rows = "\n".join(rows) or (
        "<tr><td colspan='11'>No usable market data was returned. Try again later.</td></tr>"
    )
    skipped_note = (
        f"<p class='muted'>Skipped: {html.escape(', '.join(skipped))}</p>" if skipped else ""
    )
    results_json = json.dumps([asdict(item) for item in results]).replace("</", "<\\/")
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>US Swing Stock Screener</title>
<style>
:root {{ color-scheme: dark; --bg:#0b1020; --panel:#141b2d; --line:#27314a;
--text:#e9edf7; --muted:#9aa6bd; --accent:#6ee7b7; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text);
font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif; }}
main {{ max-width:1400px; margin:auto; padding:28px 18px; }}
h1 {{ margin:0 0 6px; font-size:clamp(25px,4vw,42px); }}
.muted {{ color:var(--muted); }} .notice {{ border-left:4px solid #fbbf24;
background:#282317; padding:12px 15px; margin:20px 0; }}
.table-wrap {{ overflow:auto; background:var(--panel); border:1px solid var(--line);
border-radius:12px; }}
table {{ width:100%; border-collapse:collapse; white-space:nowrap; }}
th,td {{ padding:12px; border-bottom:1px solid var(--line); text-align:left; }}
th {{ position:sticky; top:0; background:#182137; color:#b9c4d8; }}
tr:hover {{ background:#192238; }} .ticker {{ font:inherit; font-weight:750;
color:var(--accent); background:none; border:0; padding:0; cursor:pointer; }}
.score {{ display:inline-grid; place-items:center; min-width:38px; padding:4px 7px;
border-radius:999px; background:#34405b; }} .s3,.s4 {{ background:#135d4b; }}
.s2 {{ background:#66511b; }} small {{ display:block; color:var(--muted); }}
.signal {{ display:inline-block; padding:4px 8px; border-radius:999px;
font-weight:750; }} .buy,.strong-buy {{ background:#135d4b; color:#b7f7dd; }}
.sell,.strong-sell {{ background:#742f3b; color:#fecdd3; }}
.neutral {{ background:#3d465b; color:#d9e1ef; }}
.open-guide {{ border:1px solid #456086; border-radius:8px; padding:7px 10px;
background:#1c2a43; color:#bfdbfe; cursor:pointer; }}
.sr-only {{ position:absolute; width:1px; height:1px; padding:0; margin:-1px;
overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }}
dialog {{ width:min(720px,calc(100% - 24px)); max-height:calc(100% - 24px);
overflow:auto; color:var(--text); background:var(--panel); border:1px solid var(--line);
border-radius:14px; padding:0; box-shadow:0 24px 80px #000a; }}
dialog::backdrop {{ background:#030712cc; }}
.dialog-head {{ display:flex; align-items:center; justify-content:space-between;
padding:18px 20px; border-bottom:1px solid var(--line); }}
.dialog-head h2 {{ margin:0; }} .dialog-body {{ padding:20px; }}
.close {{ background:none; border:0; color:var(--text); font-size:28px;
cursor:pointer; }} .price-grid {{ display:grid;
grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; }}
.card {{ padding:12px; border:1px solid var(--line); border-radius:10px;
background:#11182a; }} .card b {{ display:block; font-size:18px; }}
.definition {{ display:block; margin-top:7px; color:var(--muted); font-size:13px; }}
.model-table {{ width:100%; white-space:normal; margin:14px 0; }}
.model-table th {{ position:static; padding:9px 4px; background:transparent; }}
.model-table td {{ padding:9px 4px; vertical-align:top; }}
.model-rule {{ min-width:280px; color:var(--muted); font-size:13px; }}
.rule-summary {{ padding:12px; border:1px solid var(--line); border-radius:10px; }}
.order-note {{ padding:12px;
border-left:4px solid #60a5fa; background:#15233b; }}
</style>
</head>
<body><main>
<h1>US Swing Stock Screener</h1>
<p class="muted">Generated {generated} · Highest rule-match score first</p>
<div class="notice"><strong>Important:</strong> {html.escape(DISCLAIMER)}</div>
<div class="table-wrap"><table>
<thead><tr><th>Ticker</th><th>Score</th><th>Signal</th><th>Setup</th><th>Close</th>
<th>Day</th><th>RSI</th><th>Rel. volume</th><th>ATR</th>
<th>Avg $ volume</th><th>Details</th></tr></thead>
<tbody>{table_rows}</tbody>
</table></div>
{skipped_note}
<p class="muted">A score is a transparent checklist total. It is not a prediction.
Prices may be delayed or incomplete.</p>
</main>
<dialog id="stock-dialog">
  <div class="dialog-head">
    <div><h2 id="dialog-ticker"></h2><span id="dialog-signal" class="signal"></span></div>
    <button class="close" id="dialog-close" aria-label="Close">&times;</button>
  </div>
  <div class="dialog-body">
    <p id="dialog-votes" class="muted"></p>
    <p class="rule-summary"><strong>Consensus categories:</strong>
    Strong Buy = at least 3 Buy votes and no Sell votes. Buy = at least 2 Buy
    votes and more Buy than Sell votes. Neutral = neither side qualifies.
    Sell mirrors Buy; Strong Sell mirrors Strong Buy. Sell signals are an
    exit/review warning for long positions, not a short-sale instruction.</p>
    <h3>Model votes</h3>
    <table class="model-table">
      <thead><tr><th>Indicator</th><th>Vote</th><th>Exact rule and current values</th></tr></thead>
      <tbody id="model-votes"></tbody>
    </table>
    <h3>Hypothetical order guide</h3>
    <p><strong id="order-action"></strong></p>
    <div class="price-grid">
      <div class="card">Planned buy limit<b id="limit-entry"></b>
        <span class="definition">Maximum planned entry price. A buy limit can
        fill at this price or lower, or may not fill.</span></div>
      <div class="card">Protective sell-stop guide<b id="stop-price"></b>
        <span class="definition">Example loss-control trigger below entry.
        A stop becomes a market order and its fill price can differ.</span></div>
      <div class="card">Example take-profit sell limit (2R)<b id="target-price"></b>
        <span class="definition">Entry + twice the planned risk per share.
        A sell limit can fill at this price or higher, or may not fill.</span></div>
      <div class="card">Planned risk per share (1R)<b id="risk-share"></b>
        <span class="definition">Buy limit − protective stop. This is the
        planned loss per share before slippage or fees.</span></div>
    </div>
    <p class="rule-summary" id="take-profit-example"></p>
    <p class="order-note" id="order-note"></p>
    <p class="muted">Share-count formula: your chosen dollar risk ÷ risk per share.
    Recalculate the target from the actual fill price. Limit orders may not execute;
    stop orders can execute away from the stop price. Confirm supported order types,
    time-in-force, live quotes, and buying power with your broker.</p>
    <h3>Why it ranked here</h3>
    <p id="dialog-reasons"></p>
    <p><strong>Warnings:</strong> <span id="dialog-warnings"></span></p>
  </div>
</dialog>
<script id="results-data" type="application/json">{results_json}</script>
<script>
const results = JSON.parse(document.getElementById("results-data").textContent);
const dialog = document.getElementById("stock-dialog");
const dollars = value => `$${{Number(value).toFixed(2)}}`;

function openStock(index) {{
  const item = results[index];
  document.getElementById("dialog-ticker").textContent =
    `${{item.ticker}} · $${{item.close.toFixed(2)}}`;
  const signal = document.getElementById("dialog-signal");
  signal.textContent = item.signal;
  signal.className = `signal ${{item.signal.toLowerCase().replaceAll(" ", "-")}}`;
  document.getElementById("dialog-votes").textContent =
    `${{item.buy_votes}} Buy · ${{item.sell_votes}} Sell · ` +
    `${{item.neutral_votes}} Neutral`;
  document.getElementById("model-votes").replaceChildren(
    ...Object.entries(item.model_votes).map(([model, vote]) => {{
      const row = document.createElement("tr");
      const name = document.createElement("td");
      const result = document.createElement("td");
      const detail = document.createElement("td");
      name.textContent = model;
      result.textContent = vote;
      result.className = `signal ${{vote.toLowerCase()}}`;
      detail.textContent = item.model_details[model];
      detail.className = "model-rule";
      row.append(name, result, detail);
      return row;
    }})
  );
  document.getElementById("order-action").textContent = item.order_action;
  document.getElementById("limit-entry").textContent = dollars(item.limit_entry);
  document.getElementById("stop-price").textContent = dollars(item.stop_price);
  document.getElementById("target-price").textContent = dollars(item.target_price);
  document.getElementById("risk-share").textContent = dollars(item.risk_per_share);
  document.getElementById("take-profit-example").textContent =
    item.signal === "Buy" || item.signal === "Strong Buy"
      ? `If shares fill at ${{dollars(item.limit_entry)}}, the illustrative ` +
        `take-profit sell limit is ${{dollars(item.target_price)}}. ` +
        `1R = ${{dollars(item.limit_entry)}} − ${{dollars(item.stop_price)}} = ` +
        `${{dollars(item.risk_per_share)}}; 2R adds twice that risk to the entry.`
      : "No new long entry is supported by the current consensus, so the displayed " +
        "prices are a risk map—not a take-profit instruction.";
  document.getElementById("order-note").textContent = item.order_note;
  document.getElementById("dialog-reasons").textContent =
    item.reasons.join("; ") || "No positive rules matched";
  document.getElementById("dialog-warnings").textContent =
    item.warnings.join("; ") || "None";
  dialog.showModal();
}}

document.querySelectorAll(".stock-open").forEach(button =>
  button.addEventListener("click", () => openStock(Number(button.dataset.index)))
);
document.getElementById("dialog-close").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", event => {{
  if (event.target === dialog) dialog.close();
}});
</script>
</body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")


def write_json(results: list[Result], skipped: list[str], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(EASTERN_TIME).isoformat(),
        "generated_timezone": "America/New_York",
        "disclaimer": DISCLAIMER,
        "results": [asdict(item) for item in results],
        "skipped": skipped,
    }
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank US swing-trade research candidates.")
    parser.add_argument("--tickers", type=Path, default=Path("config/tickers.txt"))
    parser.add_argument("--html", type=Path, default=Path("report/index.html"))
    parser.add_argument("--json", type=Path, default=Path("report/results.json"))
    args = parser.parse_args()

    tickers = read_tickers(args.tickers)
    if not tickers:
        raise SystemExit("No tickers were found in the watchlist.")
    results, skipped = run_screen(tickers)
    render_html(results, skipped, args.html)
    write_json(results, skipped, args.json)
    print(f"Screened {len(results)} tickers; skipped {len(skipped)}.")
    print(f"Report: {args.html.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
