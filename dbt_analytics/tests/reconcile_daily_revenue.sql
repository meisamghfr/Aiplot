with source_total as (
    select sum(revenue) as revenue
    from {{ source('warehouse', 'orders') }}
    where status = 'completed'
), mart_total as (
    select sum(revenue) as revenue from {{ ref('mart_daily_sales') }}
)
select source_total.revenue as source_revenue, mart_total.revenue as mart_revenue
from source_total cross join mart_total
where abs(source_total.revenue - mart_total.revenue) > 0.01
