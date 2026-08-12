"""
load.py — Public Data Observatory / currency source

Loads raw Parquet partitions written by extract.py into BigQuery.

Behavior
--------
- Ensures the target dataset + table exist (using schema.py as the
  single source of truth for columns/partitioning/clustering).
- Loads one partition directory at a time (dt=YYYY-MM-DD/rates.parquet).
- Uses WRITE_TRUNCATE per-partition (via a partition decorator query)
  rather than WRITE_APPEND, so re-running load.py for a day you've
  already loaded is idempotent instead of duplicating rows. This
  matters a lot for a daily cron job that might get retried.

Usage
-----
    # Load every partition currently on disk
    python load.py --project my-gcp-project --all

    # Load just today's partition (what ingestion.yml calls after extract.py)
    python load.py --project my-gcp-project --date 2026-08-11

    # Load a range (after a backfill)
    python load.py --project my-gcp-project --start 2020-01-01 --end 2020-12-31
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from schema import (
    CLUSTERING_FIELDS,
    FULL_TABLE_ID_TEMPLATE,
    PROJECT_DATASET,
    RAW_CURRENCY_RATES_SCHEMA,
    build_table,
)

RAW_ROOT = Path("data/raw/currency/frankfurter")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("load")


def ensure_dataset(
    client: bigquery.Client, project: str, dataset_id: str = PROJECT_DATASET
) -> None:
    ref = bigquery.DatasetReference(project, dataset_id)
    try:
        client.get_dataset(ref)
    except NotFound:
        log.info("dataset %s.%s not found, creating", project, dataset_id)
        ds = bigquery.Dataset(ref)
        ds.location = "US"
        client.create_dataset(ds)


def ensure_table(client: bigquery.Client, project: str) -> bigquery.Table:
    table_id = FULL_TABLE_ID_TEMPLATE.format(project=project)
    try:
        return client.get_table(table_id)
    except NotFound:
        log.info("table %s not found, creating", table_id)
        table = build_table(project)
        return client.create_table(table)


def load_partition(client: bigquery.Client, project: str, rate_date: date) -> int:
    """
    Load a single dt=YYYY-MM-DD partition into the partition-decorated
    table (WRITE_TRUNCATE), so reruns for the same date are idempotent.
    Returns the number of rows loaded, or 0 if the partition file is missing.
    """
    partition_dir = RAW_ROOT / f"dt={rate_date.isoformat()}"
    file_path = partition_dir / "rates.parquet"

    if not file_path.exists():
        log.warning("no partition file for %s (%s) — skipping", rate_date, file_path)
        return 0

    table_id = FULL_TABLE_ID_TEMPLATE.format(project=project)
    # Partition decorator targets exactly one day's partition for a clean overwrite.
    decorated_table_id = f"{table_id}${rate_date.strftime('%Y%m%d')}"

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        schema=RAW_CURRENCY_RATES_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        time_partitioning=bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY, field="rate_date"
        ),
        clustering_fields=CLUSTERING_FIELDS,
    )

    with open(file_path, "rb") as f:
        job = client.load_table_from_file(f, decorated_table_id, job_config=job_config)
    job.result()  # blocks until done, raises on failure

    if job.errors:
        raise RuntimeError(f"load job for {rate_date} failed: {job.errors}")

    log.info("loaded %d rows for %s -> %s", job.output_rows, rate_date, decorated_table_id)
    return job.output_rows or 0


def discover_partitions(root: Path = RAW_ROOT) -> list[date]:
    """Find every dt=YYYY-MM-DD directory currently on disk, sorted ascending."""
    if not root.exists():
        return []
    dates = []
    for child in root.iterdir():
        if child.is_dir() and child.name.startswith("dt="):
            try:
                dates.append(datetime.strptime(child.name[3:], "%Y-%m-%d").date())
            except ValueError:
                log.warning("skipping unrecognized partition dir: %s", child)
    return sorted(dates)


def daterange(start: date, end: date) -> list[date]:
    days = (end - start).days
    return [start + timedelta(days=i) for i in range(days + 1)]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="GCP project ID")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Load every partition found on disk")
    group.add_argument("--date", help="Load a single partition, YYYY-MM-DD")
    group.add_argument("--start", help="Range start, YYYY-MM-DD (requires --end)")
    parser.add_argument("--end", help="Range end, YYYY-MM-DD (used with --start)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.start and not args.end:
        log.error("--start requires --end")
        return 2

    client = bigquery.Client(project=args.project)
    ensure_dataset(client, args.project)
    ensure_table(client, args.project)

    if args.all:
        dates = discover_partitions()
        if not dates:
            log.warning("no partitions found under %s", RAW_ROOT)
            return 0
    elif args.date:
        dates = [datetime.strptime(args.date, "%Y-%m-%d").date()]
    else:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date()
        dates = daterange(start, end)

    total_rows = 0
    failures = 0
    for d in dates:
        try:
            total_rows += load_partition(client, args.project, d)
        except Exception as exc:  # noqa: BLE001 — log and keep going across a batch
            log.error("failed to load %s: %s", d, exc)
            failures += 1

    log.info(
        "done: %d rows loaded across %d partitions, %d failures", total_rows, len(dates), failures
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
