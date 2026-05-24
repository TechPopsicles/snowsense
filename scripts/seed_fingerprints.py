"""
One-time script: seed canonical TPCH warehouse-sizing examples into pgvector
table 'query_fingerprint_embeddings'.

These cover the six scale-factor patterns from BRIEF.md §"Warehouse sizing expectations"
and give the agent correct warehouse recommendations before any real Snowflake query
history accumulates (i.e. before build_fingerprints.py can be run against
snowflake.account_usage.query_history).

Existing fingerprints with the same hash are upserted (not duplicated), so this
script is safe to re-run after build_fingerprints.py has added real history.

Usage:
    python scripts/seed_fingerprints.py

Requires:
    - Postgres + pgvector running (docker compose up postgres, or locally on port 5432)
    - PG_HOST / PG_PORT / PG_DB / PG_USER / PG_PASSWORD env vars
"""

import hashlib
import os
import re

import psycopg2
from langchain_huggingface import HuggingFaceEmbeddings
from pgvector.psycopg2 import register_vector

PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
PG_DB = os.environ.get("PG_DB", "snowsense")
PG_USER = os.environ.get("PG_USER", "snowsense")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "snowsense")


def connect() -> psycopg2.extensions.connection:
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB,
        user=PG_USER, password=PG_PASSWORD,
    )
    register_vector(conn)
    return conn


def normalise_sql(sql: str) -> str:
    sql = re.sub(r"'[^']*'", "?", sql)
    sql = re.sub(r"\b\d+(\.\d+)?\b", "?", sql)
    sql = re.sub(r"\s+", " ", sql).strip().upper()
    return sql


def fingerprint(sql: str) -> str:
    return hashlib.md5(normalise_sql(sql).encode()).hexdigest()


SEEDS = [
    {
        "label": "Daily order count — ORDERS SF1",
        "sql": (
            "SELECT order_date, COUNT(*) AS order_count "
            "FROM analytics.marts.mart_revenue "
            "GROUP BY order_date ORDER BY order_date"
        ),
        "best_warehouse": "COMPUTE_XS",
        "median_credits": 0.0001,
        "p95_execution_seconds": 2.0,
        "avg_bytes_scanned": 50_000_000,
        "execution_count": 50,
        "confidence": "high",
    },
    {
        "label": "Full customer scan + nation join — CUSTOMER+NATION SF1",
        "sql": (
            "SELECT customer_id, customer_name, nation_name, region_name, account_balance "
            "FROM analytics.marts.mart_customer_value "
            "ORDER BY account_balance DESC LIMIT 1000"
        ),
        "best_warehouse": "COMPUTE_XS",
        "median_credits": 0.0002,
        "p95_execution_seconds": 3.0,
        "avg_bytes_scanned": 80_000_000,
        "execution_count": 30,
        "confidence": "high",
    },
    {
        "label": "30-day revenue by market segment — ORDERS+LINEITEM SF1",
        "sql": (
            "SELECT market_segment, SUM(net_revenue) AS total_revenue "
            "FROM analytics.marts.mart_customer_value "
            "WHERE last_order_date >= DATEADD(day, -30, CURRENT_DATE()) "
            "GROUP BY market_segment ORDER BY total_revenue DESC"
        ),
        "best_warehouse": "COMPUTE_M",
        "median_credits": 0.005,
        "p95_execution_seconds": 12.0,
        "avg_bytes_scanned": 800_000_000,
        "execution_count": 25,
        "confidence": "high",
    },
    {
        "label": "Full lineitem scan all time — LINEITEM SF1",
        "sql": (
            "SELECT SUM(net_revenue) AS total_revenue, COUNT(*) AS line_item_count, "
            "AVG(discount) AS avg_discount FROM analytics.marts.mart_revenue"
        ),
        "best_warehouse": "COMPUTE_M",
        "median_credits": 0.01,
        "p95_execution_seconds": 20.0,
        "avg_bytes_scanned": 2_000_000_000,
        "execution_count": 20,
        "confidence": "high",
    },
    {
        "label": "Full lineitem scan — LINEITEM SF10",
        "sql": (
            "SELECT l_orderkey, l_extendedprice, l_discount, l_tax, l_shipdate "
            "FROM snowflake_sample_data.tpch_sf10.lineitem "
            "WHERE l_shipdate >= '1995-01-01'"
        ),
        "best_warehouse": "COMPUTE_L",
        "median_credits": 0.05,
        "p95_execution_seconds": 35.0,
        "avg_bytes_scanned": 10_000_000_000,
        "execution_count": 15,
        "confidence": "high",
    },
    {
        "label": "Full lineitem scan + all joins — LINEITEM+all SF100",
        "sql": (
            "SELECT o.o_orderkey, c.c_name, c.c_mktsegment, s.s_name, p.p_name, "
            "l.l_extendedprice * (1 - l.l_discount) * (1 + l.l_tax) AS net_revenue "
            "FROM snowflake_sample_data.tpch_sf100.lineitem l "
            "JOIN snowflake_sample_data.tpch_sf100.orders o ON l.l_orderkey = o.o_orderkey "
            "JOIN snowflake_sample_data.tpch_sf100.customer c ON o.o_custkey = c.c_custkey "
            "JOIN snowflake_sample_data.tpch_sf100.supplier s ON l.l_suppkey = s.s_suppkey "
            "JOIN snowflake_sample_data.tpch_sf100.part p ON l.l_partkey = p.p_partkey"
        ),
        "best_warehouse": "COMPUTE_XL",
        "median_credits": 0.5,
        "p95_execution_seconds": 120.0,
        "avg_bytes_scanned": 100_000_000_000,
        "execution_count": 5,
        "confidence": "low",
    },
]


def main():
    print(f"Connecting to Postgres at {PG_HOST}:{PG_PORT}/{PG_DB} …")
    conn = connect()

    print("Loading embedding model all-MiniLM-L6-v2 …")
    embedder = HuggingFaceEmbeddings(
        model_name="all-MiniLM-L6-v2",
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    with conn.cursor() as cur:
        for seed in SEEDS:
            norm = normalise_sql(seed["sql"])
            fp = fingerprint(seed["sql"])
            doc = (
                f"Query pattern: {norm[:500]}\n"
                f"Best warehouse: {seed['best_warehouse']}\n"
                f"Median credits: {seed['median_credits']}\n"
                f"P95 execution time: {seed['p95_execution_seconds']}s\n"
                f"Confidence: {seed['confidence']} ({seed['execution_count']} executions)\n"
                f"Scale context: {seed['label']}"
            )
            embedding = embedder.embed_documents([doc])[0]
            cur.execute(
                """
                INSERT INTO query_fingerprint_embeddings
                    (fingerprint_hash, sample_sql, best_warehouse,
                     median_credits, p95_exec_seconds, avg_bytes_scanned,
                     execution_count, confidence, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                ON CONFLICT (fingerprint_hash) DO UPDATE
                    SET sample_sql        = EXCLUDED.sample_sql,
                        best_warehouse    = EXCLUDED.best_warehouse,
                        median_credits    = EXCLUDED.median_credits,
                        p95_exec_seconds  = EXCLUDED.p95_exec_seconds,
                        avg_bytes_scanned = EXCLUDED.avg_bytes_scanned,
                        execution_count   = EXCLUDED.execution_count,
                        confidence        = EXCLUDED.confidence,
                        embedding         = EXCLUDED.embedding
                """,
                (
                    fp,
                    norm[:500],
                    seed["best_warehouse"],
                    seed["median_credits"],
                    seed["p95_execution_seconds"],
                    seed["avg_bytes_scanned"],
                    seed["execution_count"],
                    seed["confidence"],
                    embedding,
                ),
            )
            print(f"  Upserted: {seed['label']} → {seed['best_warehouse']}")

    conn.commit()
    conn.close()
    print(f"\nDone. {len(SEEDS)} seed fingerprints upserted into 'query_fingerprint_embeddings'.")


if __name__ == "__main__":
    main()
