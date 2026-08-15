-- Singular test: fails if any currency pair moves more than 20% in a
-- single day. A real ECB rate essentially never does this for the
-- major/liquid currencies in our default set — a jump this size is far
-- more likely to be a data quality problem (bad row, unit error,
-- currency mix-up) than a real market move. dbt test fails the build
-- if this query returns any rows.

select
    rate_date,
    base_currency,
    quote_currency,
    prior_rate,
    rate,
    pct_change_since_prior

from {{ ref('int_currency_rates_daily_change') }}
where abs(pct_change_since_prior) > 0.20
