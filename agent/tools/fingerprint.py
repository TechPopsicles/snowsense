import hashlib
import json
import logging
import re

from langchain_core.tools import tool

from db import get_pg_connection
from tools.embedding import get_embedder

logger = logging.getLogger(__name__)


def normalise_sql(sql: str) -> str:
    sql = re.sub(r"'[^']*'", "?", sql)
    sql = re.sub(r"\b\d+(\.\d+)?\b", "?", sql)
    sql = re.sub(r"\s+", " ", sql).strip().upper()
    return sql


def fingerprint(sql: str) -> str:
    return hashlib.md5(normalise_sql(sql).encode()).hexdigest()


@tool
async def search_fingerprints(query_sql: str) -> str:
    """Find historical execution profiles for queries similar to the current one.
    Returns best warehouse, median credits, p95 execution time, and confidence
    level based on real query history. Use this BEFORE choosing a warehouse for any query."""
    normalised = normalise_sql(query_sql)
    embedding = get_embedder().embed_query(normalised)

    try:
        conn = get_pg_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT best_warehouse, median_credits, p95_exec_seconds,
                           confidence, execution_count,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM query_fingerprint_embeddings
                    WHERE 1 - (embedding <=> %s::vector) > 0.75
                    ORDER BY embedding <=> %s::vector
                    LIMIT 3
                    """,
                    (embedding, embedding, embedding),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Fingerprint search failed: %s", exc)
        return json.dumps({
            "match_found": False,
            "recommendation": "No historical match — classify by SQL complexity.",
            "error": str(exc),
        })

    if not rows:
        return json.dumps({
            "match_found": False,
            "recommendation": "No historical match — classify by SQL complexity.",
        })

    hits = [
        {
            "best_warehouse": row[0],
            "median_credits": float(row[1]) if row[1] is not None else 0.0,
            "p95_execution_seconds": float(row[2]) if row[2] is not None else 0.0,
            "confidence": row[3],
            "execution_count": row[4],
            "similarity": round(float(row[5]), 4),
        }
        for row in rows
    ]

    return json.dumps({
        "match_found": True,
        "top_match": hits[0],
        "all_matches": hits,
    })
