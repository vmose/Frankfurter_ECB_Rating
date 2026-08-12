"""
schema.py — BigQuery schema for the raw currency rates table.

Kept separate from extract.py and load.py so both can import the same
source of truth: extract.py can validate its Parquet output against
it, load.py can use it to create the table / load job.

Table: `<project>.raw.currency_rates_frankfurter`
Grain: one row per (rate_date, base_currency, quote_currency, source)
"""

from __future__ import annotations

from google.cloud import bigquery

PROJECT_DATASET = "raw"
TABLE_NAME = "currency_rates_frankfurter"
FULL_TABLE_ID_TEMPLATE = "{project}." + PROJECT_DATASET + "." + TABLE_NAME

# --- Column definitions -----------------------------------------------

RAW_CURRENCY_RATES_SCHEMA = [
    bigquery.SchemaField(
        "rate_date",
        "DATE",
        mode="REQUIRED",
        description="Actual ECB reference rate date (may lag the requested "
        "date on weekends/EU bank holidays).",
    ),
    bigquery.SchemaField(
        "base_currency",
        "STRING",
        mode="REQUIRED",
        description="ISO 4217 base currency code, e.g. USD.",
    ),
    bigquery.SchemaField(
        "quote_currency",
        "STRING",
        mode="REQUIRED",
        description="ISO 4217 quote currency code, e.g. EUR.",
    ),
    bigquery.SchemaField(
        "rate",
        "FLOAT64",
        mode="REQUIRED",
        description="Units of quote_currency per 1 unit of base_currency.",
    ),
    bigquery.SchemaField(
        "source",
        "STRING",
        mode="REQUIRED",
        description="Ingestion source identifier, e.g. frankfurter_ecb. "
        "Lets reconciliation.py join against a second source later.",
    ),
    bigquery.SchemaField(
        "ingested_at",
        "TIMESTAMP",
        mode="REQUIRED",
        description="UTC timestamp when this row was pulled from the API "
        "(not when the rate was published). Drives freshness.py.",
    ),
]

# --- Table config --------------------------------------------------------

# Partition on rate_date: keeps daily loads cheap and lets freshness.py
# query only the most recent partitions instead of scanning the whole table.
TIME_PARTITIONING = bigquery.TimePartitioning(
    type_=bigquery.TimePartitioningType.DAY,
    field="rate_date",
)

# Cluster on the columns quality/analytics queries filter on most often.
CLUSTERING_FIELDS = ["base_currency", "quote_currency", "source"]


def build_table(project: str) -> bigquery.Table:
    """Construct a (not-yet-created) bigquery.Table object for load.py to apply."""
    table_id = FULL_TABLE_ID_TEMPLATE.format(project=project)
    table = bigquery.Table(table_id, schema=RAW_CURRENCY_RATES_SCHEMA)
    table.time_partitioning = TIME_PARTITIONING
    table.clustering_fields = CLUSTERING_FIELDS
    table.description = (
        "Raw ECB reference exchange rates ingested from the Frankfurter API. "
        "One row per (rate_date, base_currency, quote_currency)."
    )
    return table


# --- Equivalent DDL, for anyone who'd rather run this by hand in the console ---

RAW_CURRENCY_RATES_DDL = f"""
CREATE TABLE IF NOT EXISTS `{PROJECT_DATASET}.{TABLE_NAME}` (
  rate_date       DATE      NOT NULL OPTIONS(description="Actual ECB rate date"),
  base_currency   STRING    NOT NULL,
  quote_currency  STRING    NOT NULL,
  rate            FLOAT64   NOT NULL,
  source          STRING    NOT NULL,
  ingested_at     TIMESTAMP NOT NULL
)
PARTITION BY rate_date
CLUSTER BY base_currency, quote_currency, source;
""".strip()


if __name__ == "__main__":
    # Quick sanity check when run directly: print the DDL.
    print(RAW_CURRENCY_RATES_DDL)
