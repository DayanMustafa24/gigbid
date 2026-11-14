# dbt-warehouse
# models are versioned and reviewed through pull requests
-- star schema: dim_customer, dim_date, fct_sales
-- models/marts/fct_orders.sql (dbt model)
SELECT customer_id, COUNT(*) FROM orders GROUP BY 1;
