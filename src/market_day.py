import argparse
from datetime import date, datetime
import os
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal


EASTERN_TIME = ZoneInfo("America/New_York")


def is_nyse_trading_day(day: date) -> bool:
    """Return whether the NYSE has a trading session on the given date."""
    calendar = mcal.get_calendar("NYSE")
    return not calendar.valid_days(
        start_date=day.isoformat(),
        end_date=day.isoformat(),
    ).empty


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the NYSE trading calendar.")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Date to check (YYYY-MM-DD); defaults to today in New York.",
    )
    args = parser.parse_args()
    day = args.date or datetime.now(EASTERN_TIME).date()
    market_open = is_nyse_trading_day(day)
    value = str(market_open).lower()
    print(f"NYSE trading day for {day}: {value}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as output:
            output.write(f"market_open={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
