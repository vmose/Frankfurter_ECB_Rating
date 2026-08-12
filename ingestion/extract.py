"""
extract.py — Public Data Observatory / currency source

Pulls ECB reference exchange rates from the Frankfurter API
(https://api.frankfurter.dev) and writes them as raw Parquet files,
partitioned by the ACTUAL rate date returned by the API (not the
requested date — see NOTE below).

Design notes
------------
- Frankfurter has no API key and no documented rate limit, so this
  script is intentionally simple: one request per run.
- The ECB does not publish rates on weekends or EU bank holidays.
  On those days Frankfurter returns the most recent available rate,
  with `date` in the payload reflecting the real rate date. We always
  partition on that returned date, not on today's date, so:
    * freshness.py can correctly treat "no new partition on a
      weekend/holiday" as expected, not a failure.
    * we never silently duplicate the same day's rates under two
      different partition dates.
- Designed to run either:
    * daily (GitHub Actions cron)      -> fetch latest only
    * backfill (manual / one-off)      -> fetch a historical range

Output layout (matches the "Raw Parquet" stage of the pipeline):
    data/raw/currency/frankfurter/dt=YYYY-MM-DD/rates.parquet

Each row is one (base, quote) pair for one rate date, e.g.:
    rate_date, base_currency, quote_currency, rate, source, ingested_at
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

API_BASE = "https://api.frankfurter.dev/v2"
SOURCE_NAME = "frankfurter_ecb"
RAW_ROOT = Path("data/raw/currency/frankfurter")

# Keep the currency set explicit and small at first — easier to reason
# about in dbt staging models, and easy to widen later.
DEFAULT_QUOTES = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "CNY", "INR", "KES"]
DEFAULT_BASE = "USD"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("extract")


class FrankfurterError(RuntimeError):
    """Raised when the API responds with something we can't safely ingest."""


def fetch_rates(
    base: str,
    quotes: list[str],
    rate_date: str | None = None,
    session: requests.Session | None = None,
) -> dict:
    """
    Fetch rates for a single date (or 'latest' if rate_date is None).

    Returns the parsed JSON payload. Raises FrankfurterError on
    anything that isn't a clean 200 with the fields we expect.
    """
    sess = session or requests.Session()
    path = rate_date if rate_date else "latest"
    url = f"{API_BASE}/{path}"
    params = {"base": base, "quotes": ",".join(quotes)}

    try:
        resp = sess.get(url, params=params, timeout=15)
    except requests.RequestException as exc:
        raise FrankfurterError(f"request failed for {url}: {exc}") from exc

    if resp.status_code != 200:
        raise FrankfurterError(
            f"unexpected status {resp.status_code} for {url}: {resp.text[:200]}"
        )

    payload = resp.json()
    for field in ("base", "date", "rates"):
        if field not in payload:
            raise FrankfurterError(f"missing '{field}' in response: {payload}")

    if not payload["rates"]:
        raise FrankfurterError(f"empty rates dict for {url}: {payload}")

    return payload


def payload_to_frame(payload: dict) -> pd.DataFrame:
    """Reshape the Frankfurter JSON payload into a tidy long-format DataFrame."""
    ingested_at = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "rate_date": payload["date"],  # actual ECB rate date, may lag requested date
            "base_currency": payload["base"],
            "quote_currency": quote,
            "rate": rate,
            "source": SOURCE_NAME,
            "ingested_at": ingested_at,
        }
        for quote, rate in payload["rates"].items()
    ]
    df = pd.DataFrame(rows)
    df["rate_date"] = pd.to_datetime(df["rate_date"]).dt.date
    df["rate"] = df["rate"].astype("float64")
    return df


def write_partition(df: pd.DataFrame, rate_date: date, out_root: Path = RAW_ROOT) -> Path:
    """Write one date's rates to data/raw/currency/frankfurter/dt=YYYY-MM-DD/rates.parquet."""
    partition_dir = out_root / f"dt={rate_date.isoformat()}"
    partition_dir.mkdir(parents=True, exist_ok=True)
    out_path = partition_dir / "rates.parquet"
    df.to_parquet(out_path, index=False)
    log.info("wrote %d rows -> %s", len(df), out_path)
    return out_path


def run_latest(base: str, quotes: list[str]) -> Path:
    payload = fetch_rates(base=base, quotes=quotes)
    df = payload_to_frame(payload)
    rate_date = df["rate_date"].iloc[0]
    return write_partition(df, rate_date)


def run_backfill(base: str, quotes: list[str], start: date, end: date) -> list[Path]:
    """
    Fetch one date at a time rather than using Frankfurter's range endpoint,
    so each calendar day still lands as its own partition even when the
    underlying rate_date repeats across a weekend/holiday run.
    """
    written = []
    session = requests.Session()
    current = start
    while current <= end:
        try:
            payload = fetch_rates(
                base=base, quotes=quotes, rate_date=current.isoformat(), session=session
            )
            df = payload_to_frame(payload)
            written.append(write_partition(df, current))
        except FrankfurterError as exc:
            # Log and continue — a single bad day shouldn't kill a multi-year backfill.
            log.error("skipping %s: %s", current, exc)
        current += timedelta(days=1)
    return written


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE, help="Base currency (default: USD)")
    parser.add_argument(
        "--quotes",
        default=",".join(DEFAULT_QUOTES),
        help="Comma-separated quote currencies",
    )
    parser.add_argument(
        "--mode",
        choices=["latest", "backfill"],
        default="latest",
        help="'latest' for the daily cron job, 'backfill' for a historical range",
    )
    parser.add_argument("--start", help="Backfill start date, YYYY-MM-DD (mode=backfill)")
    parser.add_argument("--end", help="Backfill end date, YYYY-MM-DD (mode=backfill)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    quotes = [q.strip().upper() for q in args.quotes.split(",") if q.strip()]

    if args.mode == "latest":
        try:
            run_latest(base=args.base.upper(), quotes=quotes)
        except FrankfurterError as exc:
            log.error("failed to fetch latest rates: %s", exc)
            return 1
        return 0

    # backfill mode
    if not args.start or not args.end:
        log.error("--start and --end are required for --mode backfill")
        return 2
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    if start > end:
        log.error("--start must be <= --end")
        return 2

    written = run_backfill(base=args.base.upper(), quotes=quotes, start=start, end=end)
    log.info("backfill complete: %d partitions written", len(written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())