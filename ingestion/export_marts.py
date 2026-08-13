"""
export_marts.py — bridges dbt marts (BigQuery) to the static dashboard.

The dashboard (dashboard/app.js) is a plain static site with no backend
and no BigQuery credentials of its own. This script is the last step
of deploy.yml: it queries the marts dbt built and writes three small
JSON files into dashboard/data/, which is what actually gets published.

Writes:
    dashboard/data/latest.json   <- mart_currency_latest
    dashboard/data/history.json  <- mart_currency_history, grouped by pair
    dashboard/data/quality.json  <- pass/warn/fail per quality check
                                     (passed in via CLI flags by the
                                     workflow step that ran each check)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from google.cloud import bigquery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("export_marts")

DEFAULT_OUT_DIR = Path("dashboard/data")

LATEST_QUERY = """
select
    base_currency,
    quote_currency,
    source_name,
    as_of_date,
    latest_rate,
    prior_rate,
    pct_change_since_prior,
    days_since_prior_rate
from `{project}.marts.mart_currency_latest`
order by base_currency, quote_currency
"""

HISTORY_QUERY = """
select
    base_currency,
    quote_currency,
    as_of_date,
    rate
from `{project}.marts.mart_currency_history`
order by base_currency, quote_currency, as_of_date
"""

# Exit codes used by quality/*.py — kept in sync with those scripts.
EXIT_CODE_LABELS = {0: "pass", 1: "warn", 2: "fail", 3: "error"}


def export_latest(client: bigquery.Client, project: str, out_dir: Path) -> int:
    rows = list(client.query(LATEST_QUERY.format(project=project)).result())
    payload = [
        {
            "pair": f"{r.base_currency}/{r.quote_currency}",
            "base": r.base_currency,
            "quote": r.quote_currency,
            "source": r.source_name,
            "as_of_date": r.as_of_date.isoformat(),
            "rate": r.latest_rate,
            "prior_rate": r.prior_rate,
            "pct_change": r.pct_change_since_prior,
            "days_since_prior": r.days_since_prior_rate,
        }
        for r in rows
    ]
    out_path = out_dir / "latest.json"
    out_path.write_text(json.dumps(payload, indent=2))
    log.info("wrote %d rows -> %s", len(payload), out_path)
    return len(payload)


def export_history(client: bigquery.Client, project: str, out_dir: Path) -> int:
    rows = list(client.query(HISTORY_QUERY.format(project=project)).result())
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        pair = f"{r.base_currency}/{r.quote_currency}"
        grouped.setdefault(pair, []).append({"date": r.as_of_date.isoformat(), "rate": r.rate})

    out_path = out_dir / "history.json"
    out_path.write_text(json.dumps(grouped, indent=2))
    log.info("wrote %d pairs (%d rows total) -> %s", len(grouped), len(rows), out_path)
    return len(rows)


def export_quality(results: dict[str, int], out_dir: Path) -> None:
    """
    `results` maps check name -> exit code, e.g.
        {"freshness": 0, "volume": 0, "reconciliation": 1, "schema_drift": 0}
    as passed in by the GitHub Actions step that ran each quality/*.py script.
    """
    payload = {name: EXIT_CODE_LABELS.get(code, "unknown") for name, code in results.items()}
    payload["checked_at"] = datetime.now(UTC).isoformat()

    out_path = out_dir / "quality.json"
    out_path.write_text(json.dumps(payload, indent=2))
    log.info("wrote quality summary -> %s: %s", out_path, payload)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="GCP project ID")
    parser.add_argument(
        "--out-dir", default=str(DEFAULT_OUT_DIR), help="Directory to write JSON files into"
    )
    parser.add_argument(
        "--quality-result",
        action="append",
        default=[],
        metavar="NAME=EXIT_CODE",
        help="Repeatable. e.g. --quality-result freshness=0 --quality-result volume=0",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    client = bigquery.Client(project=args.project)

    try:
        export_latest(client, args.project, out_dir)
        export_history(client, args.project, out_dir)
    except Exception as exc:  # noqa: BLE001
        log.error("export failed: %s", exc)
        return 1

    if args.quality_result:
        results = {}
        for item in args.quality_result:
            if "=" not in item:
                log.warning("skipping malformed --quality-result %r", item)
                continue
            name, code = item.split("=", 1)
            try:
                results[name] = int(code)
            except ValueError:
                log.warning("skipping non-integer exit code in %r", item)
        export_quality(results, out_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
