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

from typing import Optional, List
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
FILINGS_DIR  = "./filings"
TOP_K        = 5          # chunks retrieved per company per query
MAX_CHUNKS   = 25         # hard cap on total chunks sent to the model
MAX_TOKENS   = 1024

SUPPORTED_MODELS = {
    "claude-sonnet-5":            "anthropic",
    "claude-haiku-4-5-20251001":  "anthropic",
    "claude-opus-5":              "anthropic",
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
anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")

_ef = embedding_functions.DefaultEmbeddingFunction()

def _scan_customer_ids() -> list:
    ids = set()
    if os.path.isdir(FILINGS_DIR):
        for f in os.listdir(FILINGS_DIR):
            if f.endswith(('.txt', '.pdf')):
                ids.add(f.split('_')[0].lower())
    return sorted(ids)

_customer_ids = _scan_customer_ids()
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
    question:     str
    customer_ids: List[str]
    model:        str = "claude-sonnet-5"
    role_prompt:  Optional[str] = None   # optional CxO system-prompt addition


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def _diversify_by_period(docs: list, metas: list, target: int) -> tuple:
    """Prefer one chunk per (company, period) before repeating any period."""
    seen = set()
    primary, secondary = [], []
    for doc, meta in zip(docs, metas):
        key = (meta.get("customer_id"), meta.get("period"))
        if key not in seen:
            seen.add(key)
            primary.append((doc, meta))
        else:
            secondary.append((doc, meta))
    combined = (primary + secondary)[:target]
    return [d for d, _ in combined], [m for _, m in combined]


def retrieve(question: str, customer_ids: list) -> tuple:
    """Embed the query and pull top-k chunks per company from Chroma, with temporal diversity."""
    target     = MAX_CHUNKS if not customer_ids else min(TOP_K * len(customer_ids), MAX_CHUNKS)
    n_fetch    = min(target * 2, 50)   # fetch extra candidates for diversity re-ranking
    kwargs: dict = dict(
        query_texts=[question],
        n_results=n_fetch,
        include=["documents", "metadatas"],
    )
    if customer_ids:
        kwargs["where"] = {"customer_id": {"$in": customer_ids}} if len(customer_ids) > 1 else {"customer_id": customer_ids[0]}
    results    = _collection.query(**kwargs)
    docs, metas = results["documents"][0], results["metadatas"][0]
    return _diversify_by_period(docs, metas, target)


def build_context(docs: list, metas: list) -> str:
    # Group by ticker so multi-company prompts are clearly structured
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for doc, m in zip(docs, metas):
        groups[m.get("ticker", "?")].append((doc, m))

    sections = []
    for ticker, items in groups.items():
        chunks = []
        for doc, m in items:
            header = f"[{m.get('ticker','?')} {m.get('filing_type','?')} {m.get('period','?')} — {m.get('section','?')}]"
            chunks.append(f"{header}\n{doc}")
        company_block = "\n\n---\n\n".join(chunks)
        if len(groups) > 1:
            company_block = f"## {ticker}\n\n{company_block}"
        sections.append(company_block)

    return "\n\n===\n\n".join(sections)


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


# ---------------------------------------------------------------------------
# /ask endpoint
# ---------------------------------------------------------------------------
@app.post("/ask")
async def ask(body: AskRequest):
    if body.model not in SUPPORTED_MODELS:
        raise HTTPException(400, f"Unsupported model: {body.model}")

    # 1. Retrieve relevant chunks (preprocessing — not counted as "the API call")
    docs, metas = retrieve(body.question, body.customer_ids)
    if not docs:
        raise HTTPException(404, f"No filings found for the selected customers.")

    # 2. Build the single prompt
    context  = build_context(docs, metas)
    tickers  = sorted(set(m.get("ticker", "") for m in metas if m.get("ticker")))
    system   = SYSTEM_PROMPT

    if len(tickers) > 1:
        comparison_prefix = (
            f"The user is asking about multiple companies: {', '.join(tickers)}. "
            "Structure your answer by addressing each company in turn, then synthesize "
            "the key similarities and differences. Use a header for each company."
        )
        system = comparison_prefix + "\n\n" + system

    if body.role_prompt:
        system = body.role_prompt + "\n\n" + system

    user_message = f"Filing excerpts:\n\n{context}\n\n---\n\nQuestion: {body.question}"

    # 3. One streaming call to the model
    async def event_stream():
        try:
            async for token in stream_anthropic(body.model, system, user_message):
                yield token
        except Exception as e:
            yield f"\n\n[ERROR: {e}]"

    return StreamingResponse(event_stream(), media_type="text/plain; charset=utf-8")


# ---------------------------------------------------------------------------
# /customers endpoint — let the UI discover available customers dynamically
# ---------------------------------------------------------------------------
@app.get("/customers")
def customers():
    return {"customers": _customer_ids}


# ---------------------------------------------------------------------------
# Serve static files (must be last)
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory="static", html=True), name="static")
