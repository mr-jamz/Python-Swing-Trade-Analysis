from __future__ import annotations

import argparse
import html
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


DISCLAIMER = (
    "Research tool only—not financial advice or an instruction to trade. "
    "Scores describe rule matches, not expected returns. Verify all data before acting."
)


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
    data["HIGH252"] = data["High"].rolling(252, min_periods=100).max()
    return data


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
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = []
    for item in results:
        reason_text = "; ".join(item.reasons) or "No positive rules matched"
        warning_text = "; ".join(item.warnings) or "None"
        rows.append(
            "<tr>"
            f"<td class='ticker'>{html.escape(item.ticker)}</td>"
            f"<td><span class='score s{min(item.score // 20, 4)}'>{item.score}</span></td>"
            f"<td>{html.escape(item.setup)}</td>"
            f"<td>${item.close:,.2f}</td>"
            f"<td>{item.day_change_pct:+.2f}%</td>"
            f"<td>{item.rsi_14:.1f}</td>"
            f"<td>{item.relative_volume:.2f}×</td>"
            f"<td>{item.atr_pct:.2f}%</td>"
            f"<td>{money(item.average_dollar_volume)}</td>"
            f"<td><details><summary>Why</summary>{html.escape(reason_text)}"
            f"<br><strong>Warnings:</strong> {html.escape(warning_text)}</details></td>"
            "</tr>"
        )

    table_rows = "\n".join(rows) or (
        "<tr><td colspan='10'>No usable market data was returned. Try again later.</td></tr>"
    )
    skipped_note = (
        f"<p class='muted'>Skipped: {html.escape(', '.join(skipped))}</p>" if skipped else ""
    )
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
tr:hover {{ background:#192238; }} .ticker {{ font-weight:750; color:var(--accent); }}
.score {{ display:inline-grid; place-items:center; min-width:38px; padding:4px 7px;
border-radius:999px; background:#34405b; }} .s3,.s4 {{ background:#135d4b; }}
.s2 {{ background:#66511b; }} details {{ white-space:normal; min-width:260px; }}
summary {{ cursor:pointer; color:#93c5fd; }}
</style>
</head>
<body><main>
<h1>US Swing Stock Screener</h1>
<p class="muted">Generated {generated} · Highest rule-match score first</p>
<div class="notice"><strong>Important:</strong> {html.escape(DISCLAIMER)}</div>
<div class="table-wrap"><table>
<thead><tr><th>Ticker</th><th>Score</th><th>Setup</th><th>Close</th>
<th>Day</th><th>RSI</th><th>Rel. volume</th><th>ATR</th>
<th>Avg $ volume</th><th>Details</th></tr></thead>
<tbody>{table_rows}</tbody>
</table></div>
{skipped_note}
<p class="muted">A score is a transparent checklist total. It is not a prediction.
Prices may be delayed or incomplete.</p>
</main></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")


def write_json(results: list[Result], skipped: list[str], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
