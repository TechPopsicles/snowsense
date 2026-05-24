import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agent_loop import graph, run_agent_loop
from db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initialising pgvector schema…")
    init_db()
    logger.info("LangGraph compiled — graph ready.")
    yield


app = FastAPI(title="SnowSense Agent API", version="2.0.0", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str
    conversation_history: list = []
    thread_id: str = "default"


class AskResponse(BaseModel):
    answer: str
    warehouse_used: str
    credits_estimate: float
    tool_calls_made: list[str]
    reasoning: str


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest):
    """
    Receives a natural language question from Streamlit.
    Passes to the LangGraph agent with all tool definitions.
    Returns answer + metadata (warehouse used, credits, reasoning).
    """
    logger.info("Question (thread=%s): %s", request.thread_id, request.question)
    try:
        result = await run_agent_loop(
            request.question,
            request.conversation_history,
            thread_id=request.thread_id,
        )
        logger.info("Tool calls made: %s", result["tool_calls_made"])
        return AskResponse(**result)
    except Exception as exc:
        logger.exception("Agent loop error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health")
async def health():
    return {"status": "ok"}
