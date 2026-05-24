CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS dbt_metadata_embeddings (
    id          TEXT PRIMARY KEY,
    model_name  TEXT,
    schema_name TEXT,
    description TEXT,
    embedding   vector(384),
    metadata    JSONB
);

CREATE TABLE IF NOT EXISTS query_fingerprint_embeddings (
    fingerprint_hash  TEXT PRIMARY KEY,
    sample_sql        TEXT,
    best_warehouse    TEXT,
    warehouse_size    TEXT,
    median_credits    FLOAT,
    p95_exec_seconds  FLOAT,
    avg_bytes_scanned BIGINT,
    execution_count   INT,
    confidence        TEXT,
    embedding         vector(384)
);

CREATE INDEX IF NOT EXISTS dbt_metadata_emb_idx
    ON dbt_metadata_embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS fingerprint_emb_idx
    ON query_fingerprint_embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
