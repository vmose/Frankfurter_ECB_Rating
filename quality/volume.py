"""
volume.py — Data Quality / currency source

Checks that each recent partition of the raw table has a plausible
row count: neither suspiciously low (partial ingestion, API returned
a subset) nor suspiciously high (accidental duplicate load).

With DEFAULT_QUOTES having 9 entries (see ingestion/extract.py), each
normal day should load exactly 9 rows. We don't hardcode that number
here though — we compare each recent day against the trailing median,
so this keeps working if the quote currency list changes.

Exit codes: 0 = pass, 2 = fail, 3 = error
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
from datetime import date, datetime

from google.cloud import bigquery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("volume")

LOOKBACK_DAYS = 30
CHECK_DAYS = 5  # how many of the most recent days to actually evaluate
DEVIATION_TOLERANCE = 0.20  # allowed fractional deviation from the trailing median

QUERY = """
select
    rate_date,
    count(*) as row_count
from `{project}.raw.currency_rates_frankfurter`
where rate_date >= date_sub(@today, interval {lookback} day)
group by rate_date
order by rate_date
"""


def check_volume(project: str, today: date | None = None) -> int:
    today = today or date.today()
    client = bigquery.Client(project=project)

    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("today", "DATE", today)]
    )
    query = QUERY.format(project=project, lookback=LOOKBACK_DAYS)

    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:  # noqa: BLE001
        log.error("query failed: %s", exc)
        return 3

    if len(rows) < 2:
        log.warning("not enough history (%d day(s)) to evaluate volume — skipping", len(rows))
        return 0

    counts_by_date = {r.rate_date: r.row_count for r in rows}
    all_counts = list(counts_by_date.values())
    median = statistics.median(all_counts)

    if median == 0:
        log.error("FAIL: trailing median row count is 0")
        return 2

    recent_dates = sorted(counts_by_date)[-CHECK_DAYS:]
    failures = []

    for d in recent_dates:
        count = counts_by_date[d]
        deviation = abs(count - median) / median
        status = "OK" if deviation <= DEVIATION_TOLERANCE else "OUT OF RANGE"
        log.info(
            "%s: row_count=%d (trailing median=%.1f, deviation=%.0f%%) [%s]",
            d,
            count,
            median,
            deviation * 100,
            status,
        )
        if deviation > DEVIATION_TOLERANCE:
            failures.append(d)

    if failures:
        log.error("FAIL: %d recent day(s) outside tolerance: %s", len(failures), failures)
        return 2

    log.info(
        "PASS: all recent partitions within %.0f%% of trailing median", DEVIATION_TOLERANCE * 100
    )
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--as-of", help="Override 'today' for testing, YYYY-MM-DD")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    today = datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else None
    return check_volume(args.project, today)


if __name__ == "__main__":
    raise SystemExit(main())
