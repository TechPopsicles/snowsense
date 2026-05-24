# SnowSense — Project Brief for Claude Code

## What is SnowSense?
An agentic AI platform that lets users query Snowflake using natural language.
It understands business semantics via dbt, optimises warehouse cost via query
fingerprinting, and is orchestrated by Claude Sonnet acting as an autonomous agent.

**Tagline:** Natural language querying, lineage, and cost optimisation for Snowflake.

---

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Streamlit (open-source, NOT Snowflake native Streamlit) |
| API layer | FastAPI |
| Agent / LLM | Claude Sonnet (`claude-sonnet-4-20250514`) via Anthropic API |
| Semantic search | ChromaDB + sentence-transformers (`all-MiniLM-L6-v2`) |
| Business semantics | dbt MCP server |
| Data access | Snowflake MCP server |
| Cost optimiser | Custom Python tools (warehouse load + query fingerprinting) |
| Containerisation | Docker Compose |

---

## Folder structure to scaffold

```
snowsense/
├── docker-compose.yml
├── .env.example
├── BRIEF.md
│
├── streamlit/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py
│
├── agent/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py               # FastAPI app — POST /ask endpoint
│   ├── agent_loop.py         # Claude agentic while-loop
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── chromadb_tool.py  # RAG search over dbt metadata + fingerprints
│   │   ├── warehouse_tool.py # get_warehouse_load + use_warehouse
│   │   └── fingerprint.py    # query fingerprinting + ChromaDB index builder
│   └── prompts/
│       └── system_prompt.py  # system prompt with reasoning instructions
│
├── chromadb/
│   └── (persisted by Docker volume — no code needed here)
│
└── scripts/
    ├── build_chroma.py       # one-time: embed dbt manifest into ChromaDB
    └── build_fingerprints.py # one-time: fingerprint query_history + embed
```

---

## Docker Compose spec

Three containers, one network:

```yaml
services:
  streamlit:
    build: ./streamlit
    ports: ["8501:8501"]
    environment:
      AGENT_URL: http://agent:8000
    depends_on: [agent]

  agent:
    build: ./agent
    ports: ["8000:8000"]
    env_file: .env
    environment:
      CHROMA_HOST: chromadb
      CHROMA_PORT: 8000
    depends_on: [chromadb]

  chromadb:
    image: chromadb/chroma:latest
    ports: ["8000:8000"]
    volumes:
      - ./chroma-data:/chroma/chroma
```

---

## Environment variables (.env.example)

```
ANTHROPIC_API_KEY=sk-ant-...

SF_ACCOUNT=orgname-accountname
SF_USER=myuser
SF_PASSWORD=mypassword
SF_ROLE=ANALYST_ROLE        # read-only role — governance constraint
SF_WAREHOUSE=COMPUTE_XS     # default warehouse (agent may switch)
SF_DATABASE=ANALYTICS
SF_SCHEMA=PUBLIC

DBT_MANIFEST_PATH=./dbt_project/target/manifest.json

AVAILABLE_WAREHOUSES=COMPUTE_XS,COMPUTE_M,COMPUTE_L,COMPUTE_XL
```

---

## FastAPI — POST /ask endpoint (agent/main.py)

```python
@app.post("/ask")
async def ask(request: AskRequest):
    """
    Receives natural language question from Streamlit.
    Passes to Claude agent loop with all tool definitions.
    Returns answer + metadata (warehouse used, credits, reasoning).
    """
```

Request schema:
```python
class AskRequest(BaseModel):
    question: str
    conversation_history: list = []   # for multi-turn support
```

Response schema:
```python
class AskResponse(BaseModel):
    answer: str
    warehouse_used: str
    credits_estimate: float
    tool_calls_made: list[str]        # audit trail of what Claude called
    reasoning: str                    # Claude's explanation of decisions
```

---

## Agent loop (agent/agent_loop.py)

Implement a `while True` loop that:
1. Sends message + tool definitions to Claude Sonnet
2. On `stop_reason == "tool_use"` — collects ALL `tool_use` blocks (not just [0])
3. Executes each tool by dispatching to the tools/ module
4. Returns ALL tool results in a single `tool_result` user message
5. On `stop_reason == "end_turn"` — extracts text and returns

Key detail: always handle parallel tool calls. Claude may call multiple tools
in one response. Collect all `tool_use` blocks, execute them all, return all
results together.

---

## Tool definitions (pass to Claude as `tools=[]`)

### 1. search_metadata
```
name: search_metadata
description: Search dbt model and column descriptions semantically.
             Use when the user asks what data exists, what a table means,
             which table to use for a business concept, or what a column represents.
             Returns ranked list of relevant dbt models with descriptions.
input: { query: string }
```

### 2. search_fingerprints
```
name: search_fingerprints
description: Find historical execution profiles for queries similar to the
             current one. Returns best warehouse, median credits, p95 execution
             time, and confidence level based on real query history.
             Use this BEFORE choosing a warehouse for any query.
input: { query_sql: string }
```

### 3. get_warehouse_load
```
name: get_warehouse_load
description: Returns current load on all available warehouses.
             Fields: warehouse_name, warehouse_size, queued_load, running_queries.
             Call this after search_fingerprints to check if preferred warehouse
             is available or queued.
input: {}   # no input needed
```

### 4. use_warehouse
```
name: use_warehouse
description: Switch the active Snowflake warehouse BEFORE running a query.
             Must be called after get_warehouse_load and before run_query.
             Only switches context — does not execute anything.
input: { warehouse_name: string }
```

### 5. run_query
```
name: run_query
description: Execute a SELECT query on Snowflake using the currently active
             warehouse. Returns rows as JSON + rows_returned count.
             ONLY run SELECT statements. Reject any DDL or DML.
input: { sql: string }
```

### 6. get_lineage
```
name: get_lineage
description: Get upstream and downstream lineage for a dbt model.
             Use when user asks where data comes from or what depends on a table.
input: { model_name: string }
```

---

## System prompt (prompts/system_prompt.py)

```python
SYSTEM_PROMPT = """
You are SnowSense, an AI data assistant for Snowflake.
You have deep knowledge of the organisation's data through dbt metadata.

## Your capabilities
- Answer natural language questions about data using Snowflake
- Understand business metrics and table semantics via dbt definitions
- Optimise query cost by routing to the right warehouse size
- Explain data lineage — where numbers come from and what depends on them

## Reasoning rules — follow these in order for every query

### Step 1 — Understand the data
Call search_metadata to find which dbt models are relevant.
Never assume table names — always search first.

### Step 2 — Get business definitions
If the question involves a metric (revenue, LTV, churn, MRR),
call get_lineage to get the approved dbt definition.
Never invent metric definitions.

### Step 3 — Pick the right warehouse
a. Call search_fingerprints with the SQL you plan to run.
b. Call get_warehouse_load to check live queue depth.
c. Decision logic:
   - fingerprint match with high confidence → use recommended warehouse
     UNLESS queued_load > 5 → step up one size
   - no fingerprint match → classify by SQL complexity:
       single table, no joins, < 7 day range → XS
       2-3 joins OR 7-90 day range           → M
       4+ joins OR 90d+ OR window functions  → L or XL
   - if preferred warehouse queued_load > 5 → pick next available size up
d. Call use_warehouse() with chosen warehouse before running SQL.

### Step 4 — Execute safely
Only run SELECT statements.
Always call use_warehouse before run_query.
If a query would scan > 1TB, warn the user and ask for confirmation.

### Step 5 — Answer clearly
Include in your response:
- The answer to the question
- Which warehouse you used and why
- Estimated credits consumed
- Which dbt models you used as source of truth

## Governance rules
- Never run INSERT, UPDATE, DELETE, DROP, CREATE, or ALTER
- Never access schemas outside the configured SF_DATABASE
- If asked to do something outside these rules, explain why you cannot
"""
```

---

## ChromaDB builder scripts

### scripts/build_chroma.py
Reads `target/manifest.json` from dbt project.
For each model node:
- Chunks: model name + description + all column names + column descriptions
- Embeds using `sentence-transformers` model `all-MiniLM-L6-v2` (runs locally, free)
- Stores in ChromaDB collection named `dbt_metadata`
- Metadata fields: model_name, schema, database, tags

### scripts/build_fingerprints.py
Connects to Snowflake using env vars.
Queries `snowflake.account_usage.query_history` for last 90 days, SELECT only, SUCCESS only.
For each query:
- Strips string literals (`'value'` → `?`) and numeric literals (`2024` → `?`)
- Normalises whitespace, uppercases
- MD5 hashes the normalised SQL as fingerprint key
Groups by fingerprint, computes per-fingerprint profile:
- best_warehouse (lowest credits execution)
- median_credits
- p95_execution_seconds
- avg_bytes_scanned
- execution_count
- confidence: "high" if count > 10, else "low"
Embeds the profile as text into ChromaDB collection `query_fingerprints`.

---

## Streamlit UI (streamlit/app.py)

Single-page app with:
- Title: SnowSense
- Subtitle: Natural language querying for Snowflake
- Chat interface (st.chat_message / st.chat_input)
- Each assistant response shows:
  - Answer text
  - Expandable "Details" section showing: warehouse used, credits, tools called
- Session state holds conversation history for multi-turn support
- On submit: POST to `{AGENT_URL}/ask` with question + history
- Handle errors gracefully with user-friendly messages

---

## Key implementation notes for Claude Code

1. All tool execution happens in FastAPI — Claude decides, FastAPI executes
2. ChromaDB client in agent connects to `chromadb:8000` (Docker service name)
3. Snowflake connector uses `snowflake-connector-python` — not SQLAlchemy
4. Embedding model (`all-MiniLM-L6-v2`) runs inside the agent container — no API key needed
5. Warehouse switching: execute `USE WAREHOUSE {name}` via Snowflake connector before query
6. The agent loop must handle parallel tool calls — collect ALL tool_use blocks per response
7. Add request logging to FastAPI so tool call sequences are visible in Docker logs
8. governance: validate every SQL string starts with SELECT before execution — reject otherwise

---

## What success looks like

User types: "What were our top 10 highest revenue customers last quarter?"

Agent does:
1. search_metadata("revenue customers") → finds mart_customer_revenue, fct_orders
2. get_lineage("mart_customer_revenue") → confirms net_revenue definition
3. search_fingerprints(generated_sql) → finds profile: COMPUTE_M, 0.12 credits, high confidence
4. get_warehouse_load() → COMPUTE_M queued_load = 1, available
5. use_warehouse("COMPUTE_M")
6. run_query(SELECT ...) → returns 10 rows

User sees: results table + "Used COMPUTE_M (0.12 credits) — matched historical pattern for this query type"

---

## Source data — Snowflake TPCH sample database

Use Snowflake's built-in TPCH sample data as the source for all dbt models.

### Connection details
```
Database : SNOWFLAKE_SAMPLE_DATA
Schema   : TPCH_SF1              # SF1 = ~1GB, good for local dev
```

Available scale factors (all pre-loaded in Snowflake sample data):
- `TPCH_SF1`   — 1GB   — use this for development
- `TPCH_SF10`  — 10GB  — use for warehouse sizing demos
- `TPCH_SF100` — 100GB — use to show XL warehouse justification

### TPCH source tables (8 tables)

| Table | Rows (SF1) | Key columns |
|---|---|---|
| ORDERS | 1.5M | O_ORDERKEY, O_CUSTKEY, O_TOTALPRICE, O_ORDERDATE, O_ORDERSTATUS |
| LINEITEM | 6M | L_ORDERKEY, L_PARTKEY, L_SUPPKEY, L_QUANTITY, L_EXTENDEDPRICE, L_DISCOUNT, L_TAX |
| CUSTOMER | 150K | C_CUSTKEY, C_NAME, C_NATIONKEY, C_ACCTBAL, C_MKTSEGMENT |
| SUPPLIER | 10K | S_SUPPKEY, S_NAME, S_NATIONKEY, S_ACCTBAL |
| PART | 200K | P_PARTKEY, P_NAME, P_TYPE, P_SIZE, P_RETAILPRICE |
| PARTSUPP | 800K | PS_PARTKEY, PS_SUPPKEY, PS_SUPPLYCOST, PS_AVAILQTY |
| NATION | 25 | N_NATIONKEY, N_NAME, N_REGIONKEY |
| REGION | 5 | R_REGIONKEY, R_NAME |

### dbt project structure to generate

```
dbt_project/
├── dbt_project.yml
├── profiles.yml
├── models/
│   ├── staging/              # direct reads from TPCH source
│   │   ├── stg_orders.sql
│   │   ├── stg_lineitem.sql
│   │   ├── stg_customer.sql
│   │   ├── stg_supplier.sql
│   │   ├── stg_part.sql
│   │   ├── stg_nation.sql
│   │   ├── stg_region.sql
│   │   └── schema.yml        # column descriptions for all staging models
│   ├── intermediate/         # joins and business logic
│   │   ├── int_orders_lineitems.sql   # orders joined with line items
│   │   ├── int_customer_orders.sql    # customer joined with their orders
│   │   └── schema.yml
│   └── marts/                # final business-facing models
│       ├── mart_revenue.sql           # daily/monthly revenue aggregation
│       ├── mart_customer_value.sql    # customer LTV and segment analysis
│       ├── mart_supplier_performance.sql
│       ├── mart_part_demand.sql
│       └── schema.yml        # CRITICAL: rich descriptions for RAG embedding
└── macros/
    └── revenue_net.sql       # net_revenue = (extendedprice * (1-discount)) * (1+tax)
```

### Key business metrics to define in schema.yml (these feed ChromaDB)

These must have rich, detailed descriptions — they are the semantic layer
that Claude uses to answer natural language questions correctly:

**net_revenue**
"Net revenue per line item after applying customer discount and adding tax.
Formula: L_EXTENDEDPRICE * (1 - L_DISCOUNT) * (1 + L_TAX).
Use this column, never L_EXTENDEDPRICE alone, for any revenue reporting."

**customer_ltv**
"Lifetime value of a customer — total net revenue generated across all their
orders since account creation. Source: mart_customer_value. Never sum
raw ORDERS.O_TOTALPRICE for this metric — it excludes discounts."

**order_status_label**
"Human-readable order status. O=open (being processed), F=fulfilled
(shipped), P=partially fulfilled. Filter to status='F' for completed
revenue figures."

**market_segment**
"Customer market segment from TPCH. Values: AUTOMOBILE, BUILDING,
FURNITURE, HOUSEHOLD, MACHINERY. Use for customer cohort analysis."

### Warehouse sizing expectations with TPCH (embed in fingerprint index)

These examples demonstrate why fingerprinting matters — same operation,
different table size, very different warehouse need:

| Query pattern | Table | Scale | Right warehouse |
|---|---|---|---|
| Daily order count | ORDERS | SF1 | XS |
| Full customer scan + nation join | CUSTOMER + NATION | SF1 | XS |
| 30-day revenue by segment | ORDERS + LINEITEM | SF1 | S or M |
| Full lineitem scan, all time | LINEITEM | SF1 | M |
| Full lineitem scan | LINEITEM | SF10 | L |
| Full lineitem scan + all joins | LINEITEM + all | SF100 | XL |

### What to tell Claude Code about dbt setup

"Generate a complete dbt project for Snowflake TPCH_SF1 sample data.
Source database is SNOWFLAKE_SAMPLE_DATA, schema TPCH_SF1.
Create staging, intermediate, and mart layers.
Schema.yml files must have detailed column and model descriptions
because they will be embedded into ChromaDB as the semantic layer
for an AI agent. Richer descriptions = better AI answers."