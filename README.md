# US Swing Stock Screener

A free, manual-run research screener for liquid US stocks. It downloads daily
market data, applies transparent technical rules, ranks the watchlist, and
publishes an HTML report with GitHub Pages.

This project does **not** place orders or tell you what to buy. Start with paper
trading and validate the rules before considering any brokerage integration.

## Run it on GitHub

1. Create an empty **public** GitHub repository and upload this project's files.
2. In **Settings → Pages → Build and deployment**, choose **GitHub Actions**.
3. Open **Actions → Run swing stock screener → Run workflow**.

The completed workflow shows a link to the report. A public repository avoids
private-repository Pages restrictions and does not expose brokerage credentials,
because this version has none. GitHub's free-plan usage and Pages availability
are subject to GitHub's current terms.

## Open a stock guide

Click a ticker or **Open guide** in the report. The panel shows:

- A consensus signal from EMA trend, MACD momentum, RSI momentum, and a
  volume-confirmed 20-day breakout model
- Each model's individual Buy, Neutral, or Sell vote, its exact threshold, and
  the stock's current indicator values
- A hypothetical pullback buy-limit price, ATR-based protective stop guide,
  example take-profit sell limit, and risk per share

`1R` is the planned risk per share: buy limit minus protective stop. The example
take-profit sell limit is a `2R` target:

```text
take-profit target = buy limit + 2 × (buy limit − protective stop)
```

The order guide is a price-and-risk calculator, not an order ticket. A buy limit
can execute only at its limit price or lower and may never fill. A sell limit can
execute only at its limit price or higher and may never fill. Recalculate from
the actual fill price and confirm live prices and order behavior with your broker.

Generated report timestamps use `America/New_York`, which automatically displays
`EST` or `EDT` as daylight-saving rules require.

## Sources and methodology

The rules are an original, transparent combination of established technical
indicators. These sources explain the indicator definitions and common
interpretations used by the project:

- [Exponential Moving Average (EMA) — Fidelity](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/ema)
- [Moving Average Convergence/Divergence (MACD) — Fidelity](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/macd)
- [Relative Strength Index (RSI) — Fidelity](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/RSI)
- [Average True Range (ATR) — Fidelity](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/atr)
- [Bollinger Bands — Fidelity](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/bollinger-bands)
- [Volume, support, resistance, and technical-analysis overview — Fidelity](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/what-is-technical-analysis)
- [Profit/loss targets and exit strategies — Fidelity](https://www.fidelity.com/learning-center/trading-investing/trading/exit-strategies)
- [Limit and stop order behavior — Investor.gov](https://www.investor.gov/introduction-investing/investing-basics/how-stock-markets-work/types-orders)

These references do not endorse this screener or its thresholds. The consensus
labels and ATR-based price guide are project-specific research rules and have
not been validated as profitable.

## Change the stocks

Edit `config/tickers.txt`. Use one Yahoo Finance ticker symbol per line.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python src/screener.py
```

Open `report/index.html` after the command finishes.

## How scoring works

The maximum score is 100:

| Rule | Points |
|---|---:|
| Price > EMA20 > EMA50 | 25 |
| Price near EMA20 | 15 |
| RSI between 50 and 70 | 20 |
| Close above prior 20-day high | 15 |
| Relative volume at least 1.2× | 10 |
| Within 15% of 52-week high | 10 |
| Positive latest day below 5% | 5 |

Liquidity and extreme-volatility warnings cap weak or risky candidates. Every
result includes its matched rules and warnings.

## Important limitations

- Yahoo Finance data is convenient and free, but unofficial and may be delayed,
  unavailable, or changed without notice.
- Technical rules can lose money and must be validated out of sample.
- The starter watchlist is intentionally small to reduce data failures.
- GitHub Actions is suitable for manual research runs, not reliable low-latency
  execution or live order management.

## Next safe milestone

Add a paper-trading ledger and backtest before connecting any brokerage API.
