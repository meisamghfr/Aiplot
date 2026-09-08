select customer_id, signup_date, segment, country
from {{ source('warehouse', 'customers') }}
