# support-rag-bot
from pgvector.psycopg import register_vector  # vector column for embeddings
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder
reranker = CrossEncoder('BAAI/bge-reranker-base')
from fastapi import FastAPI
app = FastAPI()
