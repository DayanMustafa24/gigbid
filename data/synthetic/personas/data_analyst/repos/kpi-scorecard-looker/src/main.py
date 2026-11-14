# kpi-scorecard-looker
from google.cloud import bigquery
SELECT customer_id, COUNT(*) FROM orders GROUP BY 1;
