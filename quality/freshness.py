"""
freshness.py — Data Quality / currency source

Checks that mart_currency_latest.as_of_date is recent enough to trust.

Key design point: the ECB does not publish rates on weekends or EU
bank holidays, so "no new data today" is often EXPECTED, not a
failure. We treat staleness as a function of the most recent expected
publish day, not just "yesterday".

Exit codes (for CI):
    0 = pass
    1 = warn  (stale, but within the grace window)
    2 = fail  (stale beyond the grace window)
    3 = error (couldn't run the check at all)
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta

from google.cloud import bigquery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("freshness")

WARN_AFTER_DAYS = 3  # e.g. a long weekend / bank holiday cluster
FAIL_AFTER_DAYS = 5  # beyond any plausible ECB non-publish streak

QUERY = """
select max(as_of_date) as latest_date
from `{project}.marts.mart_currency_latest`
"""


def last_expected_publish_day(today: date) -> date:
    """
    Rough heuristic: ECB doesn't publish on Sat/Sun. This does NOT
    account for EU bank holidays (that list changes yearly and isn't
    worth hardcoding here) — hence the multi-day grace window above
    rather than a same-day check.
    """
    d = today
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d -= timedelta(days=1)
    return d


def check_freshness(project: str, today: date | None = None) -> int:
    today = today or date.today()
    client = bigquery.Client(project=project)

    try:
        rows = list(client.query(QUERY.format(project=project)).result())
    except Exception as exc:  # noqa: BLE001
        log.error("query failed: %s", exc)
        return 3

    if not rows or rows[0].latest_date is None:
        log.error("mart_currency_latest is empty")
        return 3

    latest_date = rows[0].latest_date
    expected = last_expected_publish_day(today)
    lag_days = (expected - latest_date).days

    log.info(
        "latest mart date=%s | last expected publish day=%s | lag=%d day(s)",
        latest_date,
        expected,
        lag_days,
    )

    if lag_days <= 0:
        log.info("PASS: data is current")
        return 0
    if lag_days <= WARN_AFTER_DAYS:
        log.warning("WARN: %d day(s) behind expected publish day — could be a holiday", lag_days)
        return 1
    log.error("FAIL: %d day(s) behind expected publish day — pipeline likely broken", lag_days)
    return 2


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--as-of", help="Override 'today' for testing, YYYY-MM-DD")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    today = datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else None
    return check_freshness(args.project, today)


if __name__ == "__main__":
    raise SystemExit(main())
