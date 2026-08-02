from datetime import date
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from market_day import is_nyse_trading_day


class MarketDayTests(unittest.TestCase):
    def test_regular_weekday_is_open(self):
        self.assertTrue(is_nyse_trading_day(date(2026, 1, 2)))

    def test_nyse_holiday_is_closed(self):
        self.assertFalse(is_nyse_trading_day(date(2026, 1, 1)))

    def test_weekend_is_closed(self):
        self.assertFalse(is_nyse_trading_day(date(2026, 1, 3)))


if __name__ == "__main__":
    unittest.main()
