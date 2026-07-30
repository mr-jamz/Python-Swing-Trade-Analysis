from pathlib import Path
import json
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from screener import (
    add_indicators,
    parse_symbol_directory,
    read_tickers,
    render_html,
    result_sort_key,
    score_ticker,
    write_json,
)


def sample_prices(rows: int = 280) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=rows, freq="B")
    close = np.linspace(80, 120, rows) + np.sin(np.arange(rows) / 8)
    volume = np.full(rows, 2_000_000.0)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": volume,
        },
        index=index,
    )


class ScreenerTests(unittest.TestCase):
    def test_indicators_are_added(self):
        result = add_indicators(sample_prices())
        self.assertTrue(
            {
                "EMA20",
                "EMA50",
                "RSI14",
                "ATR14",
                "PRIOR_HIGH20",
                "PRIOR_LOW20",
                "MACD",
                "MACD_SIGNAL",
                "BB_UPPER",
                "BB_LOWER",
            }.issubset(result.columns)
        )
        self.assertGreater(result.iloc[-1]["EMA20"], result.iloc[-1]["EMA50"])

    def test_scoring_returns_explainable_result(self):
        result = score_ticker("TEST", sample_prices())
        self.assertIsNotNone(result)
        self.assertEqual(result.ticker, "TEST")
        self.assertLessEqual(result.score, 100)
        self.assertGreaterEqual(result.score, 0)
        self.assertTrue(result.reasons)
        self.assertEqual(
            result.buy_votes + result.sell_votes + result.neutral_votes,
            4,
        )
        self.assertIn(
            result.signal,
            {"Strong Buy", "Buy", "Neutral", "Sell", "Strong Sell"},
        )
        self.assertEqual(set(result.model_details), set(result.model_votes))
        self.assertTrue(
            all(
                "Buy" in detail and "Sell" in detail
                for detail in result.model_details.values()
            )
        )
        self.assertLessEqual(result.limit_entry, result.close)
        self.assertLess(result.stop_price, result.limit_entry)
        self.assertGreater(result.target_price, result.limit_entry)
        self.assertAlmostEqual(
            result.target_price - result.limit_entry,
            2 * result.risk_per_share,
            delta=0.03,
        )

    def test_html_has_click_open_order_guide(self):
        result = score_ticker("TEST", sample_prices())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "index.html"
            render_html([result], [], output)
            document = output.read_text(encoding="utf-8")
        self.assertIn("Open guide", document)
        self.assertIn('id="stock-dialog"', document)
        self.assertIn("Hypothetical order guide", document)
        self.assertIn('"model_votes"', document)
        self.assertIn("Example take-profit sell limit (2R)", document)
        self.assertIn("Exact rule and current values", document)
        self.assertIn("take-profit sell limit is", document)
        self.assertIn('const pageSize = 50', document)
        self.assertIn('id="ticker-search"', document)
        self.assertIn('id="signal-filter"', document)
        self.assertIn("stocks and ETFs", document)
        self.assertNotIn(": NaN", document)
        self.assertRegex(
            document,
            r"Generated \d{4}-\d{2}-\d{2} \d{2}:\d{2} E(?:S|D)T",
        )

    def test_non_finite_market_data_is_skipped(self):
        prices = sample_prices()
        prices.loc[prices.index[-20:], "Volume"] = 0
        self.assertIsNone(score_ticker("BAD", prices))

    def test_symbol_directory_includes_stocks_and_etfs(self):
        directory = (
            "Symbol|Security Name|Test Issue|ETF\n"
            "AAPL|Apple Inc. - Common Stock|N|N\n"
            "QQQ|Invesco QQQ Trust|N|Y\n"
            "BRK.B|Berkshire Hathaway Class B|N|N\n"
            "FAKE|Example Test Security|Y|N\n"
            "XYZW|Example Warrant|N|N\n"
            "File Creation Time: 0727202620:00||||\n"
        )
        self.assertEqual(
            parse_symbol_directory(directory, "Symbol"),
            ["AAPL", "QQQ", "BRK-B"],
        )

    def test_signal_priority_sorts_best_consensus_first(self):
        buy = score_ticker("BUY", sample_prices())
        neutral = score_ticker("NEUTRAL", sample_prices())
        buy.signal = "Buy"
        buy.score = 50
        neutral.signal = "Neutral"
        neutral.score = 100
        ordered = sorted([neutral, buy], key=result_sort_key)
        self.assertEqual([item.ticker for item in ordered], ["BUY", "NEUTRAL"])

    def test_json_timestamp_uses_eastern_time(self):
        result = score_ticker("TEST", sample_prices())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.json"
            write_json([result], [], output)
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["generated_timezone"], "America/New_York")
        self.assertRegex(payload["generated_at"], r"-0[45]:00$")

    def test_watchlist_ignores_comments_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tickers.txt"
            path.write_text("# comment\nAAPL\n\naapl\nMSFT\n", encoding="utf-8")
            self.assertEqual(read_tickers(path), ["AAPL", "MSFT"])


if __name__ == "__main__":
    unittest.main()
