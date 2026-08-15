-- Intermediate: adds day-over-day movement per currency pair.
-- This is where "business logic" starts — staging stays dumb on purpose.

with rates as (

    select * from {{ ref('stg_currency_rates') }}

),

with_lag as (

    select
        rate_date,
        base_currency,
        quote_currency,
        rate,
        source_name,
        lag(rate) over (
            partition by base_currency, quote_currency, source_name
            order by rate_date
        ) as prior_rate,
        lag(rate_date) over (
            partition by base_currency, quote_currency, source_name
            order by rate_date
        ) as prior_rate_date

    from rates

)

select
    rate_date,
    base_currency,
    quote_currency,
    rate,
    source_name,
    prior_rate,
    prior_rate_date,
    date_diff(rate_date, prior_rate_date, day) as days_since_prior_rate,
    safe_divide(rate - prior_rate, prior_rate) as pct_change_since_prior

from with_lag
