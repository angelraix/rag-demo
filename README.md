# FilingsIQ — Stock Filings RAG Demo

A three-page demo app that lets you query SEC filings for multiple customers using
retrieval-augmented generation. One `/ask` API call per query — Chroma handles
retrieval locally so only ~5 relevant chunks reach the model.

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set API keys
```bash
export OPENAI_API_KEY=sk-...        # required (used for embeddings)
export ANTHROPIC_API_KEY=sk-ant-... # required (Claude models)
# GPT-4o on the compare page also uses OPENAI_API_KEY
```

### 3. Add your filings

Place `.txt` or `.pdf` filing files in a `filings/` directory.
Name them: `TICKER_FILINGTYPE_PERIOD.txt`

Examples:
```
filings/
  ACME_10K_2024.txt
  ACME_10Q_2024Q1.txt
  GLOBEX_10K_2024.txt
  INITECH_10K_2023.txt
```

The part before the first `_` becomes the `customer_id` used for filtering.

### 4. Ingest filings (run once)
```bash
python ingest.py --filings_dir ./filings
```

To re-ingest from scratch:
```bash
python ingest.py --filings_dir ./filings --reset
```

### 5. Start the server
```bash
uvicorn server:app --reload --port 8000
```

Open **http://localhost:8000**

---

## Pages

| URL | Page | What it does |
|-----|------|-------------|
| `/` | **Query** | Ask a freeform question, streaming answer |
| `/models.html` | **Model Compare** | Same query, 2–4 models side by side |
| `/roles.html` | **CxO Playbook** | Pre-built prompts by role (CFO, CEO, CRO, CLO, CISO) |

## How it works

```
ingest.py (once)
  parse filing → detect SEC sections → chunk (400 words, 50 overlap)
  → embed with text-embedding-3-small → store in local Chroma DB

/ask endpoint (per query)
  embed query → cosine similarity search in Chroma (top 5 chunks, filtered by customer_id)
  → build prompt with ~1,500 tokens of context
  → ONE streaming call to the chosen model
  → stream tokens back to browser
```

## Token budget per query

| Component | Tokens (approx) |
|-----------|----------------|
| System prompt | ~150 |
| 5 retrieved chunks × 400 words | ~1,500 |
| User question | ~50 |
| **Total input** | **~1,700** |

## Supported models

| Model | Provider |
|-------|----------|
| claude-sonnet-5 | Anthropic |
| claude-haiku-4-5-20251001 | Anthropic |
| claude-opus-5 | Anthropic |
| gpt-4o | OpenAI |
| gpt-4o-mini | OpenAI |
