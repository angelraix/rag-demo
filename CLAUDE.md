# FilingsIQ — Project Context for Claude Code

## What this is
A RAG demo app for querying SEC filings across multiple customers. Built for a tech demo.
Three-page web UI, FastAPI backend, local Chroma vector store, streams from a single LLM call per query.

## Repo
https://github.com/angelraix/rag-demo

## File map
```
run.py              # One-command startup — loads .env, installs deps, ingests, opens browser
ingest.py           # Run once (or on new filings) to build the Chroma vector store
server.py           # FastAPI backend — single /ask endpoint
static/index.html   # Page 1: freeform query with streaming answer
static/models.html  # Page 2: same query sent to multiple models side by side
static/roles.html   # Page 3: CxO role cards that pre-fill and fire a query
requirements.txt
.env.example        # Copy to .env and fill in keys
```

## How to run
```bash
cp .env.example .env   # fill in OPENAI_API_KEY and ANTHROPIC_API_KEY
# drop filing .txt or .pdf files into ./filings/ named TICKER_FILINGTYPE_PERIOD.txt
python run.py          # installs deps, ingests if needed, starts server, opens browser
```

Flags: `--filings-dir ./path` `--port 8000` `--reset` (force re-ingest)

## Architecture
```
ingest.py (once)
  parse filing → detect SEC Item sections → chunk 400 words / 50 overlap
  → embed with text-embedding-3-small (OpenAI) → store in local Chroma DB

/ask endpoint (per query)
  embed query → Chroma cosine search top 5, filtered by customer_id
  → ~1,700 token prompt → ONE streaming call to model → stream to browser
```

## Key config in server.py
- `TOP_K = 5` — chunks retrieved per query
- `MAX_TOKENS = 1024` — max response length
- `EMBED_MODEL = "text-embedding-3-small"` — swap for voyage-finance-2 if preferred
- `SYSTEM_PROMPT` — base prompt sent on every query (tone, citation style, constraints)
- `SUPPORTED_MODELS` — dict of model_id → provider; add/remove models here

## Prompts (the ones users don't see)
Two layers:

1. **Base prompt** — `SYSTEM_PROMPT` in `server.py` (~line 45)
   Applied to every query. Controls citation format, tone, grounding rules.

2. **Role prompts** — `system` field on each role in `static/roles.html` (~line 120, in the `ROLES` array)
   Prepended to the base prompt when a CxO card is clicked. Controls role-specific focus areas.
   Roles: CFO, CEO, CRO, CLO, CISO — each has 5 pre-built prompt cards.

Effective system prompt for a CxO query = `role.system + "\n\n" + SYSTEM_PROMPT`

## Filing naming convention
`TICKER_FILINGTYPE_PERIOD.txt` — e.g. `ACME_10K_2024.txt`
First segment before `_` becomes the `customer_id` used to filter Chroma queries.
Supports `.txt` and `.pdf`.

## API keys needed
- `OPENAI_API_KEY` — embeddings (text-embedding-3-small)
- `ANTHROPIC_API_KEY` — Claude models
Both go in `.env`. OpenAI key also covers GPT-4o on the model comparison page.

## Supported models (server.py SUPPORTED_MODELS)
- claude-sonnet-5 (default)
- claude-haiku-4-5-20251001
- claude-opus-5
- gpt-4o
- gpt-4o-mini

## What's NOT done yet
- run.py / push to GitHub blocked by proxy — files were committed locally in /tmp/rag-demo
  but push failed; user needs to push from their machine
- GitHub MCP server not connected (would allow future Claude Code sessions to push directly)
  Setup: add @modelcontextprotocol/server-github to claude_desktop_config.json

## Token budget per query
| Component | ~Tokens |
|---|---|
| System prompt | 150 |
| 5 chunks × 400 words | 1,500 |
| Question | 50 |
| **Total input** | **~1,700** |
