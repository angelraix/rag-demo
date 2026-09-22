"""
server.py — FastAPI backend for the stock filings RAG demo.

One endpoint: POST /ask
  - Embeds the query (OpenAI)
  - Retrieves top-k chunks from local Chroma, filtered by customer_id
  - Builds a single prompt and streams the response from the chosen model
  - Returns a text/event-stream so the UI can render tokens as they arrive

Run:
    uvicorn server:app --reload --port 8000
"""

import os
import json
import asyncio
from typing import AsyncIterator

import chromadb
from chromadb.utils import embedding_functions
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CHROMA_PATH  = "./chroma_db"
COLLECTION   = "filings"
EMBED_MODEL  = "text-embedding-3-small"
TOP_K        = 5          # chunks retrieved per query
MAX_TOKENS   = 1024

SUPPORTED_MODELS = {
    # Claude models
    "claude-sonnet-5":       "anthropic",
    "claude-haiku-4-5-20251001":      "anthropic",
    "claude-opus-5":         "anthropic",
    # OpenAI models
    "gpt-4o":                "openai",
    "gpt-4o-mini":           "openai",
}

SYSTEM_PROMPT = (
    "You are a senior financial analyst. "
    "Answer the user's question using ONLY the filing excerpts provided. "
    "For every claim, cite the ticker, filing type, and period in brackets, e.g. [ACME 10-K 2024]. "
    "If the excerpts do not contain enough information, say so clearly. "
    "Be concise and precise."
)

# ---------------------------------------------------------------------------
# Startup — load Chroma once
# ---------------------------------------------------------------------------
openai_api_key    = os.environ.get("OPENAI_API_KEY", "")
anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")

if not openai_api_key:
    raise SystemExit("OPENAI_API_KEY not set — needed for embeddings.")

_ef = embedding_functions.OpenAIEmbeddingFunction(
    api_key=openai_api_key,
    model_name=EMBED_MODEL,
)
_chroma = chromadb.PersistentClient(path=CHROMA_PATH)
_collection = _chroma.get_or_create_collection(
    name=COLLECTION,
    embedding_function=_ef,
    metadata={"hnsw:space": "cosine"},
)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class AskRequest(BaseModel):
    question:    str
    customer_id: str
    model:       str = "claude-sonnet-5"
    role_prompt: str | None = None   # optional CxO system-prompt addition


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def retrieve(question: str, customer_id: str) -> tuple[list[str], list[dict]]:
    """Embed the query and pull top-k chunks for this customer from Chroma."""
    results = _collection.query(
        query_texts=[question],
        where={"customer_id": customer_id},
        n_results=TOP_K,
        include=["documents", "metadatas"],
    )
    docs  = results["documents"][0]
    metas = results["metadatas"][0]
    return docs, metas


def build_context(docs: list[str], metas: list[dict]) -> str:
    parts = []
    for doc, m in zip(docs, metas):
        header = f"[{m.get('ticker','?')} {m.get('filing_type','?')} {m.get('period','?')} — {m.get('section','?')}]"
        parts.append(f"{header}\n{doc}")
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Generation — one streaming call per model
# ---------------------------------------------------------------------------
async def stream_anthropic(model: str, system: str, user_message: str) -> AsyncIterator[str]:
    import anthropic
    client = anthropic.Anthropic(api_key=anthropic_api_key)
    with client.messages.stream(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        for text in stream.text_stream:
            yield text


async def stream_openai(model: str, system: str, user_message: str) -> AsyncIterator[str]:
    from openai import OpenAI
    client = OpenAI(api_key=openai_api_key)
    with client.chat.completions.create(
        model=model,
        max_tokens=MAX_TOKENS,
        stream=True,
        messages=[
            {"role": "system",  "content": system},
            {"role": "user",    "content": user_message},
        ],
    ) as stream:
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


# ---------------------------------------------------------------------------
# /ask endpoint
# ---------------------------------------------------------------------------
@app.post("/ask")
async def ask(body: AskRequest):
    if body.model not in SUPPORTED_MODELS:
        raise HTTPException(400, f"Unsupported model: {body.model}")

    # 1. Retrieve relevant chunks (preprocessing — not counted as "the API call")
    docs, metas = retrieve(body.question, body.customer_id)
    if not docs:
        raise HTTPException(404, f"No filings found for customer '{body.customer_id}'.")

    # 2. Build the single prompt
    context      = build_context(docs, metas)
    system       = SYSTEM_PROMPT
    if body.role_prompt:
        system = body.role_prompt + "\n\n" + system
    user_message = f"Filing excerpts:\n\n{context}\n\n---\n\nQuestion: {body.question}"

    # 3. One streaming call to the model
    provider = SUPPORTED_MODELS[body.model]

    async def event_stream():
        try:
            if provider == "anthropic":
                async for token in stream_anthropic(body.model, system, user_message):
                    yield token
            else:
                async for token in stream_openai(body.model, system, user_message):
                    yield token
        except Exception as e:
            yield f"\n\n[ERROR: {e}]"

    return StreamingResponse(event_stream(), media_type="text/plain; charset=utf-8")


# ---------------------------------------------------------------------------
# /customers endpoint — let the UI discover available customers dynamically
# ---------------------------------------------------------------------------
@app.get("/customers")
def customers():
    try:
        # Peek at the collection metadata to get distinct customer_ids
        results = _collection.get(limit=2000, include=["metadatas"])
        ids = sorted(set(m["customer_id"] for m in results["metadatas"]))
        return {"customers": ids}
    except Exception:
        return {"customers": []}


# ---------------------------------------------------------------------------
# Serve static files (must be last)
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory="static", html=True), name="static")
