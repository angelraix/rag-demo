#!/usr/bin/env python3
"""
run.py — One command to start the FilingsIQ demo.

Usage:
    python run.py [--filings-dir ./filings] [--port 8000] [--reset]

What it does:
  1. Loads .env for API keys
  2. Checks that required keys are present
  3. Installs missing dependencies from requirements.txt
  4. Runs ingest.py if chroma_db/ is missing or filings have changed
  5. Starts uvicorn and opens the browser
"""

import os
import sys
import subprocess
import argparse
import hashlib
import json
import webbrowser
import time
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).parent
ENV_FILE    = ROOT / ".env"
HASH_FILE   = ROOT / "chroma_db" / ".ingest_hash"
CHROMA_DIR  = ROOT / "chroma_db"

# ── Load .env ──────────────────────────────────────────────────────────────
def load_env():
    if not ENV_FILE.exists():
        print("⚠️  No .env file found.")
        print("   Copy .env.example → .env and fill in your API keys.\n")
        sys.exit(1)
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

# ── Check required keys ────────────────────────────────────────────────────
def check_keys():
    missing = [k for k in ("ANTHROPIC_API_KEY",) if not os.environ.get(k)]
    if missing:
        print(f"❌  Missing API keys in .env: {', '.join(missing)}")
        print("   Add them to your .env file and re-run.\n")
        sys.exit(1)
    print("✅  API keys loaded.")

# ── Install dependencies ───────────────────────────────────────────────────
def install_deps():
    req = ROOT / "requirements.txt"
    if not req.exists():
        return
    print("📦  Checking dependencies…")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req), "-q", "--break-system-packages"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        # Try without --break-system-packages (not needed on all systems)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req), "-q"],
            capture_output=True, text=True
        )
    if result.returncode != 0:
        print("❌  Dependency install failed:\n", result.stderr)
        sys.exit(1)
    print("✅  Dependencies ready.")

# ── Fingerprint filings dir ────────────────────────────────────────────────
def filings_hash(filings_dir: Path) -> str:
    h = hashlib.md5()
    for f in sorted(filings_dir.glob("*")):
        if f.suffix in (".txt", ".pdf"):
            h.update(f.name.encode())
            h.update(str(f.stat().st_mtime).encode())
    return h.hexdigest()

# ── Ingest if needed ───────────────────────────────────────────────────────
def maybe_ingest(filings_dir: Path, reset: bool):
    if not filings_dir.exists() or not any(filings_dir.glob("*.txt")) and not any(filings_dir.glob("*.pdf")):
        print(f"⚠️  No filings found in {filings_dir}/")
        print("   Add .txt or .pdf filing files named TICKER_FILINGTYPE_PERIOD.txt")
        print("   e.g.  ACME_10K_2024.txt")
        print("   Then re-run this script.\n")
        sys.exit(1)

    current_hash = filings_hash(filings_dir)

    # Check if already ingested and unchanged
    if not reset and CHROMA_DIR.exists() and HASH_FILE.exists():
        stored = HASH_FILE.read_text().strip()
        if stored == current_hash:
            print("✅  Vector store is up to date — skipping ingest.")
            return

    print("🔄  Ingesting filings into Chroma…")
    args = [sys.executable, str(ROOT / "ingest.py"), "--filings_dir", str(filings_dir)]
    if reset:
        args.append("--reset")

    result = subprocess.run(args, text=True)
    if result.returncode != 0:
        print("❌  Ingest failed.")
        sys.exit(1)

    # Save hash so we skip next time if nothing changed
    CHROMA_DIR.mkdir(exist_ok=True)
    HASH_FILE.write_text(current_hash)
    print("✅  Ingest complete.")

# ── Start server ───────────────────────────────────────────────────────────
def start_server(port: int):
    url = f"http://localhost:{port}"
    print(f"\n🚀  Starting FilingsIQ on {url}")
    print("   Press Ctrl+C to stop.\n")

    # Open browser after a short delay
    def open_browser():
        time.sleep(1.8)
        webbrowser.open(url)

    import threading
    threading.Thread(target=open_browser, daemon=True).start()

    subprocess.run([
        sys.executable, "-m", "uvicorn", "server:app",
        "--port", str(port),
        "--reload",
    ], cwd=str(ROOT))

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Start the FilingsIQ RAG demo.")
    parser.add_argument("--filings-dir", default="./filings",
                        help="Directory of .txt/.pdf filing files (default: ./filings)")
    parser.add_argument("--port", type=int, default=8000,
                        help="Port to serve on (default: 8000)")
    parser.add_argument("--reset", action="store_true",
                        help="Force re-ingest even if filings haven't changed")
    args = parser.parse_args()

    filings_dir = Path(args.filings_dir)
    if not filings_dir.is_absolute():
        filings_dir = ROOT / filings_dir

    print("\n⬡  FilingsIQ — RAG Demo\n" + "─" * 32)
    load_env()
    check_keys()
    install_deps()
    maybe_ingest(filings_dir, args.reset)
    start_server(args.port)

if __name__ == "__main__":
    main()
