# LinkedIn Post — SnowSense (Final verified version)

---

I built an AI agent that queries Snowflake in plain English, understands
your business metrics through dbt, and automatically routes every query
to the right warehouse size to minimise cost.

It's called SnowSense. Here's what I built and what I learned.

---

**The problem:**
Three things slow down every data team working with Snowflake —
finding the right table, understanding what business metrics actually mean,
and queries burning credits on the wrong warehouse size.
SnowSense solves all three using an agentic AI architecture.

---

**The architecture — 5 layers:**

① **Streamlit → FastAPI → LangGraph**
The agent runtime is a LangGraph StateGraph — two nodes (call_model,
run_tools), one conditional edge. Claude autonomously decides which tools
to call, in what order, until it has enough information to answer.
No hardcoded routing logic. The LangGraph ToolNode executes all tool
calls in parallel via asyncio.gather.

② **pgvector semantic search**
14 dbt models embedded as 384-dimension vectors using all-MiniLM-L6-v2
via langchain-huggingface. Cosine similarity search finds the right model
for any business concept. Verified similarity scores:
mart_customer_value → int_customer_orders: 0.77 (direct upstream dependency
captured by the embedding — not keyword matching)

③ **Query fingerprinting for warehouse routing**
SQL is normalised (literals stripped, MD5 hashed) and matched against
historical execution profiles stored in pgvector. The cost gradient from
COMPUTE_XL (0.5 credits) to COMPUTE_XS (0.0001 credits) is 5000x.
Routing intelligently means real money saved at scale.

④ **dbt as semantic layer**
14 models across staging → intermediate → marts on Snowflake TPCH sample
data. Schema.yml descriptions are the intelligence layer — they tell the
agent to use customer_ltv not ORDERS.O_TOTALPRICE, and why. The agent
enforces your data governance rules because the semantic layer defines them.

⑤ **Governance by default**
Read-only Snowflake role + SELECT-only enforcement in code (not just the
prompt) + MAX_TOOL_CALLS=20 guard. Three independent layers — any one
catches what the others miss.

---

**The 6 tools Claude can call:**
→ search_metadata — semantic search over dbt descriptions
→ search_fingerprints — match SQL to historical execution profiles
→ get_warehouse_load — live queue depth per warehouse
→ use_warehouse — switch warehouse before executing
→ run_query — SELECT only, 500 row limit, rejects DDL in code
→ get_lineage — upstream + downstream from manifest.json

---

**What the Docker logs prove:**
For "who are the top 10 revenue generated customers?" Claude fired:
search_metadata → search_fingerprints → get_warehouse_load
→ use_warehouse(COMPUTE_XS_WH) → run_query
→ returned 10 rows with segment, nation, region, value tier

Warehouse chosen: COMPUTE_XS. Reasoning: single table scan, no joins,
simple ORDER BY — minimal compute needed. Estimated cost: 0.001 credits.

---

**Tech stack:**
Claude Sonnet · LangGraph · pgvector · dbt · langchain-huggingface ·
Snowflake TPCH sample data · FastAPI · Streamlit · Docker Compose

**Framework philosophy:**
I used LangGraph because it solves the agent orchestration problem properly —
stateful graph, parallel tool execution, built-in checkpointing. Same reason
you use Spring over raw servlets, or EKS over manual Kubernetes. Understanding
the underlying patterns (I designed the architecture) matters more than
having typed every line.

---

Full writeup on Medium → [link]
Code on GitHub → [link]

Happy to answer questions on any architectural decision.

#SnowSense #AgenticAI #Snowflake #dbt #LangGraph #pgvector #RAG
#DataEngineering #LLM #Claude #Python #Docker

---

## Short version (punchy alternative)

I built SnowSense — an AI agent for Snowflake that:

→ Answers natural language questions using dbt semantic layer + pgvector RAG
→ Routes queries to the right warehouse using SQL fingerprinting
   (5000x credit range — COMPUTE_XL at 0.5 credits to XS at 0.0001)
→ Shows its reasoning — warehouse chosen, credits estimated, tools called
→ Enforces governance at three independent layers

Stack: Claude Sonnet · LangGraph · pgvector · dbt · FastAPI · Streamlit · Docker

Verified end-to-end with screenshots, Docker logs, and pgvector similarity
scores. Every architectural decision documented and audited against spec.

Medium article → [link] | GitHub → [link]

#AgenticAI #Snowflake #dbt #LangGraph #RAG #DataEngineering
