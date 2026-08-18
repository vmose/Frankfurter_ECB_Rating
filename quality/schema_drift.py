"""
schema_drift.py — Data Quality / currency source

Compares the live BigQuery schema of the raw table against the
expected schema defined in ingestion/schema.py. Catches upstream API
changes (Frankfurter has done a v1 -> v2 migration before) or manual
table edits before they silently break dbt models downstream.

Checks for:
    - missing expected columns
    - unexpected new columns (informational — not a failure by default,
      since a new column shouldn't break existing models)
    - type mismatches on any shared column (a real failure)
    - mode mismatches, e.g. a REQUIRED column becoming NULLABLE
      (usually a sign the upstream source is now sending partial rows)

Exit codes: 0 = pass, 2 = fail, 3 = error
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from google.cloud import bigquery

# Import the expected schema from the ingestion package rather than
# redefining it — this file is the drift *check*, schema.py is the
# source of truth.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingestion"))
from schema import FULL_TABLE_ID_TEMPLATE, RAW_CURRENCY_RATES_SCHEMA  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("schema_drift")


def check_schema_drift(project: str) -> int:
    client = bigquery.Client(project=project)
    table_id = FULL_TABLE_ID_TEMPLATE.format(project=project)

    try:
        live_table = client.get_table(table_id)
    except Exception as exc:  # noqa: BLE001
        log.error("could not fetch live table %s: %s", table_id, exc)
        return 3

    expected = {f.name: f for f in RAW_CURRENCY_RATES_SCHEMA}
    live = {f.name: f for f in live_table.schema}

    missing = sorted(set(expected) - set(live))
    new = sorted(set(live) - set(expected))
    type_mismatches = []
    mode_mismatches = []

    for name in sorted(set(expected) & set(live)):
        exp_field, live_field = expected[name], live[name]
        if exp_field.field_type != live_field.field_type:
            type_mismatches.append((name, exp_field.field_type, live_field.field_type))
        # Only flag REQUIRED -> NULLABLE drift; NULLABLE -> REQUIRED is a
        # tightening, not a risk to downstream models.
        if exp_field.mode == "REQUIRED" and live_field.mode != "REQUIRED":
            mode_mismatches.append((name, exp_field.mode, live_field.mode))

    if missing:
        log.error("missing expected column(s): %s", missing)
    if new:
        log.info("new column(s) not in expected schema (informational): %s", new)
    if type_mismatches:
        for name, exp_t, live_t in type_mismatches:
            log.error("type drift on '%s': expected %s, live %s", name, exp_t, live_t)
    if mode_mismatches:
        for name, exp_m, live_m in mode_mismatches:
            log.error("mode drift on '%s': expected %s, live %s", name, exp_m, live_m)

    if missing or type_mismatches or mode_mismatches:
        log.error("FAIL: schema drift detected")
        return 2

    log.info("PASS: live schema matches expected schema (%d new informational column(s))", len(new))
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    return check_schema_drift(args.project)


if __name__ == "__main__":
    raise SystemExit(main())
