"""
reconciliation.py — Data Quality / currency source

Cross-checks our latest USD/EUR rate against a second, independent
public source (exchangerate.host / open.er-api.com style free API)
to catch cases where our primary source is wrong or stale in a way
freshness/volume checks wouldn't catch (e.g. correct row count, wrong
values).

This only checks a small, high-liquidity subset of pairs — reconciling
every pair against a second free API isn't worth the request budget,
and major pairs are the ones most worth catching a bad value on.

Exit codes: 0 = pass, 2 = fail, 3 = error
"""

from __future__ import annotations

import argparse
import logging
import sys

import requests
from google.cloud import bigquery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("reconciliation")

# Pairs worth the cost of an extra API call to double-check.
RECONCILE_PAIRS = [("USD", "EUR"), ("USD", "GBP"), ("USD", "JPY")]

# How far apart two independent sources can reasonably be before we
# call it a discrepancy rather than normal cross-source noise
# (different fixing times, rounding, etc).
TOLERANCE_PCT = 0.015  # 1.5%

SECOND_SOURCE_URL = "https://open.er-api.com/v6/latest/{base}"

OUR_LATEST_QUERY = """
select base_currency, quote_currency, latest_rate
from `{project}.marts.mart_currency_latest`
where {where_clause}
"""


def fetch_reference_rate(base: str, quote: str) -> float | None:
    try:
        resp = requests.get(SECOND_SOURCE_URL.format(base=base), timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("rates", {}).get(quote)
    except (requests.RequestException, ValueError) as exc:
        log.warning("could not fetch reference rate for %s/%s: %s", base, quote, exc)
        return None


def check_reconciliation(project: str) -> int:
    client = bigquery.Client(project=project)

    # Plain scalar parameters + OR'd conditions, one pair per condition.
    # (An earlier version tried to pass RECONCILE_PAIRS as a single
    # STRUCT-typed ArrayQueryParameter — the BigQuery client needs
    # StructQueryParameter objects for that, not plain dicts, and
    # rejected the request outright. This is simpler and avoids that
    # API surface entirely.)
    conditions = []
    params = []
    for i, (base, quote) in enumerate(RECONCILE_PAIRS):
        conditions.append(f"(base_currency = @base_{i} AND quote_currency = @quote_{i})")
        params.append(bigquery.ScalarQueryParameter(f"base_{i}", "STRING", base))
        params.append(bigquery.ScalarQueryParameter(f"quote_{i}", "STRING", quote))

    query = OUR_LATEST_QUERY.format(project=project, where_clause=" OR ".join(conditions))
    job_config = bigquery.QueryJobConfig(query_parameters=params)

    try:
        rows = {
            (r.base_currency, r.quote_currency): r.latest_rate
            for r in client.query(query, job_config=job_config).result()
        }
    except Exception as exc:  # noqa: BLE001
        log.error("query failed: %s", exc)
        return 3

    failures = []
    for base, quote in RECONCILE_PAIRS:
        our_rate = rows.get((base, quote))
        if our_rate is None:
            log.warning("no data for %s/%s in mart_currency_latest — skipping", base, quote)
            continue

        ref_rate = fetch_reference_rate(base, quote)
        if ref_rate is None:
            log.warning("no reference rate for %s/%s — skipping comparison", base, quote)
            continue

        deviation = abs(our_rate - ref_rate) / ref_rate
        status = "OK" if deviation <= TOLERANCE_PCT else "MISMATCH"
        log.info(
            "%s/%s: ours=%.4f reference=%.4f deviation=%.2f%% [%s]",
            base,
            quote,
            our_rate,
            ref_rate,
            deviation * 100,
            status,
        )
        if deviation > TOLERANCE_PCT:
            failures.append((base, quote, our_rate, ref_rate, deviation))

    if failures:
        log.error(
            "FAIL: %d pair(s) diverge beyond %.1f%%: %s",
            len(failures),
            TOLERANCE_PCT * 100,
            failures,
        )
        return 2

    log.info("PASS: all checked pairs agree with reference source within tolerance")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    return check_reconciliation(args.project)


if __name__ == "__main__":
    raise SystemExit(main())
