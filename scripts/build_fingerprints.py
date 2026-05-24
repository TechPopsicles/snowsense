"""
One-time script: fingerprint Snowflake query_history and embed into pgvector
table 'query_fingerprint_embeddings'.

Usage:
    python scripts/build_fingerprints.py

Requires:
    - .env loaded (set -a && source .env && set +a)
    - Postgres + pgvector running (docker compose up postgres, or locally on port 5432)
    - PG_HOST / PG_PORT / PG_DB / PG_USER / PG_PASSWORD env vars
    - Snowflake role with access to snowflake.account_usage.query_history
      (typically ACCOUNTADMIN or a role granted MONITOR privilege on the account)
"""

import hashlib
import os
import re
import sys
from collections import defaultdict
from statistics import median

import psycopg2
import snowflake.connector
from langchain_huggingface import HuggingFaceEmbeddings
from pgvector.psycopg2 import register_vector

PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
PG_DB = os.environ.get("PG_DB", "snowsense")
PG_USER = os.environ.get("PG_USER", "snowsense")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "snowsense")
LOOKBACK_DAYS = 90
MIN_EXECUTIONS_HIGH_CONFIDENCE = 10


def connect_pg() -> psycopg2.extensions.connection:
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


def connect_snowflake() -> snowflake.connector.SnowflakeConnection:
    kwargs: dict = {
        "account": os.environ["SF_ACCOUNT"],
        "user": os.environ["SF_USER"],
        "role": os.environ.get("SF_ROLE", "ACCOUNTADMIN"),
        "warehouse": os.environ.get("SF_WAREHOUSE", "COMPUTE_XS"),
    }
    private_key_path = os.environ.get("PRIVATE_KEY_PATH")
    if private_key_path:
        from cryptography.hazmat.primitives.serialization import (
            Encoding, NoEncryption, PrivateFormat, load_pem_private_key,
        )
        with open(private_key_path, "rb") as f:
            pk = load_pem_private_key(f.read(), password=None)
        kwargs["private_key"] = pk.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
    else:
        kwargs["password"] = os.environ["SF_PASSWORD"]
    return snowflake.connector.connect(**kwargs)


def fetch_query_history(conn: snowflake.connector.SnowflakeConnection) -> list[dict]:
    sql = f"""
        SELECT
            query_text,
            warehouse_name,
            credits_used_cloud_services,
            execution_time / 1000.0             AS execution_seconds,
            bytes_scanned
        FROM snowflake.account_usage.query_history
        WHERE query_type = 'SELECT'
          AND execution_status = 'SUCCESS'
          AND start_time >= DATEADD(day, -{LOOKBACK_DAYS}, CURRENT_TIMESTAMP())
          AND query_text IS NOT NULL
          AND warehouse_name IS NOT NULL
        ORDER BY start_time DESC
        LIMIT 50000
    """
    print("Querying snowflake.account_usage.query_history (last 90 days) …")
    cur = conn.cursor()
    cur.execute(sql)
    cols = [d[0].lower() for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    cur.close()
    print(f"Fetched {len(rows)} query history rows.")
    return rows


def build_profiles(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        fp = fingerprint(row["query_text"])
        groups[fp].append(row)

    profiles = []
    for fp, executions in groups.items():
        count = len(executions)
        credits_list = [float(e["credits_used_cloud_services"] or 0) for e in executions]
        exec_time_list = [float(e["execution_seconds"] or 0) for e in executions]
        bytes_list = [float(e["bytes_scanned"] or 0) for e in executions]

        best_exec = min(executions, key=lambda e: float(e["credits_used_cloud_services"] or 0))
        best_warehouse = best_exec["warehouse_name"]

        sorted_times = sorted(exec_time_list)
        p95_idx = max(0, int(len(sorted_times) * 0.95) - 1)

        profiles.append({
            "fingerprint": fp,
            "best_warehouse": best_warehouse,
            "median_credits": round(median(credits_list), 6),
            "p95_execution_seconds": round(sorted_times[p95_idx], 2),
            "avg_bytes_scanned": int(round(sum(bytes_list) / count, 0)),
            "execution_count": count,
            "confidence": "high" if count > MIN_EXECUTIONS_HIGH_CONFIDENCE else "low",
            "sample_query": normalise_sql(executions[0]["query_text"])[:500],
        })

    print(f"Built {len(profiles)} fingerprint profiles from {len(rows)} executions.")
    return profiles


def embed_profiles(profiles: list[dict], embedder: HuggingFaceEmbeddings):
    pg = connect_pg()
    try:
        with pg.cursor() as cur:
            cur.execute("DELETE FROM query_fingerprint_embeddings")
            print("Cleared existing query_fingerprint_embeddings rows.")

            for i, p in enumerate(profiles, 1):
                doc = (
                    f"Query pattern: {p['sample_query']}\n"
                    f"Best warehouse: {p['best_warehouse']}\n"
                    f"Median credits: {p['median_credits']}\n"
                    f"P95 execution time: {p['p95_execution_seconds']}s\n"
                    f"Confidence: {p['confidence']} ({p['execution_count']} executions)"
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
                        p["fingerprint"],
                        p["sample_query"],
                        p["best_warehouse"],
                        p["median_credits"],
                        p["p95_execution_seconds"],
                        p["avg_bytes_scanned"],
                        p["execution_count"],
                        p["confidence"],
                        embedding,
                    ),
                )
                if i % 100 == 0:
                    print(f"  Embedded {i}/{len(profiles)} fingerprints …")

        pg.commit()
        print(f"\nDone. {len(profiles)} fingerprints embedded into 'query_fingerprint_embeddings'.")
    finally:
        pg.close()


def main():
    sf_conn = connect_snowflake()
    try:
        rows = fetch_query_history(sf_conn)
        if not rows:
            print("No query history found. Exiting.")
            sys.exit(0)
        profiles = build_profiles(rows)
    finally:
        sf_conn.close()

    print("Loading embedding model all-MiniLM-L6-v2 …")
    embedder = HuggingFaceEmbeddings(
        model_name="all-MiniLM-L6-v2",
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    embed_profiles(profiles, embedder)


if __name__ == "__main__":
    main()
