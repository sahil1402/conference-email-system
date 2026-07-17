"""Stream the labeled real-ticket sample into a RUNNING app as demo traffic.

POSTs each ticket from data/eval_real/sample.jsonl to the app's own
/api/v1/ingest endpoint, so every email flows through the production path
(classify -> retrieve -> route -> draft -> persist -> SSE event) inside the
single server process — no second SQLite writer. Drafting calls the configured
provider, so expect ~10-30 s per email; the queue fills progressively.

Resumable: already-ingested tickets are tracked in
data/eval_real/.ingested_ids and skipped on rerun.

Run with the backend up:
    cd backend && python scripts/ingest_sample_traffic.py [--limit N] [--workers 2]
"""

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

_ROOT_DIR = Path(__file__).resolve().parents[2]
SAMPLE_PATH = _ROOT_DIR / "data" / "eval_real" / "sample.jsonl"
STATE_PATH = _ROOT_DIR / "data" / "eval_real" / ".ingested_ids"
API = "http://localhost:8000/api/v1"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    rows = [json.loads(l) for l in open(SAMPLE_PATH, encoding="utf-8")]
    done: set[str] = set()
    if STATE_PATH.exists():
        done = set(STATE_PATH.read_text().split())
    todo = [r for r in rows if str(r["ticket_id"]) not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"ingesting {len(todo)} tickets ({len(done)} already done)")

    lock = threading.Lock()
    counts = {"ok": 0, "fail": 0}

    def one(row: dict) -> None:
        payload = {
            "from_email": f"ticket-{row['ticket_id']}@sample.aaai.local",
            "to_email": "workflowchairs@aaai.zendesk.com",
            "subject": row["subject"][:990],
            "body": row["question"],
            "timestamp": f"{row['month']}-01T00:00:00Z",
        }
        try:
            resp = httpx.post(f"{API}/emails/ingest", json=payload, timeout=180)
            resp.raise_for_status()
            with lock:
                counts["ok"] += 1
                with open(STATE_PATH, "a") as fh:
                    fh.write(f"{row['ticket_id']}\n")
        except Exception as exc:  # noqa: BLE001 - keep streaming on failure
            with lock:
                counts["fail"] += 1
            print(f"  ticket {row['ticket_id']}: {type(exc).__name__}: {exc}", flush=True)

    start = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, _ in enumerate(pool.map(one, todo), 1):
            if i % 10 == 0:
                print(f"  {i}/{len(todo)} ({time.time()-start:.0f}s)", flush=True)
    print(f"done: {counts['ok']} ok, {counts['fail']} failed")


if __name__ == "__main__":
    sys.exit(main())
