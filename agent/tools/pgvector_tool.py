import json
import logging
import os

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict

from db import get_pg_connection
from tools.embedding import get_embedder

logger = logging.getLogger(__name__)


@tool
async def search_metadata(query: str) -> str:
    """Search dbt model and column descriptions semantically.
    Use when the user asks what data exists, what a table means,
    which table to use for a business concept, or what a column represents.
    Returns ranked list of relevant dbt models with descriptions."""
    embedding = get_embedder().embed_query(query)
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT model_name, schema_name, description, metadata,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM dbt_metadata_embeddings
                ORDER BY embedding <=> %s::vector
                LIMIT 5
                """,
                (embedding, embedding),
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.error("search_metadata error: %s", exc)
        return json.dumps({"error": str(exc), "results": []})
    finally:
        conn.close()

    hits = []
    for row in rows:
        meta = row[3] or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        hits.append({
            "model": row[0],
            "schema": row[1],
            "description": row[2],
            "tags": meta.get("tags", ""),
            "similarity": round(float(row[4]), 4),
        })

    return json.dumps({"query": query, "results": hits})


class _GetLineageInput(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model_name: str


@tool("get_lineage", args_schema=_GetLineageInput)
async def get_lineage(model_name: str) -> str:
    """Get upstream and downstream lineage for a dbt model.
    Use when the user asks where data comes from or what depends on a table."""
    manifest_path = os.environ.get(
        "DBT_MANIFEST_PATH", "./dbt_project/target/manifest.json"
    )
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except FileNotFoundError:
        return json.dumps({
            "error": (
                f"manifest.json not found at {manifest_path}. "
                "Run 'dbt compile' or 'dbt run' first to generate it."
            )
        })

    nodes = manifest.get("nodes", {})
    parent_map = manifest.get("parent_map", {})
    child_map = manifest.get("child_map", {})
    sources = manifest.get("sources", {})

    target_key = next(
        (k for k in nodes if k.endswith(f".{model_name}")), None
    )
    if not target_key:
        return json.dumps({"error": f"Model '{model_name}' not found in manifest."})

    node = nodes[target_key]

    def _label(ref: str) -> str:
        if ref in nodes:
            return nodes[ref]["name"]
        if ref in sources:
            return f"source:{sources[ref]['name']}.{sources[ref]['identifier']}"
        return ref

    return json.dumps({
        "model": model_name,
        "description": node.get("description", ""),
        "upstream": [_label(r) for r in parent_map.get(target_key, [])],
        "downstream": [_label(r) for r in child_map.get(target_key, [])],
        "columns": {
            col: meta.get("description", "")
            for col, meta in node.get("columns", {}).items()
        },
    })
