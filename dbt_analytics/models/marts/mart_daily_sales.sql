{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key=['order_date', 'segment']) }}

select
    o.order_date,
    c.segment,
    count(*) filter (where o.status = 'completed') as completed_orders,
    sum(case when o.status = 'completed' then o.revenue else 0 end) as revenue,
    max(o.updated_at) as last_updated_at
from {{ ref('stg_orders') }} o
join {{ ref('stg_customers') }} c using (customer_id)
{% if is_incremental() %}
where o.updated_at >= current_timestamp - interval '3 days'
{% endif %}
group by 1, 2
