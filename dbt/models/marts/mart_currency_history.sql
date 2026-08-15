-- Mart: full time series per currency pair, for the D3 line/area charts.
-- Kept wide-but-simple on purpose so the dashboard's fetch layer is a
-- single flat query rather than joining marts at request time.

select
    rate_date        as as_of_date,
    base_currency,
    quote_currency,
    source_name,
    rate,
    pct_change_since_prior

from {{ ref('int_currency_rates_daily_change') }}
order by base_currency, quote_currency, rate_date
