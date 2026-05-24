# I built an agentic AI platform for Snowflake — here's every architectural decision and why

*LangGraph · pgvector · dbt semantic layer · Claude Sonnet · query fingerprinting
· warehouse cost optimisation · Docker Compose*

---

## Why I built this

I wanted a portfolio project that demonstrates real AI engineering depth —
not a chatbot, not a tutorial clone, but something that combines agentic
reasoning, RAG pipelines, vector databases, semantic layers, and production
governance in one coherent system.

The result is SnowSense: an AI agent that answers natural language questions
about Snowflake data, understands business semantics through dbt, and
automatically routes queries to the optimal warehouse to minimise cost.

This article documents every architectural decision, what the alternatives
were, and what the working system actually proved.

---

## The problem worth solving

Anyone working with Snowflake in a data team knows these three frustrations:

**Discovery** — "Which table has revenue data?" means 30 minutes hunting
schemas and asking colleagues. The answer is in your dbt docs — but nobody
reads dbt docs.

**Semantic gaps** — the database stores `O_ORDERSTATUS = 'F'` but the agent
needs to know F means fulfilled, and that `ORDERS.O_TOTALPRICE` overstates
revenue because it excludes discounts. That knowledge lives in dbt
descriptions — if you can surface it at query time.

**Cost surprises** — a full scan of LINEITEM (6M rows at SF1, 60M at SF10)
on the wrong warehouse size wastes real credits. The right warehouse depends
on the actual query shape and table size — not SQL syntax guessing.

SnowSense addresses all three. Here is how.

---

## Architecture overview

```
User → Streamlit UI → FastAPI → LangGraph StateGraph
                                      │
                      ┌───────────────┼──────────────────┐
                      │               │                  │
                 pgvector          get_lineage      Snowflake
              (RAG search)       (manifest.json)  (run_query)
                      │
              Warehouse tools
           (fingerprint + load)
```

**Docker Compose startup order:**
`postgres (healthy) → agent-init → agent → streamlit`

`agent-init` is a one-shot container that seeds the pgvector fingerprint
table and builds the dbt metadata index before the agent starts.
No manual setup steps after `docker compose up --build`.

---

## Component 1 — LangGraph as the agent runtime

The first architectural decision was the agent loop. The naive approach is
a `while True` loop that calls Claude, checks `stop_reason`, executes tools,
and repeats. This works but has real limitations: state is managed manually,
parallel tool calls need custom code, checkpointing for multi-turn
conversations is an afterthought.

LangGraph solves all three. It models the agent as an explicit graph:

```python
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode

class AgentState(TypedDict):
    messages:        Annotated[list, add_messages]
    warehouse_used:  str
    credits_estimate: float
    tool_calls_made: Annotated[list, operator.add]
    reasoning:       str

graph = StateGraph(AgentState)
graph.add_node("call_model", call_model)
graph.add_node("run_tools", ToolNode(LANGCHAIN_TOOLS))
graph.add_edge(START, "call_model")
graph.add_conditional_edges("call_model", should_continue)
graph.add_edge("run_tools", "call_model")
compiled = graph.compile(checkpointer=MemorySaver())
```

Two nodes. One conditional edge. The ToolNode executes all tool_use blocks
from Claude's response in parallel via `asyncio.gather` — automatically,
without custom parallelisation code.

`MemorySaver` checkpointer maps Streamlit session IDs to LangGraph thread
IDs, giving multi-turn conversation continuity with zero extra code.

**Why LangGraph over a raw while-loop:**
Same reason you use Spring over raw Java servlets — it solves the
orchestration problem properly so you focus on business logic. Understanding
the underlying pattern (the while-loop, the tool dispatch, the state
management) is what matters. LangGraph implements that pattern correctly
so you don't have to.

---

## Component 2 — pgvector replacing ChromaDB

The initial design used ChromaDB for vector storage. The upgrade to pgvector
was driven by one specific advantage: joins.

ChromaDB is a pure vector database. pgvector is a PostgreSQL extension —
which means your embeddings live in a relational database and can be joined
with other tables in a single query.

For SnowSense, the two tables are queried independently by `search_metadata`
and `search_fingerprints`. But because both live in the same PostgreSQL
instance, a future join is a single SQL query — not a Python merge across
two separate databases:

```sql
-- Possible with pgvector — would require two round-trips with ChromaDB
SELECT
    m.model_name,
    m.description,
    1 - (m.embedding <=> query_vec) AS similarity,
    f.best_warehouse,
    f.median_credits
FROM dbt_metadata_embeddings m
LEFT JOIN query_fingerprint_embeddings f
    ON f.sample_sql ILIKE '%' || m.model_name || '%'
WHERE 1 - (m.embedding <=> query_vec) > 0.6
ORDER BY similarity DESC
LIMIT 5;
```

This is a roadmap capability. Today the two tables are separate tool calls;
the point is that pgvector makes the join possible without a Python layer.

**pgvector is fully free and open-source** (PostgreSQL licence). It runs
in Docker via `pgvector/pgvector:pg16`. No managed service, no API cost.

**Verified similarity scores from production:**
```
mart_customer_value  → 1.0000  (anchor)
int_customer_orders  → 0.7712  (direct upstream — correctly highest)
mart_part_demand     → 0.6632  (related mart domain)
stg_orders           → 0.6212  (source table — correctly lower)
stg_customer         → 0.5917  (source table — correctly lower)
```

The embedding captured that `int_customer_orders` is the upstream feed of
`mart_customer_value` — without any explicit graph traversal. The vector
space learned the dependency relationship from the text descriptions alone.

---

## Component 3 — dbt as the semantic layer

Raw Snowflake schemas are syntactically queryable but semantically opaque.
`O_ORDERSTATUS = 'F'` means nothing without domain knowledge. `O_TOTALPRICE`
looks like revenue but overstates it. `DIM_CUSTOMERS` and `FACT_ORDERS`
join on `CUSTOMER_KEY` not `CUSTOMER_ID`.

dbt fixes this. Every model and column in `schema.yml` has a description
that becomes a vector in pgvector. When the agent asks "which table for
customer revenue?" — it finds `mart_customer_value` and reads:

```
"Always use customer_ltv — calculated as SUM(net_revenue) across all
line items, properly accounting for discounts. Never use
ORDERS.O_TOTALPRICE — it excludes discounts and overstates revenue."
```

That instruction, retrieved via semantic search, grounds Claude's SQL
generation in your actual business rules. Not because you wrote a rule
engine. Because your dbt authors wrote good descriptions.

**14 models built on TPCH SF1:**
- 8 staging views — direct reads from SNOWFLAKE_SAMPLE_DATA.TPCH_SF1
- 2 intermediate views — business joins and net_revenue macro
- 4 mart tables — mart_revenue, mart_customer_value,
  mart_supplier_performance, mart_part_demand

TPCH is available in every Snowflake account — no data import needed.

---

## Component 4 — Query fingerprinting for warehouse routing

Syntax-based warehouse classification is brittle. "4+ joins → Large
warehouse" fails when those joins are on 25-row lookup tables. The actual
cost depends on data volume — which only execution history knows.

Query fingerprinting extracts the shape of a SQL query by stripping
variable literals:

```python
def fingerprint_query(sql: str) -> str:
    sql = re.sub(r"'[^']*'", "?", sql)   # 'AUTOMOBILE' → ?
    sql = re.sub(r"\b\d+\b", "?", sql)   # 2024 → ?
    sql = re.sub(r"\s+", " ", sql).strip().upper()
    return hashlib.md5(sql.encode()).hexdigest()
```

Two queries that differ only in `WHERE year = 2023` vs `WHERE year = 2024`
produce the same fingerprint — same shape, same performance characteristics.

The fingerprint table stores execution profiles from 90 days of
`QUERY_HISTORY`: best warehouse, median credits, p95 execution seconds,
bytes scanned, execution count, confidence level.

**Verified cost gradient from fingerprint profiles:**
```
COMPUTE_XL → 0.5000 credits    (full scan + all joins)
COMPUTE_L  → 0.0500 credits    (multi-join aggregation)
COMPUTE_M  → 0.0100 credits    (medium aggregation)
COMPUTE_M  → 0.0050 credits    (simple aggregation)
COMPUTE_XS → 0.0002 credits    (single table scan)
COMPUTE_XS → 0.0001 credits    (lookup / small filter)
```

5000x difference between worst-case and optimal routing.
For a team running 1000 queries per day, intelligent routing is not
a nice-to-have — it is a meaningful cost control.

For new deployments with no query history, `seed_fingerprints.py` pre-loads
canonical TPCH patterns so routing works from day one.

---

## Component 5 — Embeddings: langchain-huggingface

The embedding model is `all-MiniLM-L6-v2` — 384 dimensions, runs locally
on CPU, baked into the Docker image at build time to avoid cold-start
latency.

The wrapper is `langchain-huggingface.HuggingFaceEmbeddings` — the current,
actively maintained LangChain integration for HuggingFace models,
co-maintained by LangChain and HuggingFace. The deprecated predecessor
(`langchain-community.HuggingFaceEmbeddings`) was migrated during
development after Claude Code flagged the deprecation warning.

Key config:
```python
HuggingFaceEmbeddings(
    model_name="all-MiniLM-L6-v2",
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True}
)
```

`normalize_embeddings=True` scales every vector to unit length before
storage. For cosine similarity search via pgvector's `<=>` operator,
normalised vectors produce maximum accuracy. This is a quiet quality
improvement over the default sentence-transformers behaviour.

`embed_query()` for single search strings at query time.
`embed_documents()` for batches at index-build time.
The distinction matters — using `embed_documents()` for single strings
at search time silently produces a list-wrapped result that breaks
pgvector parameterisation.

---

## Governance — three independent layers

Production AI systems on sensitive data need defence in depth.
SnowSense enforces governance at three independent levels:

**Layer 1 — Snowflake role**
The agent connects using a read-only role. `INSERT`, `UPDATE`, `DELETE`,
`DROP` are physically impossible regardless of what the agent generates.

**Layer 2 — Code enforcement**
`run_query()` validates the SQL string before execution:
```python
if not sql.strip().upper().startswith("SELECT"):
    return json.dumps({"error": "Blocked. Only SELECT statements are permitted."})
```
This fires even if the system prompt is ignored or bypassed.

**Layer 3 — System prompt**
Claude is instructed to only generate SELECT statements, warn before
scanning > 1TB, and explain warehouse choices. This is the softest layer —
but it handles the cases the other two don't catch (like choosing the
wrong table).

Any one layer catches what the others miss.

---

## What the logs proved

Docker log sequence for "who are the top 10 revenue generated customers?":

```
agent_loop — call_model: tool_calls=['search_metadata', 'search_fingerprints']
agent_loop — call_model: tool_calls=['get_warehouse_load']
warehouse_tool — Active warehouse switched to: COMPUTE_XS_WH
agent_loop — call_model: tool_calls=['run_query']
snowflake_connector — Query returned 10 rows on warehouse COMPUTE_XS_WH
agent_loop — call_model: tool_calls=[]
main — Tool calls made: ['search_metadata', 'search_fingerprints',
        'get_warehouse_load', 'use_warehouse', 'run_query']
```

Five tool calls. Claude planned and executed the full retrieval strategy
autonomously. The warehouse was COMPUTE_XS — single table scan, no joins,
correct choice, 0.001 credits.

---

## How it compares to Snowflake Cortex AI

Snowflake Cortex AI offers similar natural language querying capabilities.
SnowSense is not a replacement — it is a transparent implementation of
the same patterns.

| | SnowSense | Cortex AI |
|---|---|---|
| LLM | Claude Sonnet (external) | Snowflake-hosted models |
| RAG pipeline | Visible, tunable | Managed, opaque |
| Warehouse routing | Custom fingerprint logic | Not exposed |
| Semantic layer | dbt descriptions | Cortex Analyst semantic model |
| Purpose | Learning + portfolio | Production SaaS |

Building SnowSense taught me what Cortex abstracts away. That understanding
is the value — not the product itself.

---

## Running it yourself

```bash
git clone https://github.com/yourname/snowsense
cd snowsense && cp .env.example .env
# fill in SF_ACCOUNT, SF_USER, SF_PASSWORD (or PRIVATE_KEY_PATH), ANTHROPIC_API_KEY

cd dbt_project && dbt run && dbt docs generate && cd ..
docker compose up --build
# open http://localhost:8501
```

TPCH sample data is pre-loaded in every Snowflake account.
Total setup time: under 20 minutes.

---

## Known limitations (honest section)

- `ivfflat` indexes are sized for large datasets — plain scan is faster
  for the current 14-model corpus. Indexes pay off after real query history
  accumulates.
- Snowflake connection is a module-level singleton — container restart
  needed after connection drop.
- `build_pgvector.py` clears and rebuilds the metadata index on every
  `make up`. Run `dbt docs generate` before `docker compose up` or the
  metadata index starts empty.

---

## What I learned

**RAG quality is a data problem.** The similarity scores were semantically
correct because the dbt descriptions were semantically rich. Garbage
descriptions produce garbage retrieval regardless of the embedding model.

**LangGraph's ToolNode parallel execution is real.** Two tools called in
one Claude response execute simultaneously. For Snowflake queries with
real network latency, this matters.

**Fingerprinting beats syntax classification.** A 1-join query on LINEITEM
(6M rows) is heavier than a 6-join query on lookup tables. Only execution
history tells the truth.

**Deprecation warnings are worth fixing immediately.** `langchain-community`
flagged `HuggingFaceEmbeddings` as deprecated mid-development. Fixing it
to `langchain-huggingface` took five minutes. Leaving it would have created
a silent breakage on the next LangChain major version.

**Three governance layers is not paranoia.** Each layer catches edge cases
the others don't. The code-level SELECT check is the most important — it
fires even if the prompt is ignored.

---

*Built with Claude Sonnet · LangGraph · pgvector · dbt · langchain-huggingface
· FastAPI · Streamlit · Docker Compose*

*Source: github.com/yourname/snowsense*
