# SnowSense ❄️

**Natural language querying, lineage, and cost optimisation for Snowflake — powered by Claude.**

SnowSense is an agentic AI platform that answers business questions about your Snowflake
data in plain English. It understands your data semantics through dbt, routes every query
to the optimal warehouse size using historical fingerprinting, and explains every decision
it makes.

---

## Architecture

```
Streamlit UI  →  FastAPI  →  LangGraph (StateGraph)
                                  │
                    ┌─────────────┼──────────────────┐
                    │             │                  │
              pgvector      dbt manifest.json     Snowflake
           (RAG semantic   (get_lineage reads    (schema +
             search)        directly at          query exec)
                    │       query time)
              Warehouse tools
           (load check + switching)
```

**How dbt integrates (two paths, no MCP server):**
- `build_pgvector.py` — runs at startup via `agent-init`; parses `manifest.json` and embeds all model + column descriptions into `dbt_metadata_embeddings`. Powers `search_metadata` semantic search.
- `get_lineage` tool — reads `manifest.json` directly at query time to traverse upstream parents and downstream dependents. No live dbt process required.

**Agentic loop — LangGraph StateGraph:**
```
START → call_model → should_continue → run_tools → call_model → ... → END
```
Claude autonomously decides which tools to call, in what order, until it has
enough information to answer. No hardcoded routing logic.

---

## What it does

**Natural language querying** — ask questions in plain English, get SQL-backed answers
with business context and key observations.

**Semantic understanding** — 14 dbt models embedded as 384-dimension vectors in pgvector.
Cosine similarity search finds the right model for any business concept — not keyword
matching, but genuine semantic understanding.

**Cost-aware warehouse routing** — every query is fingerprinted (literals stripped, MD5
hashed) and matched against historical execution profiles. The agent checks live warehouse
queue depth before executing. XS to XL — chosen automatically, explained transparently.

**Lineage awareness** — upstream dependencies and downstream dbt tests shown on demand.
Reads directly from `manifest.json` at query time.

**Governance by default** — read-only Snowflake role, SELECT-only enforcement in code
(not just the prompt), MAX_TOOL_CALLS=20 guard, non-SELECT SQL rejected before execution.

---

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Streamlit |
| API | FastAPI + Uvicorn |
| Agent runtime | LangGraph (StateGraph + MemorySaver) |
| LLM | Claude Sonnet (claude-sonnet-4-6) via langchain-anthropic |
| Vector DB | pgvector on PostgreSQL 16 |
| Embeddings | all-MiniLM-L6-v2 (384 dims) via langchain-huggingface |
| Semantic layer | dbt (14 models on Snowflake TPCH sample data) |
| Data access | snowflake-connector-python |
| Containers | Docker Compose |

---

## LangGraph agent — how it works

Two nodes, one conditional edge:

```
call_model  — ChatAnthropic call with all 6 tools bound.
              Appends response to AgentState.messages.

run_tools   — LangGraph ToolNode. Executes ALL tool_use blocks
              from the preceding AIMessage in parallel via
              asyncio.gather. Returns all results in one pass.

should_continue — routes to run_tools if tool_calls present
                  and total < 20. Routes to END otherwise.
```

State fields persisted across nodes:
`messages · warehouse_used · credits_estimate · tool_calls_made · reasoning`

MemorySaver checkpointer enables multi-turn conversations — thread_id
from Streamlit session maps to LangGraph conversation thread.

---

## The 6 tools

| Tool | Purpose |
|---|---|
| `search_metadata` | Semantic search over 14 dbt model + column descriptions |
| `search_fingerprints` | Match incoming SQL to historical execution profiles |
| `get_warehouse_load` | Live queue depth and running query count per warehouse |
| `use_warehouse` | Switch active Snowflake warehouse before query execution |
| `run_query` | Execute SELECT on Snowflake — returns up to 500 rows as JSON |
| `get_lineage` | Upstream parents + downstream dependents from manifest.json |

---

## pgvector schema

```sql
CREATE EXTENSION IF NOT EXISTS vector;

-- dbt semantic layer — 14 models embedded
CREATE TABLE dbt_metadata_embeddings (
    id            TEXT PRIMARY KEY,
    model_name    TEXT,
    schema_name   TEXT,
    description   TEXT,
    embedding     vector(384),
    metadata      JSONB
);

-- Query execution profiles — warehouse routing intelligence
CREATE TABLE query_fingerprint_embeddings (
    fingerprint_hash  TEXT PRIMARY KEY,
    sample_sql        TEXT,
    best_warehouse    TEXT,
    median_credits    FLOAT,
    p95_exec_seconds  FLOAT,
    avg_bytes_scanned BIGINT,
    execution_count   INT,
    confidence        TEXT,
    embedding         vector(384)
);

-- ivfflat indexes for cosine similarity search
CREATE INDEX ON dbt_metadata_embeddings
    USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX ON query_fingerprint_embeddings
    USING ivfflat (embedding vector_cosine_ops);
```

---

## dbt project — 14 models on TPCH

```
Source: SNOWFLAKE_SAMPLE_DATA.TPCH_SF1

Staging (8 views)       — stg_orders, stg_lineitem, stg_customer,
                           stg_supplier, stg_part, stg_partsupp,
                           stg_nation, stg_region

Intermediate (2 views)  — int_orders_lineitems, int_customer_orders

Marts (4 tables)        — mart_revenue, mart_customer_value,
                           mart_supplier_performance, mart_part_demand

Macro: net_revenue = extended_price * (1 - discount) * (1 + tax)
```

Key semantic rule enforced via dbt descriptions and agent system prompt:
always use `customer_ltv` (discount-adjusted), never `ORDERS.O_TOTALPRICE`.

---

## Prerequisites

- Docker Desktop
- Snowflake account (free trial works — TPCH sample data pre-loaded)
- Anthropic API key
- dbt CLI: `pip install dbt-snowflake`

---

## Quick start

```bash
git clone https://github.com/yourname/snowsense.git
cd snowsense
cp .env.example .env
# fill in SF_ACCOUNT, SF_USER, SF_PASSWORD, ANTHROPIC_API_KEY

# Build dbt semantic layer
cd dbt_project
dbt debug && dbt run && dbt docs generate
cd ..

# Start everything — postgres → agent-init → agent → streamlit
docker compose up --build
```

Open `http://localhost:8501`

**Startup order is automatic:**
`postgres (healthy) → agent-init (seeds fingerprints + builds pgvector index) → agent → streamlit`

---

## Example queries

```
who are the top 10 revenue generated customers?
show me monthly net revenue by market segment
which suppliers have the highest return rates?
what is the customer LTV formula and where does it come from?
what are the best-selling parts by quantity this year?
what does mart_revenue depend on?
which table should I use for customer revenue analysis?
```

---

## Verified similarity scores (pgvector)

Cosine similarity search anchored to `mart_customer_value`:

| Model | Similarity | Why |
|---|---|---|
| mart_customer_value | 1.0000 | itself |
| int_customer_orders | 0.7712 | direct upstream feed |
| mart_part_demand | 0.6632 | related mart domain |
| stg_orders | 0.6212 | source — more generic |
| stg_customer | 0.5917 | source — more generic |

Ranking is semantically correct — the embedding captured the upstream
dependency relationship without any explicit graph traversal.

---

## Warehouse cost gradient (fingerprint profiles)

| Pattern | Warehouse | Median credits | Confidence |
|---|---|---|---|
| Full scan + all joins | COMPUTE_XL | 0.5000 | low |
| Multi-join aggregation | COMPUTE_L | 0.0500 | high |
| Medium aggregation | COMPUTE_M | 0.0100 | high |
| Simple aggregation | COMPUTE_M | 0.0050 | high |
| Single table scan | COMPUTE_XS | 0.0002 | high |
| Lookup / small filter | COMPUTE_XS | 0.0001 | high |

5000x cost difference between worst-case and best-case routing.

---

## Known limitations

- `ivfflat` indexes sized for large datasets — plain scan faster for current
  14-model corpus. Indexes pay off after `build_fingerprints.py` populates
  hundreds of real query history patterns.
- Snowflake connection is a module-level singleton — container restart needed
  after connection drop.
- `build_pgvector.py` clears and rebuilds the metadata index on every
  `make up` — even if `manifest.json` has not changed.
- If `dbt_project/target/manifest.json` does not exist, metadata index is
  silently skipped. Run `dbt docs generate` before `docker compose up`.

---

## Roadmap

- [ ] dbt MCP server — live metric lookup and semantic layer queries (currently dbt contributes only via manifest.json at startup and query time; a running MCP server would enable richer, real-time access)
- [ ] pgAdmin service for vector table inspection
- [ ] LangSmith tracing dashboard
- [ ] `build_pgvector.py` manifest change detection (skip rebuild if unchanged)
- [ ] Snowflake connection pooling
- [ ] Pinecone swap path for cloud deployment

---

## What this demonstrates

- **Agentic AI** — LangGraph StateGraph with parallel tool execution,
  stateful conversation, and autonomous multi-step reasoning
- **RAG pipeline** — dbt metadata embedded as 384-dim vectors, retrieved
  by cosine similarity, grounding every SQL generation decision
- **Semantic layer integration** — dbt descriptions as the business
  intelligence layer between raw Snowflake tables and the AI agent
- **Cost engineering** — learned warehouse sizing from execution history,
  not syntax heuristics — 5000x credit range managed automatically
- **Governance in depth** — role-level, code-level, and prompt-level
  controls layered independently
