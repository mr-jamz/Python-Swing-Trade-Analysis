from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from screener import add_indicators, read_tickers, render_html, score_ticker


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

    def test_watchlist_ignores_comments_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tickers.txt"
            path.write_text("# comment\nAAPL\n\naapl\nMSFT\n", encoding="utf-8")
            self.assertEqual(read_tickers(path), ["AAPL", "MSFT"])


if __name__ == "__main__":
    unittest.main()
