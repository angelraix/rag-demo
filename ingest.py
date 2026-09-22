"""
ingest.py — Run once before the demo to build the Chroma vector store.

Usage:
    python ingest.py --filings_dir ./filings

Expects filings_dir to contain text or PDF files named like:
    ACME_10K_2024.txt
    ACME_10Q_2024Q1.txt
    GLOBEX_10K_2024.txt
    ...

The first part of the filename (before the first underscore) is used as the
customer_id, so keep filenames consistent.
"""

import os
import re
import argparse
import chromadb
from chromadb.utils import embedding_functions

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CHROMA_PATH = "./chroma_db"
COLLECTION  = "filings"
CHUNK_SIZE  = 400   # tokens ≈ words for rough sizing
CHUNK_OVERLAP = 50

EMBED_MODEL = "text-embedding-3-small"  # OpenAI — swap for voyage-finance-2 if preferred

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_metadata_from_filename(filename: str) -> dict:
    """
    Derive customer_id, ticker, filing_type, period from filename.
    Expected pattern: TICKER_FILINGTYPE_PERIOD.txt  e.g. ACME_10K_2024.txt
    Falls back gracefully if the name doesn't match.
    """
    stem = os.path.splitext(filename)[0]
    parts = stem.split("_")
    return {
        "customer_id": parts[0].lower() if len(parts) > 0 else stem.lower(),
        "ticker":      parts[0].upper() if len(parts) > 0 else stem.upper(),
        "filing_type": parts[1].upper() if len(parts) > 1 else "FILING",
        "period":      parts[2]         if len(parts) > 2 else "UNKNOWN",
    }


def read_file(path: str) -> str:
    """Read .txt or .pdf files."""
    if path.endswith(".pdf"):
        try:
            import pypdf
            reader = pypdf.PdfReader(path)
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            raise SystemExit("pypdf not installed. Run: pip install pypdf")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def detect_sections(text: str) -> list[tuple[str, str]]:
    """
    Try to split on SEC filing section headers (Item 1, Item 1A, etc.).
    Returns list of (section_name, section_text).
    Falls back to a single section if no headers found.
    """
    pattern = re.compile(
        r"(Item\s+\d+[A-Z]?\.?\s+[A-Z][^\n]{0,80})",
        re.IGNORECASE
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return [("Full Document", text)]

    sections = []
    for i, m in enumerate(matches):
        start = m.start()
        end   = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((m.group(1).strip(), text[start:end]))
    return sections


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping word-based chunks."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i : i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
        i += chunk_size - overlap
    return chunks


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--filings_dir", default="./filings",
                        help="Directory containing filing .txt or .pdf files")
    parser.add_argument("--reset", action="store_true",
                        help="Delete and recreate the collection")
    args = parser.parse_args()

    if not os.path.isdir(args.filings_dir):
        raise SystemExit(f"Filings directory not found: {args.filings_dir}")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY not set in environment.")

    # Set up Chroma
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    ef = embedding_functions.OpenAIEmbeddingFunction(
        api_key=api_key,
        model_name=EMBED_MODEL,
    )

    if args.reset:
        try:
            client.delete_collection(COLLECTION)
            print(f"Deleted existing collection '{COLLECTION}'.")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    # Ingest files
    files = [
        f for f in os.listdir(args.filings_dir)
        if f.endswith(".txt") or f.endswith(".pdf")
    ]
    if not files:
        raise SystemExit(f"No .txt or .pdf files found in {args.filings_dir}")

    total_chunks = 0
    for filename in sorted(files):
        path = os.path.join(args.filings_dir, filename)
        meta_base = parse_metadata_from_filename(filename)
        print(f"\n📄 {filename}  →  customer={meta_base['customer_id']}  filing={meta_base['filing_type']}  period={meta_base['period']}")

        text = read_file(path)
        sections = detect_sections(text)
        print(f"   {len(sections)} section(s) detected")

        doc_ids, doc_texts, doc_metas = [], [], []
        for sec_name, sec_text in sections:
            for i, chunk in enumerate(chunk_text(sec_text)):
                chunk_id = f"{meta_base['customer_id']}-{meta_base['filing_type']}-{meta_base['period']}-{sec_name[:20]}-{i}"
                # Sanitise id
                chunk_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", chunk_id)[:128]
                doc_ids.append(chunk_id)
                doc_texts.append(chunk)
                doc_metas.append({**meta_base, "section": sec_name[:100]})

        # Upsert in batches of 100
        batch = 100
        for start in range(0, len(doc_ids), batch):
            collection.upsert(
                ids=doc_ids[start:start+batch],
                documents=doc_texts[start:start+batch],
                metadatas=doc_metas[start:start+batch],
            )
        total_chunks += len(doc_ids)
        print(f"   ✅ {len(doc_ids)} chunks ingested")

    print(f"\n✅ Done. {total_chunks} total chunks in '{CHROMA_PATH}'.")
    print(f"   Customers indexed: {set(f.split('_')[0].lower() for f in files)}")


if __name__ == "__main__":
    main()
