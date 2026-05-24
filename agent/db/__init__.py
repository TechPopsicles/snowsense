import logging
import os

import psycopg2
from pgvector.psycopg2 import register_vector

logger = logging.getLogger(__name__)


def _dsn() -> dict:
    return {
        "host": os.environ.get("PG_HOST", "postgres"),
        "port": int(os.environ.get("PG_PORT", "5432")),
        "dbname": os.environ.get("PG_DB", "snowsense"),
        "user": os.environ.get("PG_USER", "snowsense"),
        "password": os.environ.get("PG_PASSWORD", "snowsense"),
    }


def get_pg_connection() -> psycopg2.extensions.connection:
    conn = psycopg2.connect(**_dsn())
    register_vector(conn)
    return conn


def init_db() -> None:
    """Create extension and tables if they don't exist.

    Connects without register_vector because the vector type doesn't exist
    in pg_type until after CREATE EXTENSION runs — register_vector would
    raise 'vector type not found' if called before the schema executes.
    """
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    conn = psycopg2.connect(**_dsn())   # bare connect — extension not yet installed
    try:
        with open(schema_path) as f:
            sql = f.read()
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        logger.info("pgvector schema initialised")
    finally:
        conn.close()
