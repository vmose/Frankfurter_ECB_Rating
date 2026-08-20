-- Staging: 1:1 with the raw source, just cleaned/typed/renamed.
-- No business logic here — that starts in intermediate/.
--
-- NOTE: this CTE is deliberately named `raw_rows`, not `source` — even
-- though it reads via the source() macro. Naming a CTE `source` while
-- it also contains a column literally named `source` is ambiguous in
-- BigQuery: a bare `source` reference can resolve to the ENTIRE ROW as
-- a STRUCT (matching the relation's own name) instead of the column.
-- That's exactly what broke this pipeline: `source as source_name`
-- silently became a struct containing every column (including the
-- FLOAT64 `rate` column), which then made `partition by ... source_name`
-- fail downstream in int_currency_rates_daily_change.sql with
-- "Partitioning by expressions of type STRUCT containing FLOAT64 is
-- not allowed". Keep CTE names distinct from column names they contain.

with raw_rows as (

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

    from raw_rows
    where rate is not null
      and rate > 0                     -- drop obviously bad rows rather than let them poison marts
      and base_currency != quote_currency  -- a currency quoted against itself isn't a "rate"

)

select * from cleaned