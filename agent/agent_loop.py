import json
import logging
import operator
import os
from typing import Annotated, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from prompts.system_prompt import SYSTEM_PROMPT
from tools import LANGCHAIN_TOOLS

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TOOL_CALLS = 20


# ── State ──────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    warehouse_used: str
    credits_estimate: float
    tool_calls_made: Annotated[list, operator.add]
    reasoning: str


# ── LLM with tools bound ──────────────────────────────────────────────────────

_llm = ChatAnthropic(model=MODEL, max_tokens=4096).bind_tools(LANGCHAIN_TOOLS)


# ── Nodes ──────────────────────────────────────────────────────────────────────

def call_model(state: AgentState) -> dict:
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = _llm.invoke(messages)

    new_tool_calls = [tc["name"] for tc in (response.tool_calls or [])]
    logger.info("call_model: tool_calls=%s", new_tool_calls)

    reasoning = ""
    content = response.content
    if isinstance(content, str):
        reasoning = content
    elif isinstance(content, list):
        reasoning = "\n".join(
            c.get("text", "") for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        )

    return {
        "messages": [response],
        "tool_calls_made": new_tool_calls,
        "reasoning": reasoning,
    }


# ── Edge ───────────────────────────────────────────────────────────────────────

def should_continue(state: AgentState) -> str:
    if len(state.get("tool_calls_made", [])) >= MAX_TOOL_CALLS:
        logger.warning("MAX_TOOL_CALLS (%d) reached — stopping.", MAX_TOOL_CALLS)
        return END

    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "run_tools"
    return END


# ── Graph ──────────────────────────────────────────────────────────────────────

_checkpointer = MemorySaver()
_tool_node = ToolNode(LANGCHAIN_TOOLS)


def _build_graph() -> StateGraph:
    g = StateGraph(AgentState)
    g.add_node("call_model", call_model)
    g.add_node("run_tools", _tool_node)
    g.add_edge(START, "call_model")
    g.add_conditional_edges(
        "call_model",
        should_continue,
        {"run_tools": "run_tools", END: END},
    )
    g.add_edge("run_tools", "call_model")
    return g.compile(checkpointer=_checkpointer)


graph = _build_graph()


# ── Public API ─────────────────────────────────────────────────────────────────

async def run_agent_loop(
    question: str,
    conversation_history: list,
    thread_id: str = "default",
) -> dict:
    config = {"configurable": {"thread_id": thread_id}}

    # Count existing messages so we can isolate this turn's additions
    saved = graph.get_state(config)
    prev_count = len(saved.values.get("messages", [])) if saved.values else 0

    await graph.ainvoke(
        {
            "messages": [HumanMessage(content=question)],
            "tool_calls_made": [],
            "warehouse_used": os.environ.get("SF_WAREHOUSE", "COMPUTE_XS"),
            "credits_estimate": 0.0,
            "reasoning": "",
        },
        config=config,
    )

    # Re-fetch state after completion (ainvoke returns the final snapshot)
    final = graph.get_state(config)
    all_messages = final.values.get("messages", [])
    new_messages = all_messages[prev_count:]

    # ── Extract answer from final non-tool AIMessage ───────────────────────────
    answer = ""
    for msg in reversed(new_messages):
        if isinstance(msg, AIMessage) and not msg.tool_calls:
            content = msg.content
            if isinstance(content, str):
                answer = content
            elif isinstance(content, list):
                answer = "\n".join(
                    c.get("text", "") for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                )
            break

    # ── Tool calls made this turn ──────────────────────────────────────────────
    tool_calls_made: list[str] = []
    for msg in new_messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            tool_calls_made.extend(tc["name"] for tc in msg.tool_calls)

    # ── Warehouse used (last successful use_warehouse result) ──────────────────
    warehouse_used = os.environ.get("SF_WAREHOUSE", "COMPUTE_XS")
    for msg in new_messages:
        if isinstance(msg, ToolMessage):
            try:
                result = json.loads(msg.content)
                if isinstance(result, dict) and result.get("warehouse"):
                    warehouse_used = result["warehouse"]
            except (json.JSONDecodeError, AttributeError):
                pass

    # ── Credits estimate ───────────────────────────────────────────────────────
    credits_estimate = tool_calls_made.count("run_query") * 0.0001

    # ── Reasoning (text blocks from AI messages this turn) ────────────────────
    reasoning_parts: list[str] = []
    for msg in new_messages:
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str) and content.strip():
                reasoning_parts.append(content)
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text" and c.get("text", "").strip():
                        reasoning_parts.append(c["text"])
    reasoning = "\n\n".join(reasoning_parts)

    return {
        "answer": answer,
        "warehouse_used": warehouse_used,
        "credits_estimate": round(credits_estimate, 6),
        "tool_calls_made": tool_calls_made,
        "reasoning": reasoning,
    }
