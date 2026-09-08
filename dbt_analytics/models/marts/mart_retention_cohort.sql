{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key=['cohort_month', 'activity_month', 'segment']) }}

with activity as (
    select
        date_trunc('month', c.signup_date)::date as cohort_month,
        date_trunc('month', o.order_date)::date as activity_month,
        c.segment,
        o.customer_id,
        o.updated_at
    from {{ ref('stg_orders') }} o
    join {{ ref('stg_customers') }} c using (customer_id)
    where o.status = 'completed'
),
affected_cohorts as (
    select distinct cohort_month
    from activity
    where updated_at >= current_timestamp - interval '90 days'
)
select
    cohort_month,
    activity_month,
    segment,
    count(distinct customer_id) as active_customers,
    max(updated_at) as last_updated_at
from activity
{% if is_incremental() %}
where cohort_month in (select cohort_month from affected_cohorts)
{% endif %}
group by 1, 2, 3
