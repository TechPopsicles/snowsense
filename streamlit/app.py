import os
import time
import uuid

import httpx
import streamlit as st

AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8000")

# ── page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SnowSense",
    page_icon="❄️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── minimal custom CSS ────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .tool-pill {
        display: inline-block;
        background: #1e3a5f;
        color: #7ec8e3;
        border-radius: 12px;
        padding: 2px 10px;
        font-size: 0.78rem;
        font-family: monospace;
        margin: 2px 3px 2px 0;
    }
    .meta-row { font-size: 0.85rem; margin-bottom: 4px; }
    .meta-label { color: #888; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── session state ─────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []        # {role, content, meta?} for display
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())  # stable per browser session


# ── helpers ───────────────────────────────────────────────────────────────────
def agent_online() -> bool:
    try:
        r = httpx.get(f"{AGENT_URL}/health", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


def tool_pills(tools: list[str]) -> str:
    if not tools:
        return "<span class='meta-label'>none</span>"
    return " ".join(f"<span class='tool-pill'>{t}</span>" for t in tools)


def render_details(meta: dict):
    warehouse = meta.get("warehouse_used", "—")
    credits   = meta.get("credits_estimate", 0.0)
    tools     = meta.get("tool_calls_made", [])

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
            f"<div class='meta-row'><span class='meta-label'>Warehouse</span>&nbsp;&nbsp;"
            f"<strong>{warehouse}</strong></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div class='meta-row'><span class='meta-label'>Credits estimate</span>&nbsp;&nbsp;"
            f"<strong>{credits:.6f}</strong></div>",
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            f"<div class='meta-row'><span class='meta-label'>Tool calls ({len(tools)})</span></div>"
            + tool_pills(tools),
            unsafe_allow_html=True,
        )


# ── sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("SnowSense", divider="blue")
    st.caption("Natural language querying for Snowflake")

    st.subheader("Agent status")
    online = agent_online()
    if online:
        st.success("Agent online", icon="✅")
    else:
        st.error("Agent offline", icon="🔴")
        st.caption(f"Expecting agent at `{AGENT_URL}`")
        st.caption("Start with: `make up` or `cd agent && uvicorn main:app`")

    st.divider()

    st.subheader("Example questions")
    examples = [
        "What were the top 10 revenue customers last quarter?",
        "Show me monthly net revenue by market segment",
        "Which suppliers have the highest return rates?",
        "What is the customer LTV formula and where does it come from?",
        "What are the best-selling parts by quantity this year?",
    ]
    for ex in examples:
        if st.button(ex, use_container_width=True, key=ex):
            st.session_state._inject = ex

    st.divider()

    if st.button("Clear conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.thread_id = str(uuid.uuid4())  # new thread = fresh history
        st.rerun()

    st.caption(f"Agent URL: `{AGENT_URL}`")


# ── main chat area ────────────────────────────────────────────────────────────
st.title("❄️ SnowSense")
st.caption("Ask anything about your Snowflake data. The agent understands dbt semantics and optimises warehouse costs automatically.")

# render conversation history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("meta"):
            with st.expander("Details — warehouse · credits · tools called"):
                render_details(msg["meta"])

# pick up example button injection
injected = st.session_state.pop("_inject", None)

# chat input
question = st.chat_input("Ask a question about your data…") or injected

if question:
    if not online:
        st.error("Cannot send — agent is offline. Start it with `make up`.")
        st.stop()

    # show user message immediately
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # call agent
    with st.chat_message("assistant"):
        status = st.status("Thinking…", expanded=True)
        t0 = time.time()
        try:
            with status:
                st.write("Sending question to SnowSense agent…")
                resp = httpx.post(
                    f"{AGENT_URL}/ask",
                    json={
                        "question": question,
                        "thread_id": st.session_state.thread_id,
                    },
                    timeout=180.0,
                )
                resp.raise_for_status()
                data = resp.json()
                elapsed = time.time() - t0
                st.write(f"Response received in {elapsed:.1f}s")

            status.update(label="Done", state="complete", expanded=False)

            answer = data["answer"]
            st.markdown(answer)

            with st.expander("Details — warehouse · credits · tools called"):
                render_details(data)

            # persist display history (LangGraph checkpointer handles agent history)
            st.session_state.messages.append({
                "role": "assistant",
                "content": answer,
                "meta": data,
            })

        except httpx.HTTPStatusError as exc:
            status.update(label="Agent error", state="error", expanded=False)
            body = exc.response.text[:300]
            st.error(f"Agent returned HTTP {exc.response.status_code}")
            st.code(body, language="text")
            st.session_state.messages.append({
                "role": "assistant",
                "content": f"Error {exc.response.status_code}: {body}",
            })

        except httpx.TimeoutException:
            status.update(label="Timeout", state="error", expanded=False)
            msg = "Request timed out after 180 s. The query may be too complex or the warehouse is cold-starting."
            st.warning(msg)
            st.session_state.messages.append({"role": "assistant", "content": msg})

        except Exception as exc:
            status.update(label="Connection error", state="error", expanded=False)
            msg = f"Could not reach the agent at `{AGENT_URL}`.\n\n`{exc}`"
            st.error(msg)
            st.session_state.messages.append({"role": "assistant", "content": msg})
