from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from screener import add_indicators, read_tickers, score_ticker


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
            {"EMA20", "EMA50", "RSI14", "ATR14", "PRIOR_HIGH20"}.issubset(
                result.columns
            )
        )
        self.assertGreater(result.iloc[-1]["EMA20"], result.iloc[-1]["EMA50"])

    def test_scoring_returns_explainable_result(self):
        result = score_ticker("TEST", sample_prices())
        self.assertIsNotNone(result)
        self.assertEqual(result.ticker, "TEST")
        self.assertLessEqual(result.score, 100)
        self.assertGreaterEqual(result.score, 0)
        self.assertTrue(result.reasons)

    def test_watchlist_ignores_comments_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tickers.txt"
            path.write_text("# comment\nAAPL\n\naapl\nMSFT\n", encoding="utf-8")
            self.assertEqual(read_tickers(path), ["AAPL", "MSFT"])


if __name__ == "__main__":
    unittest.main()
