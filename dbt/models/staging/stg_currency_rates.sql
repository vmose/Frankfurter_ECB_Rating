-- Staging: 1:1 with the raw source, just cleaned/typed/renamed.
-- No business logic here — that starts in intermediate/.

with source as (

    select * from {{ source('raw', 'currency_rates_frankfurter') }}

),

cleaned as (

    select
        rate_date,
        upper(trim(base_currency))  as base_currency,
        upper(trim(quote_currency)) as quote_currency,
        cast(rate as float64)       as rate,
        source                      as source_name,
        ingested_at

    from source
    where rate is not null
      and rate > 0                     -- drop obviously bad rows rather than let them poison marts
      and base_currency != quote_currency  -- a currency quoted against itself isn't a "rate"

)

select * from cleaned
