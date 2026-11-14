# text-to-sql-agent
from langgraph.graph import StateGraph
import psycopg
SELECT customer_id, COUNT(*) FROM orders GROUP BY 1;
