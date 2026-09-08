select order_id, customer_id, order_date, revenue, status, updated_at
from {{ source('warehouse', 'orders') }}
