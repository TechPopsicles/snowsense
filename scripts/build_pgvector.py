"""
One-time script: embed dbt manifest into pgvector table 'dbt_metadata_embeddings'.

Usage:
    python scripts/build_pgvector.py

Requires:
    - dbt compile or dbt run to have been run (generates target/manifest.json)
    - Postgres + pgvector running (docker compose up postgres, or locally on port 5432)
    - PG_HOST / PG_PORT / PG_DB / PG_USER / PG_PASSWORD env vars
    - DBT_MANIFEST_PATH env var (default: ./dbt_project/target/manifest.json)
"""

import json
import os
import sys

import psycopg2
from langchain_huggingface import HuggingFaceEmbeddings
from pgvector.psycopg2 import register_vector

MANIFEST_PATH = os.environ.get(
    "DBT_MANIFEST_PATH", "./dbt_project/target/manifest.json"
)
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


def load_manifest(path: str) -> dict:
    if not os.path.exists(path):
        print(f"ERROR: manifest.json not found at {path}")
        print("Run 'make dbt-compile' first.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def build_document(node: dict) -> str:
    parts = [
        f"Model: {node['name']}",
        f"Description: {node.get('description', 'No description provided.')}",
        f"Schema: {node.get('schema', '')}",
        f"Database: {node.get('database', '')}",
    ]
    columns = node.get("columns", {})
    if columns:
        col_lines = [
            f"  - {col}: {meta.get('description', '')}"
            for col, meta in columns.items()
        ]
        parts.append("Columns:\n" + "\n".join(col_lines))
    tags = node.get("tags", [])
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")
    return "\n".join(parts)


def main():
    print(f"Loading manifest from {MANIFEST_PATH} …")
    manifest = load_manifest(MANIFEST_PATH)
    nodes = manifest.get("nodes", {})
    model_nodes = {k: v for k, v in nodes.items() if v.get("resource_type") == "model"}
    print(f"Found {len(model_nodes)} model nodes.")

    print("Loading embedding model all-MiniLM-L6-v2 …")
    embedder = HuggingFaceEmbeddings(
        model_name="all-MiniLM-L6-v2",
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    print(f"Connecting to Postgres at {PG_HOST}:{PG_PORT}/{PG_DB} …")
    conn = connect()

    with conn.cursor() as cur:
        cur.execute("DELETE FROM dbt_metadata_embeddings")
        print("Cleared existing dbt_metadata_embeddings rows.")

        for node_key, node in model_nodes.items():
            doc = build_document(node)
            embedding = embedder.embed_documents([doc])[0]
            meta = json.dumps({
                "tags": ",".join(node.get("tags", [])),
                "database": node.get("database", ""),
            })
            cur.execute(
                """
                INSERT INTO dbt_metadata_embeddings
                    (id, model_name, schema_name, description, embedding, metadata)
                VALUES (%s, %s, %s, %s, %s::vector, %s)
                ON CONFLICT (id) DO UPDATE
                    SET model_name  = EXCLUDED.model_name,
                        schema_name = EXCLUDED.schema_name,
                        description = EXCLUDED.description,
                        embedding   = EXCLUDED.embedding,
                        metadata    = EXCLUDED.metadata
                """,
                (
                    node_key,
                    node["name"],
                    node.get("schema", ""),
                    doc,
                    embedding,
                    meta,
                ),
            )
            print(f"  Embedded: {node['name']}")

    conn.commit()
    conn.close()
    print(f"\nDone. {len(model_nodes)} models embedded into 'dbt_metadata_embeddings'.")


if __name__ == "__main__":
    main()
