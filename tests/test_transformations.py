"""
Tests for the transformation logic in dbt/models/intermediate and
dbt/models/marts.

IMPORTANT — what these tests actually cover:
dbt SQL models run against a real BigQuery warehouse, so they can't be
unit tested directly without one (that's what `dbt build` in
.github/workflows/dbt.yml does, plus the schema/singular tests under
dbt/tests/ and dbt/models/**/*.yml).

What we CAN do without a warehouse is pin down the *business logic* in
plain Python/pandas and assert it matches what the SQL is supposed to
do — day-over-day change per currency pair, and "latest row per pair"
selection. If someone changes int_currency_rates_daily_change.sql in a
way that changes this logic, these tests won't catch the SQL edit
directly, but they document the intended behavior precisely enough
that a reviewer can check the SQL against it.
"""

import pandas as pd
import pytest


def compute_daily_change(df: pd.DataFrame) -> pd.DataFrame:
    """
    Python mirror of dbt/models/intermediate/int_currency_rates_daily_change.sql:
    for each (base_currency, quote_currency, source_name), compute the
    prior rate/date via lag-by-rate_date, then pct change since prior.
    """
    df = df.sort_values(["base_currency", "quote_currency", "source_name", "rate_date"]).copy()
    group_cols = ["base_currency", "quote_currency", "source_name"]

    df["prior_rate"] = df.groupby(group_cols)["rate"].shift(1)
    df["prior_rate_date"] = df.groupby(group_cols)["rate_date"].shift(1)
    df["days_since_prior_rate"] = (
        pd.to_datetime(df["rate_date"]) - pd.to_datetime(df["prior_rate_date"])
    ).dt.days
    df["pct_change_since_prior"] = (df["rate"] - df["prior_rate"]) / df["prior_rate"]

    return df


def latest_per_pair(df: pd.DataFrame) -> pd.DataFrame:
    """Python mirror of dbt/models/marts/mart_currency_latest.sql's row_number()=1 filter."""
    group_cols = ["base_currency", "quote_currency", "source_name"]
    idx = df.groupby(group_cols)["rate_date"].idxmax()
    return df.loc[idx].reset_index(drop=True)


@pytest.fixture
def sample_rates():
    return pd.DataFrame(
        [
            {
                "rate_date": "2026-08-05",
                "base_currency": "USD",
                "quote_currency": "EUR",
                "source_name": "frankfurter_ecb",
                "rate": 0.900,
            },
            {
                "rate_date": "2026-08-06",
                "base_currency": "USD",
                "quote_currency": "EUR",
                "source_name": "frankfurter_ecb",
                "rate": 0.910,
            },
            # Friday -> Monday gap (weekend), still should compute a valid pct change
            {
                "rate_date": "2026-08-07",
                "base_currency": "USD",
                "quote_currency": "EUR",
                "source_name": "frankfurter_ecb",
                "rate": 0.918,
            },
            {
                "rate_date": "2026-08-10",
                "base_currency": "USD",
                "quote_currency": "EUR",
                "source_name": "frankfurter_ecb",
                "rate": 0.920,
            },
            # A second, unrelated pair — must not leak into USD/EUR's lag chain
            {
                "rate_date": "2026-08-06",
                "base_currency": "USD",
                "quote_currency": "GBP",
                "source_name": "frankfurter_ecb",
                "rate": 0.780,
            },
            {
                "rate_date": "2026-08-07",
                "base_currency": "USD",
                "quote_currency": "GBP",
                "source_name": "frankfurter_ecb",
                "rate": 0.785,
            },
        ]
    )


def test_first_observation_per_pair_has_no_prior(sample_rates):
    result = compute_daily_change(sample_rates)
    first_eur_row = result[
        (result["base_currency"] == "USD")
        & (result["quote_currency"] == "EUR")
        & (result["rate_date"] == "2026-08-05")
    ].iloc[0]

    assert pd.isna(first_eur_row["prior_rate"])
    assert pd.isna(first_eur_row["pct_change_since_prior"])


def test_pct_change_computed_correctly(sample_rates):
    result = compute_daily_change(sample_rates)
    row = result[
        (result["base_currency"] == "USD")
        & (result["quote_currency"] == "EUR")
        & (result["rate_date"] == "2026-08-06")
    ].iloc[0]

    expected_pct = (0.910 - 0.900) / 0.900
    assert row["prior_rate"] == pytest.approx(0.900)
    assert row["pct_change_since_prior"] == pytest.approx(expected_pct)


def test_weekend_gap_uses_last_available_rate_not_calendar_day(sample_rates):
    """
    2026-08-07 -> 2026-08-10 is a 3 calendar-day gap (Fri -> Mon), but
    it's still the correct "prior" row: Frankfurter has no Sat/Sun
    rows to begin with, so the prior row IS the last real observation.
    """
    result = compute_daily_change(sample_rates)
    monday_row = result[
        (result["base_currency"] == "USD")
        & (result["quote_currency"] == "EUR")
        & (result["rate_date"] == "2026-08-10")
    ].iloc[0]

    assert monday_row["prior_rate"] == pytest.approx(0.918)
    assert monday_row["days_since_prior_rate"] == 3


def test_pairs_do_not_leak_into_each_others_lag_chain(sample_rates):
    result = compute_daily_change(sample_rates)
    first_gbp_row = result[
        (result["base_currency"] == "USD")
        & (result["quote_currency"] == "GBP")
        & (result["rate_date"] == "2026-08-06")
    ].iloc[0]

    # This is GBP's first observation — it must not pick up EUR's prior rate.
    assert pd.isna(first_gbp_row["prior_rate"])


def test_latest_per_pair_returns_one_row_each(sample_rates):
    changes = compute_daily_change(sample_rates)
    latest = latest_per_pair(changes)

    assert len(latest) == 2  # one row per (base, quote, source) triple

    eur_row = latest[latest["quote_currency"] == "EUR"].iloc[0]
    gbp_row = latest[latest["quote_currency"] == "GBP"].iloc[0]

    assert eur_row["rate_date"] == "2026-08-10"
    assert gbp_row["rate_date"] == "2026-08-07"


def test_extreme_jump_would_be_caught_by_dbt_singular_test(sample_rates):
    """
    Mirrors the threshold in dbt/tests/assert_no_extreme_rate_jumps.sql
    (fails the build on any |pct_change| > 20%). This test just confirms
    our sample fixture doesn't accidentally trip that threshold, and
    that a genuinely bad row would.
    """
    result = compute_daily_change(sample_rates)
    assert (result["pct_change_since_prior"].dropna().abs() <= 0.20).all()

    bad_row = pd.DataFrame(
        [
            {
                "rate_date": "2026-08-05",
                "base_currency": "USD",
                "quote_currency": "CHF",
                "source_name": "frankfurter_ecb",
                "rate": 1.0,
            },
            {
                "rate_date": "2026-08-06",
                "base_currency": "USD",
                "quote_currency": "CHF",
                "source_name": "frankfurter_ecb",
                "rate": 1.5,
            },  # +50%
        ]
    )
    bad_result = compute_daily_change(bad_row)
    assert (bad_result["pct_change_since_prior"].dropna().abs() > 0.20).any()
