create schema if not exists raw;
create schema if not exists analytics;

create table if not exists raw.customers (
    customer_id bigint primary key,
    signup_date date not null,
    segment text not null check (segment in ('SMB', 'Mid-market', 'Enterprise')),
    country text not null
);

create table if not exists raw.orders (
    order_id bigint primary key,
    customer_id bigint not null references raw.customers(customer_id),
    order_date date not null,
    revenue numeric(12, 2) not null check (revenue >= 0),
    status text not null check (status in ('completed', 'refunded')),
    updated_at timestamptz not null
);

truncate table raw.orders, raw.customers restart identity cascade;

insert into raw.customers (customer_id, signup_date, segment, country)
select
    customer_id,
    date '2025-01-01' + ((customer_id * 11) % 300)::integer,
    (array['SMB', 'Mid-market', 'Enterprise'])[1 + (customer_id % 3)],
    (array['Germany', 'France', 'Netherlands', 'Spain'])[1 + (customer_id % 4)]
from generate_series(1, 120) as customer_id;

insert into raw.orders (order_id, customer_id, order_date, revenue, status, updated_at)
select
    order_id,
    1 + (order_id * 17 % 120),
    date '2025-02-01' + ((order_id * 7) % 550)::integer,
    round((25 + (order_id * 13 % 900))::numeric, 2),
    case when order_id % 23 = 0 then 'refunded' else 'completed' end,
    (date '2025-02-01' + ((order_id * 7) % 550)::integer)::timestamptz
from generate_series(1, 1500) as order_id;

create index if not exists orders_updated_at_idx on raw.orders(updated_at);
create index if not exists orders_customer_date_idx on raw.orders(customer_id, order_date);

create or replace view public.customers as select * from raw.customers;
create or replace view public.orders as select * from raw.orders;

comment on schema raw is 'Source-like demo warehouse data owned by the Aiplot bootstrap.';
comment on schema analytics is 'Stable dbt-built analytics marts consumed by downstream reporting.';
comment on view public.customers is 'Read-only Text-to-SQL surface for warehouse customers.';
comment on view public.orders is 'Read-only Text-to-SQL surface for warehouse orders.';
