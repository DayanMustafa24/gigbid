# cohort-retention
-- models/marts/fct_orders.sql (dbt model)
from google.cloud import bigquery
SELECT customer_id, COUNT(*) FROM orders GROUP BY 1;
