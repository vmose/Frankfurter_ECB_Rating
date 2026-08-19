"""
Unit tests for quality/freshness.py, volume.py, reconciliation.py, and
schema_drift.py.

The BigQuery client is always mocked — these tests never touch a real
warehouse or the network, so they run fine in CI with no credentials.
"""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "quality"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingestion"))

import freshness  # noqa: E402
import reconciliation  # noqa: E402
import schema_drift  # noqa: E402
import volume  # noqa: E402
from google.cloud import bigquery  # noqa: E402

# --------------------------- freshness.py ---------------------------


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 8, 11), date(2026, 8, 11)),  # Tuesday -> itself
        (date(2026, 8, 8), date(2026, 8, 7)),  # Saturday -> Friday
        (date(2026, 8, 9), date(2026, 8, 7)),  # Sunday -> Friday
        (date(2026, 8, 10), date(2026, 8, 10)),  # Monday -> itself
    ],
)
def test_last_expected_publish_day(today, expected):
    assert freshness.last_expected_publish_day(today) == expected


def test_check_freshness_passes_when_current():
    mock_client = MagicMock()
    mock_client.query.return_value.result.return_value = [MagicMock(latest_date=date(2026, 8, 11))]
    with patch.object(freshness.bigquery, "Client", return_value=mock_client):
        code = freshness.check_freshness("proj", today=date(2026, 8, 11))
    assert code == 0


def test_check_freshness_warns_within_grace_window():
    mock_client = MagicMock()
    # 2 days stale relative to Tuesday's expected publish day
    mock_client.query.return_value.result.return_value = [MagicMock(latest_date=date(2026, 8, 9))]
    with patch.object(freshness.bigquery, "Client", return_value=mock_client):
        code = freshness.check_freshness("proj", today=date(2026, 8, 11))
    assert code == 1


def test_check_freshness_fails_when_very_stale():
    mock_client = MagicMock()
    mock_client.query.return_value.result.return_value = [MagicMock(latest_date=date(2026, 8, 1))]
    with patch.object(freshness.bigquery, "Client", return_value=mock_client):
        code = freshness.check_freshness("proj", today=date(2026, 8, 11))
    assert code == 2


def test_check_freshness_errors_on_empty_result():
    mock_client = MagicMock()
    mock_client.query.return_value.result.return_value = []
    with patch.object(freshness.bigquery, "Client", return_value=mock_client):
        code = freshness.check_freshness("proj", today=date(2026, 8, 11))
    assert code == 3


# --------------------------- volume.py ---------------------------


def _row(rate_date, row_count):
    return MagicMock(rate_date=rate_date, row_count=row_count)


def test_check_volume_passes_when_stable():
    rows = [_row(date(2026, 8, d), 9) for d in range(1, 12)]
    mock_client = MagicMock()
    mock_client.query.return_value.result.return_value = rows
    with patch.object(volume.bigquery, "Client", return_value=mock_client):
        code = volume.check_volume("proj", today=date(2026, 8, 11))
    assert code == 0


def test_check_volume_fails_on_sudden_drop():
    rows = [_row(date(2026, 8, d), 9) for d in range(1, 11)]
    rows.append(_row(date(2026, 8, 11), 2))  # today's load only got 2 of 9 currencies
    mock_client = MagicMock()
    mock_client.query.return_value.result.return_value = rows
    with patch.object(volume.bigquery, "Client", return_value=mock_client):
        code = volume.check_volume("proj", today=date(2026, 8, 11))
    assert code == 2


def test_check_volume_skips_with_insufficient_history():
    rows = [_row(date(2026, 8, 11), 9)]
    mock_client = MagicMock()
    mock_client.query.return_value.result.return_value = rows
    with patch.object(volume.bigquery, "Client", return_value=mock_client):
        code = volume.check_volume("proj", today=date(2026, 8, 11))
    assert code == 0


# --------------------------- reconciliation.py ---------------------------


def test_check_reconciliation_passes_within_tolerance():
    mock_bq_client = MagicMock()
    mock_bq_client.query.return_value.result.return_value = [
        MagicMock(base_currency="USD", quote_currency="EUR", latest_rate=0.9230),
        MagicMock(base_currency="USD", quote_currency="GBP", latest_rate=0.7890),
        MagicMock(base_currency="USD", quote_currency="JPY", latest_rate=147.20),
    ]

    def fake_fetch(base, quote):
        return {"EUR": 0.9235, "GBP": 0.7895, "JPY": 147.30}.get(quote)

    with (
        patch.object(reconciliation.bigquery, "Client", return_value=mock_bq_client),
        patch.object(reconciliation, "fetch_reference_rate", side_effect=fake_fetch),
    ):
        code = reconciliation.check_reconciliation("proj")
    assert code == 0


def test_check_reconciliation_fails_on_large_divergence():
    mock_bq_client = MagicMock()
    mock_bq_client.query.return_value.result.return_value = [
        MagicMock(base_currency="USD", quote_currency="EUR", latest_rate=0.9230),
        MagicMock(base_currency="USD", quote_currency="GBP", latest_rate=0.7890),
        MagicMock(base_currency="USD", quote_currency="JPY", latest_rate=147.20),
    ]

    def fake_fetch(base, quote):
        # EUR is wildly off — simulates a bad row in our primary source
        return {"EUR": 1.200, "GBP": 0.7895, "JPY": 147.30}.get(quote)

    with (
        patch.object(reconciliation.bigquery, "Client", return_value=mock_bq_client),
        patch.object(reconciliation, "fetch_reference_rate", side_effect=fake_fetch),
    ):
        code = reconciliation.check_reconciliation("proj")
    assert code == 2


def test_check_reconciliation_skips_pairs_with_no_reference_data():
    mock_bq_client = MagicMock()
    mock_bq_client.query.return_value.result.return_value = [
        MagicMock(base_currency="USD", quote_currency="EUR", latest_rate=0.9230),
    ]

    with (
        patch.object(reconciliation.bigquery, "Client", return_value=mock_bq_client),
        patch.object(reconciliation, "fetch_reference_rate", return_value=None),
    ):
        code = reconciliation.check_reconciliation("proj")
    assert code == 0  # nothing to compare against -> not a failure


# --------------------------- schema_drift.py ---------------------------


def _field(name, ftype, mode="REQUIRED"):
    return bigquery.SchemaField(name, ftype, mode=mode)


def test_check_schema_drift_passes_on_identical_schema():
    mock_table = MagicMock(schema=schema_drift.RAW_CURRENCY_RATES_SCHEMA)
    mock_client = MagicMock()
    mock_client.get_table.return_value = mock_table

    with patch.object(schema_drift.bigquery, "Client", return_value=mock_client):
        code = schema_drift.check_schema_drift("proj")
    assert code == 0


def test_check_schema_drift_fails_on_missing_column():
    live_schema = [f for f in schema_drift.RAW_CURRENCY_RATES_SCHEMA if f.name != "source"]
    mock_table = MagicMock(schema=live_schema)
    mock_client = MagicMock()
    mock_client.get_table.return_value = mock_table

    with patch.object(schema_drift.bigquery, "Client", return_value=mock_client):
        code = schema_drift.check_schema_drift("proj")
    assert code == 2


def test_check_schema_drift_fails_on_type_change():
    live_schema = [
        _field("rate_date", "DATE"),
        _field("base_currency", "STRING"),
        _field("quote_currency", "STRING"),
        _field("rate", "STRING"),  # was FLOAT64 — simulated upstream API change
        _field("source", "STRING"),
        _field("ingested_at", "TIMESTAMP"),
    ]
    mock_table = MagicMock(schema=live_schema)
    mock_client = MagicMock()
    mock_client.get_table.return_value = mock_table

    with patch.object(schema_drift.bigquery, "Client", return_value=mock_client):
        code = schema_drift.check_schema_drift("proj")
    assert code == 2


def test_check_schema_drift_allows_new_informational_column():
    live_schema = list(schema_drift.RAW_CURRENCY_RATES_SCHEMA) + [
        _field("new_upstream_field", "STRING", mode="NULLABLE")
    ]
    mock_table = MagicMock(schema=live_schema)
    mock_client = MagicMock()
    mock_client.get_table.return_value = mock_table

    with patch.object(schema_drift.bigquery, "Client", return_value=mock_client):
        code = schema_drift.check_schema_drift("proj")
    assert code == 0
