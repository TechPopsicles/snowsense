import json
import logging
import os

import snowflake.connector
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_conn: snowflake.connector.SnowflakeConnection | None = None
_active_warehouse: str = ""


def _build_connection() -> snowflake.connector.SnowflakeConnection:
    kwargs: dict = {
        "account": os.environ["SF_ACCOUNT"],
        "user": os.environ["SF_USER"],
        "role": os.environ.get("SF_ROLE", "ANALYST_ROLE"),
        "database": os.environ.get("SF_DATABASE", "ANALYTICS"),
        "schema": os.environ.get("SF_SCHEMA", "PUBLIC"),
        "warehouse": os.environ.get("SF_WAREHOUSE", "COMPUTE_XS"),
        "session_parameters": {"QUERY_TAG": "snowsense_agent"},
    }

    private_key_path = os.environ.get("PRIVATE_KEY_PATH")
    if private_key_path:
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
            load_pem_private_key,
        )
        with open(private_key_path, "rb") as f:
            private_key = load_pem_private_key(f.read(), password=None)
        kwargs["private_key"] = private_key.private_bytes(
            Encoding.DER, PrivateFormat.PKCS8, NoEncryption()
        )
    else:
        kwargs["password"] = os.environ["SF_PASSWORD"]

    conn = snowflake.connector.connect(**kwargs)
    logger.info("Snowflake connection established (account=%s)", kwargs["account"])
    return conn


def _get_connection() -> snowflake.connector.SnowflakeConnection:
    global _conn
    if _conn is None or _conn.is_closed():
        _conn = _build_connection()
    return _conn


@tool
async def get_warehouse_load() -> str:
    """Returns current load on all available warehouses.
    Fields: warehouse_name, warehouse_size, queued_load, running_queries.
    Call this after search_fingerprints to check if the preferred warehouse is available or queued."""
    available = set(
        os.environ.get("AVAILABLE_WAREHOUSES", "COMPUTE_XS").split(",")
    )
    conn = _get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SHOW WAREHOUSES")
        cols = [d[0].lower() for d in cur.description]
        rows = cur.fetchall()
    finally:
        cur.close()

    warehouses = []
    for row in rows:
        rec = dict(zip(cols, row))
        name = rec.get("name", "")
        if name in available:
            warehouses.append({
                "warehouse_name": name,
                "warehouse_size": rec.get("size", ""),
                "state": rec.get("state", ""),
                "running_queries": int(rec.get("running") or 0),
                "queued_load": int(rec.get("queued") or 0),
            })

    return json.dumps({"warehouses": warehouses})


@tool
async def use_warehouse(warehouse_name: str) -> str:
    """Switch the active Snowflake warehouse BEFORE running a query.
    Must be called after get_warehouse_load and before run_query.
    Only switches context — does not execute anything."""
    global _active_warehouse
    conn = _get_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"USE WAREHOUSE IDENTIFIER('{warehouse_name}')")
        _active_warehouse = warehouse_name
        logger.info("Active warehouse switched to: %s", warehouse_name)
        return json.dumps({"status": "ok", "warehouse": warehouse_name})
    finally:
        cur.close()


@tool
async def run_query(sql: str) -> str:
    """Execute a SELECT query on Snowflake using the currently active warehouse.
    Returns rows as JSON and rows_returned count.
    ONLY run SELECT statements — any DDL or DML will be rejected."""
    if not sql.strip().upper().startswith("SELECT"):
        return json.dumps({
            "error": "Blocked. Only SELECT statements are permitted. DDL and DML are not allowed."
        })

    conn = _get_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql)
        rows = cur.fetchmany(500)
        cols = [d[0] for d in cur.description]
        data = [dict(zip(cols, row)) for row in rows]
        logger.info(
            "Query returned %d rows on warehouse %s", len(data), _active_warehouse
        )
        return json.dumps({
            "rows": data,
            "rows_returned": len(data),
            "warehouse": _active_warehouse,
        }, default=str)
    except snowflake.connector.errors.ProgrammingError as exc:
        logger.error("Snowflake query error: %s", exc)
        return json.dumps({"error": str(exc)})
    finally:
        cur.close()
