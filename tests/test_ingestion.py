"""
Unit tests for ingestion/extract.py and ingestion/load.py.

These tests never touch the network or BigQuery — requests.Session.get
and the BigQuery client are mocked throughout, so this suite runs the
same in CI (no credentials) as it does locally.

extract.py was rewritten against the real Frankfurter v2 API shape
(confirmed against api.frankfurter.dev's docs and a live response):
one GET /v2/rates endpoint, returning a flat array of
{date, base, quote, rate} rows rather than a single object with a
nested `rates` dict. These tests reflect that shape.
"""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingestion"))

import extract  # noqa: E402
import load  # noqa: E402

SAMPLE_PAYLOAD = [
    {"date": "2026-08-10", "base": "USD", "quote": "EUR", "rate": 0.923},
    {"date": "2026-08-10", "base": "USD", "quote": "GBP", "rate": 0.789},
    {"date": "2026-08-10", "base": "USD", "quote": "JPY", "rate": 147.2},
]


# --------------------------- payload_to_frame ---------------------------


def test_payload_to_frame_shape_and_types():
    df = extract.payload_to_frame(SAMPLE_PAYLOAD)

    assert set(df.columns) == {
        "rate_date",
        "base_currency",
        "quote_currency",
        "rate",
        "source",
        "ingested_at",
    }
    assert len(df) == 3
    assert (df["rate_date"] == date(2026, 8, 10)).all()
    assert df["base_currency"].eq("USD").all()
    assert set(df["quote_currency"]) == {"EUR", "GBP", "JPY"}
    assert df["rate"].dtype == "float64"
    assert df["source"].eq(extract.SOURCE_NAME).all()


def test_payload_to_frame_preserves_per_row_dates():
    """
    The real API can return different dates for different currencies in
    the SAME response (per-currency publish lag) — even on a plain
    "latest" call. payload_to_frame must NOT collapse this to one date.
    """
    mixed_payload = [
        {"date": "2026-08-12", "base": "EUR", "quote": "AED", "rate": 4.24},
        {"date": "2026-08-11", "base": "EUR", "quote": "ALL", "rate": 92.94},  # lagging
    ]
    df = extract.payload_to_frame(mixed_payload)

    aed_row = df[df["quote_currency"] == "AED"].iloc[0]
    all_row = df[df["quote_currency"] == "ALL"].iloc[0]
    assert aed_row["rate_date"] == date(2026, 8, 12)
    assert all_row["rate_date"] == date(2026, 8, 11)


# --------------------------- fetch_rates ---------------------------


def test_fetch_rates_includes_providers_by_default():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = SAMPLE_PAYLOAD
    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    extract.fetch_rates("USD", ["EUR", "GBP", "JPY"], session=mock_session)

    _, kwargs = mock_session.get.call_args
    assert kwargs["params"]["providers"] == "ECB"
    assert kwargs["params"]["base"] == "USD"
    assert kwargs["params"]["quotes"] == "EUR,GBP,JPY"


def test_fetch_rates_omits_providers_when_none():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = SAMPLE_PAYLOAD
    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    extract.fetch_rates("USD", ["EUR"], providers=None, session=mock_session)

    _, kwargs = mock_session.get.call_args
    assert "providers" not in kwargs["params"]


def test_fetch_rates_returns_payload_on_success():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = SAMPLE_PAYLOAD
    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    result = extract.fetch_rates("USD", ["EUR", "GBP", "JPY"], session=mock_session)
    assert result == SAMPLE_PAYLOAD


def test_fetch_rates_raises_with_api_error_message():
    """Docs specify error bodies as {"message": "..."} on 400/404/422."""
    mock_resp = MagicMock(status_code=404, url="https://api.frankfurter.dev/v2/rates")
    mock_resp.json.return_value = {"message": "Could not find currency ABC"}
    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    with pytest.raises(extract.FrankfurterError, match="Could not find currency ABC"):
        extract.fetch_rates("USD", ["ABC"], session=mock_session)


def test_fetch_rates_raises_on_unexpected_object_response():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"unexpected": "shape"}
    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    with pytest.raises(extract.FrankfurterError, match="unexpected object"):
        extract.fetch_rates("USD", ["EUR"], session=mock_session)


def test_fetch_rates_raises_on_empty_list():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = []
    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    with pytest.raises(extract.FrankfurterError, match="empty or malformed"):
        extract.fetch_rates("USD", ["EUR"], session=mock_session)


def test_fetch_rates_raises_on_row_missing_field():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = [{"date": "2026-08-10", "base": "USD"}]  # no quote/rate
    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    with pytest.raises(extract.FrankfurterError, match="missing"):
        extract.fetch_rates("USD", ["EUR"], session=mock_session)


# --------------------------- _get_with_retry ---------------------------


def test_get_with_retry_succeeds_after_transient_429(monkeypatch):
    monkeypatch.setattr(extract.time, "sleep", lambda _: None)

    ok_resp = MagicMock(status_code=200)
    bad_resp = MagicMock(status_code=429, text="rate limited")
    mock_session = MagicMock()
    mock_session.get.side_effect = [bad_resp, bad_resp, ok_resp]

    resp = extract._get_with_retry(mock_session, "https://example.test", {})

    assert resp is ok_resp
    assert mock_session.get.call_count == 3


def test_get_with_retry_raises_after_exhausting_attempts(monkeypatch):
    monkeypatch.setattr(extract.time, "sleep", lambda _: None)

    bad_resp = MagicMock(status_code=500, text="server error")
    mock_session = MagicMock()
    mock_session.get.return_value = bad_resp

    with pytest.raises(extract.FrankfurterError):
        extract._get_with_retry(mock_session, "https://example.test", {})

    assert mock_session.get.call_count == extract.MAX_RETRIES


# --------------------------- write_partition / write_by_date ---------------------------


def test_write_partition_creates_expected_path(tmp_path):
    df = extract.payload_to_frame(SAMPLE_PAYLOAD)
    out_root = tmp_path / "raw"

    out_path = extract.write_partition(df, date(2026, 8, 10), out_root=out_root)

    assert out_path == out_root / "dt=2026-08-10" / "rates.parquet"
    assert out_path.exists()
    roundtrip = pd.read_parquet(out_path)
    assert len(roundtrip) == 3


def test_write_by_date_splits_multiple_dates(tmp_path):
    mixed_payload = [
        {"date": "2026-08-12", "base": "EUR", "quote": "AED", "rate": 4.24},
        {"date": "2026-08-12", "base": "EUR", "quote": "AFN", "rate": 75.39},
        {"date": "2026-08-11", "base": "EUR", "quote": "ALL", "rate": 92.94},  # lagging
    ]
    df = extract.payload_to_frame(mixed_payload)
    out_root = tmp_path / "raw"

    written = extract.write_by_date(df, out_root=out_root)

    assert len(written) == 2
    assert out_root / "dt=2026-08-12" / "rates.parquet" in written
    assert out_root / "dt=2026-08-11" / "rates.parquet" in written

    aug12 = pd.read_parquet(out_root / "dt=2026-08-12" / "rates.parquet")
    aug11 = pd.read_parquet(out_root / "dt=2026-08-11" / "rates.parquet")
    assert len(aug12) == 2
    assert len(aug11) == 1


# --------------------------- run_latest ---------------------------


def test_run_latest_writes_one_partition_per_date_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mixed_payload = [
        {"date": "2026-08-12", "base": "USD", "quote": "EUR", "rate": 0.92},
        {"date": "2026-08-11", "base": "USD", "quote": "GBP", "rate": 0.78},  # lagging
    ]

    with patch.object(extract, "fetch_rates", return_value=mixed_payload) as mock_fetch:
        written = extract.run_latest(base="USD", quotes=["EUR", "GBP"])

    assert len(written) == 2
    mock_fetch.assert_called_once_with(
        base="USD", quotes=["EUR", "GBP"], providers=extract.DEFAULT_PROVIDERS
    )


# --------------------------- _chunk_date_range ---------------------------


def test_chunk_date_range_splits_by_max_days():
    chunks = list(extract._chunk_date_range(date(2020, 1, 1), date(2021, 6, 15), max_days=366))

    assert chunks == [
        (date(2020, 1, 1), date(2020, 12, 31)),  # full leap year
        (date(2021, 1, 1), date(2021, 6, 15)),
    ]


def test_chunk_date_range_single_chunk_when_within_max_days():
    chunks = list(extract._chunk_date_range(date(2026, 1, 1), date(2026, 1, 10), max_days=366))
    assert chunks == [(date(2026, 1, 1), date(2026, 1, 10))]


# --------------------------- run_backfill ---------------------------


def test_run_backfill_uses_time_series_chunks_and_skips_failed_chunk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def fake_fetch_rates(base, quotes, providers=None, date_from=None, date_to=None, session=None):
        if date_from == "2020-01-01":
            raise extract.FrankfurterError("simulated upstream failure")
        return [{"date": date_from, "base": base, "quote": quotes[0], "rate": 1.0}]

    with patch.object(extract, "fetch_rates", side_effect=fake_fetch_rates):
        written = extract.run_backfill(
            base="USD", quotes=["EUR"], start=date(2020, 1, 1), end=date(2021, 6, 15)
        )

    # 2020 chunk fails and is skipped; 2021 chunk succeeds -> 1 partition written
    assert len(written) == 1


# --------------------------- load.py ---------------------------


def test_discover_partitions_sorts_and_ignores_junk(tmp_path):
    root = tmp_path / "raw"
    (root / "dt=2026-08-10").mkdir(parents=True)
    (root / "dt=2026-08-08").mkdir(parents=True)
    (root / "not-a-partition").mkdir(parents=True)

    dates = load.discover_partitions(root=root)

    assert dates == [date(2026, 8, 8), date(2026, 8, 10)]


def test_load_partition_skips_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(load, "RAW_ROOT", tmp_path / "raw")
    mock_client = MagicMock()

    rows = load.load_partition(mock_client, "my-project", date(2026, 8, 10))

    assert rows == 0
    mock_client.load_table_from_file.assert_not_called()


def test_daterange_inclusive():
    days = load.daterange(date(2026, 8, 10), date(2026, 8, 12))
    assert days == [date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)]
