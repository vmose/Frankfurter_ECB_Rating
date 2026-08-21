"""
extract.py — Public Data Observatory / currency source

Pulls ECB reference exchange rates from the Frankfurter API
(https://api.frankfurter.dev/v2/rates) and writes them as raw Parquet
files, partitioned by the ACTUAL rate date returned by the API.

API shape (confirmed against api.frankfurter.dev docs)
--------------------------------------------------------
- One endpoint: GET /v2/rates. No separate "/latest" or "/{date}"
  paths — history and time series are query params on the same
  endpoint (`date=`, or `from=`/`to=`).
- The response is a FLAT ARRAY of rows, each already shaped as
  {date, base, quote, rate} — not a single object with a nested
  `rates` dict.
- Different currencies in the SAME response can carry DIFFERENT
  dates. Slower-publishing currencies lag behind faster ones even on
  a plain "give me the latest" call. This isn't just a weekend
  phenomenon — it's routine. So a single extraction run may need to
  write rows into more than one date partition.
- By default, rates are blended across ALL contributing providers,
  not ECB alone. We explicitly pass `providers=ECB` to get pure ECB
  reference rates, since the rest of this pipeline (freshness.py's
  weekend/holiday heuristic, the dashboard, dbt tests) assumes ECB's
  publish calendar specifically.

Design notes
------------
- No API key, but the API is rate-limited (no hard quota, per the
  docs) — fetch_rates() retries with backoff on 429/5xx.
- Backfill uses the `from`/`to` time-series query, chunked by
  calendar year, instead of one request per day. Much fewer requests,
  and matches how the API is meant to be used (the docs explicitly
  recommend narrowing currencies + streaming for large ranges).
- Every row keeps its own true rate_date; we group by that date and
  write one partition per distinct date found, rather than assuming
  a single date per run.

Usage
-----
    python extract.py --mode latest
    python extract.py --mode backfill --start 2020-01-01 --end 2020-12-31
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import requests

API_BASE = "https://api.frankfurter.dev/v2"
RATES_ENDPOINT = f"{API_BASE}/rates"
SOURCE_NAME = "frankfurter_ecb"
RAW_ROOT = Path("data/raw/currency/frankfurter")

DEFAULT_QUOTES = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "CNY", "INR", "KES"]
DEFAULT_BASE = "USD"
DEFAULT_PROVIDERS = "ECB"  # pure ECB reference rates, not the blended default

BACKFILL_CHUNK_DAYS = 366  # one calendar year at a time
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0

HEADERS = {"User-Agent": "public-data-observatory/1.0"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("extract")


class FrankfurterError(RuntimeError):
    """Raised when the API responds with something we can't safely ingest."""


def _get_with_retry(session: requests.Session, url: str, params: dict) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, params=params, headers=HEADERS, timeout=30)
        except requests.RequestException as exc:
            last_exc = exc
            log.warning("request error (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
        else:
            if resp.status_code == 429 or resp.status_code >= 500:
                log.warning(
                    "got %d (attempt %d/%d), backing off", resp.status_code, attempt, MAX_RETRIES
                )
                last_exc = FrankfurterError(f"status {resp.status_code}: {resp.text[:200]}")
            else:
                return resp
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise FrankfurterError(f"request to {url} failed after {MAX_RETRIES} attempts: {last_exc}")


def fetch_rates(
    base: str,
    quotes: list[str],
    providers: str | None = DEFAULT_PROVIDERS,
    date_param: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    session: requests.Session | None = None,
) -> list[dict]:
    """
    Call GET /v2/rates and return the parsed JSON array of row dicts.

    - No date/from/to  -> latest rates
    - date_param only   -> rates for one specific historical date
    - date_from/date_to -> a time series across that range

    Raises FrankfurterError on a non-2xx response, an error body
    ({"message": ...}), or a response that isn't a non-empty list of
    well-formed rows.
    """
    sess = session or requests.Session()

    params: dict = {"base": base, "quotes": ",".join(quotes)}
    if providers:
        params["providers"] = providers
    if date_param:
        params["date"] = date_param
    if date_from:
        params["from"] = date_from
    if date_to:
        params["to"] = date_to

    resp = _get_with_retry(sess, RATES_ENDPOINT, params)

    if resp.status_code != 200:
        # Documented error shape: {"message": "..."} on 400/404/422.
        try:
            body = resp.json()
            msg = (
                body.get("message", resp.text[:200]) if isinstance(body, dict) else resp.text[:200]
            )
        except ValueError:
            msg = resp.text[:200]
        raise FrankfurterError(f"status {resp.status_code} for {resp.url}: {msg}")

    payload = resp.json()

    if isinstance(payload, dict):
        # Shouldn't happen on a 200, but handle it defensively rather
        # than crash on payload.get(...) below returning wrong type.
        raise FrankfurterError(f"unexpected object response: {payload}")

    if not isinstance(payload, list) or not payload:
        raise FrankfurterError(f"empty or malformed rates array for {resp.url}: {payload!r}")

    for row in payload:
        for field in ("date", "base", "quote", "rate"):
            if field not in row:
                raise FrankfurterError(f"row missing '{field}': {row}")

    return payload


def payload_to_frame(payload: list[dict]) -> pd.DataFrame:
    """
    Reshape the flat Frankfurter row array into our internal column
    names. Each row already carries its own rate_date — we do NOT
    assume one uniform date across the whole payload.
    """
    ingested_at = datetime.now(UTC).isoformat()
    df = pd.DataFrame(
        [
            {
                "rate_date": row["date"],
                "base_currency": row["base"],
                "quote_currency": row["quote"],
                "rate": row["rate"],
                "source": SOURCE_NAME,
                "ingested_at": ingested_at,
            }
            for row in payload
        ]
    )
    df["rate_date"] = pd.to_datetime(df["rate_date"]).dt.date
    df["rate"] = df["rate"].astype("float64")
    return df


def write_partition(df: pd.DataFrame, rate_date: date, out_root: Path = RAW_ROOT) -> Path:
    """Write one date's rows to data/raw/currency/frankfurter/dt=YYYY-MM-DD/rates.parquet."""
    partition_dir = out_root / f"dt={rate_date.isoformat()}"
    partition_dir.mkdir(parents=True, exist_ok=True)
    out_path = partition_dir / "rates.parquet"
    df.to_parquet(out_path, index=False)
    log.info("wrote %d rows -> %s", len(df), out_path)
    return out_path


def write_by_date(df: pd.DataFrame, out_root: Path = RAW_ROOT) -> list[Path]:
    """
    Split a (possibly multi-date) DataFrame by rate_date and write one
    partition file per distinct date found. This is what makes
    per-currency publish lag land in the right partitions instead of
    getting stamped with a single "today" date.
    """
    written = []
    for rate_date, group in df.groupby("rate_date"):
        written.append(write_partition(group, rate_date, out_root=out_root))
    return written


def run_latest(
    base: str, quotes: list[str], providers: str | None = DEFAULT_PROVIDERS
) -> list[Path]:
    payload = fetch_rates(base=base, quotes=quotes, providers=providers)
    df = payload_to_frame(payload)
    dates_found = sorted(df["rate_date"].unique())
    if len(dates_found) > 1:
        log.info(
            "latest pull spans %d dates (per-currency publish lag): %s",
            len(dates_found),
            dates_found,
        )
    return write_by_date(df)


def _chunk_date_range(start: date, end: date, max_days: int = BACKFILL_CHUNK_DAYS):
    """Yield (chunk_start, chunk_end) pairs covering [start, end], inclusive, in bounded chunks."""
    from datetime import timedelta

    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=max_days - 1), end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def run_backfill(
    base: str, quotes: list[str], start: date, end: date, providers: str | None = DEFAULT_PROVIDERS
) -> list[Path]:
    """
    Fetch the full range via the time-series query (from/to), chunked
    by calendar year so a multi-decade backfill doesn't ride on one
    giant, fragile request. A failed chunk is logged and skipped
    rather than aborting the whole backfill.
    """
    written: list[Path] = []
    session = requests.Session()

    for chunk_start, chunk_end in _chunk_date_range(start, end):
        try:
            payload = fetch_rates(
                base=base,
                quotes=quotes,
                providers=providers,
                date_from=chunk_start.isoformat(),
                date_to=chunk_end.isoformat(),
                session=session,
            )
            df = payload_to_frame(payload)
            written.extend(write_by_date(df))
            log.info(
                "chunk %s..%s: %d rows across %d date(s)",
                chunk_start,
                chunk_end,
                len(df),
                df["rate_date"].nunique(),
            )
        except FrankfurterError as exc:
            log.error("skipping chunk %s..%s: %s", chunk_start, chunk_end, exc)

    return written


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE, help="Base currency (default: USD)")
    parser.add_argument(
        "--quotes", default=",".join(DEFAULT_QUOTES), help="Comma-separated quote currencies"
    )
    parser.add_argument(
        "--providers",
        default=DEFAULT_PROVIDERS,
        help="Provider filter (default: ECB). Pass '' to use blended rates across all providers.",
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
    providers = args.providers or None

    if args.mode == "latest":
        try:
            run_latest(base=args.base.upper(), quotes=quotes, providers=providers)
        except FrankfurterError as exc:
            log.error("failed to fetch latest rates: %s", exc)
            return 1
        return 0

    if not args.start or not args.end:
        log.error("--start and --end are required for --mode backfill")
        return 2
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    if start > end:
        log.error("--start must be <= --end")
        return 2

    written = run_backfill(
        base=args.base.upper(), quotes=quotes, start=start, end=end, providers=providers
    )
    log.info("backfill complete: %d partition file(s) written", len(written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
