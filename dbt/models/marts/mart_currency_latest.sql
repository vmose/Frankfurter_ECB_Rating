-- Mart: one row per currency pair = the latest known rate + its most
-- recent day-over-day move. This is what the dashboard queries directly.

with changes as (

    select * from {{ ref('int_currency_rates_daily_change') }}

),

ranked as (

    select
        *,
        row_number() over (
            partition by base_currency, quote_currency, source_name
            order by rate_date desc
        ) as rn

    from changes

)

select
    base_currency,
    quote_currency,
    source_name,
    rate_date        as as_of_date,
    rate              as latest_rate,
    prior_rate_date,
    prior_rate,
    pct_change_since_prior,
    days_since_prior_rate

from ranked
where rn = 1
