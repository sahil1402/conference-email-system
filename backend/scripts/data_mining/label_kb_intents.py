"""Task B3 — LLM-label the 93 KB chunks with answerable intents.

For each chunk in data/knowledge_base/policies.json, asks the LLM (via the
OpenAI-compatible seam, app/pipeline/openai_compat.post_chat) which of the 14
canonical intents (app.pipeline.taxonomy) that chunk's text could actually be
used to answer. One LLM call per chunk; concurrency capped; per-chunk cache
makes the script resumable without re-calling the LLM for chunks already
labeled.

Outputs:
  data/knowledge_base/policies.json (in place)  chunk["intents"] = [...]
  data/mining/kb_intent_label_cache.jsonl        resumable per-chunk cache (raw)
  data/mining/kb_intent_labels.jsonl             final per-chunk {id, intents}
  data/mining/kb_intent_coverage.json            {intent: [chunk_ids...]} (working copy)
  backend/reports/kb_intent_coverage.json        same coverage map (tracked, PII-free)

Chunks are public AAAI policy text (no PII), but the script still runs through
the same configured LLM seam as the rest of the mining pipeline.

Usage:
    cd backend && python scripts/data_mining/label_kb_intents.py
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.config import settings  # noqa: E402
from app.pipeline.openai_compat import post_chat  # noqa: E402
from app.pipeline.taxonomy import INTENT_DEFS, VALID_INTENTS  # noqa: E402

POLICIES_PATH = REPO_ROOT / "data" / "knowledge_base" / "policies.json"
MINING = REPO_ROOT / "data" / "mining"
CACHE_PATH = MINING / "kb_intent_label_cache.jsonl"
LABELS_PATH = MINING / "kb_intent_labels.jsonl"
COVERAGE_MINING_PATH = MINING / "kb_intent_coverage.json"
COVERAGE_REPORT_PATH = REPO_ROOT / "backend" / "reports" / "kb_intent_coverage.json"

CONCURRENCY = 4
MAX_TOKENS = 1200

_SYS_PROMPT = (
    "You are labeling chunks of a computer-science conference's (AAAI) PUBLIC "
    "submission-policy knowledge base. Inbound help-desk emails are each "
    "classified into one of 14 fixed \"intents\" (categories of question). You "
    "will be shown ONE policy chunk (title + content) and the list of 14 "
    "intents with their definitions. Decide which intents this chunk's text "
    "could actually be used to ANSWER -- i.e., if an email of that intent "
    "arrived, could an agent quote or closely paraphrase this chunk as an "
    "on-topic, satisfying answer? Most chunks will match zero, one, or a few "
    "intents -- do not force a match just because a topic is loosely related. "
    "Respond with ONLY a JSON object: {\"intents\": [\"<intent_name>\", ...]}, "
    "using EXACTLY the intent names given verbatim, or an empty list if none "
    "apply. No other text."
)


def _json_from(text: str):
    """Extract the first JSON object/array from a model response."""
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    return None


def _build_user_prompt(chunk: dict) -> str:
    intent_lines = "\n".join(f"- {name}: {defn}" for name, defn in INTENT_DEFS.items())
    return (
        f"Policy chunk title: {chunk['title']}\n\n"
        f"Policy chunk content:\n{chunk['content']}\n\n"
        f"14 intents (name: definition):\n{intent_lines}\n\n"
        "Which of these intents could this chunk answer? Respond with ONLY the "
        "JSON object described in the system prompt."
    )


async def _label_chunk(client: httpx.AsyncClient, sem: asyncio.Semaphore, chunk: dict) -> dict:
    base = settings.LOCAL_MODEL_BASE_URL.rstrip("/")
    payload = {
        "model": settings.LOCAL_MODEL_NAME,
        "messages": [
            {"role": "system", "content": _SYS_PROMPT},
            {"role": "user", "content": _build_user_prompt(chunk)},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": settings.DRAFTER_TEMPERATURE,
        "seed": settings.DRAFTER_SEED,
        "stream": False,
    }
    headers = ({"Authorization": f"Bearer {settings.LOCAL_MODEL_API_KEY}"}
               if settings.LOCAL_MODEL_API_KEY else None)
    async with sem:
        for attempt in range(2):
            try:
                r = await post_chat(client, f"{base}/chat/completions", payload, headers)
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                return {"id": chunk["id"], "raw": content, "error": None}
            except Exception as e:  # noqa: BLE001
                if attempt == 1:
                    return {
                        "id": chunk["id"],
                        "raw": None,
                        "error": f"{type(e).__name__}: {str(e)[:200]}",
                    }
                await asyncio.sleep(2)


def _parse_intents(raw: str | None, chunk_id: str) -> list[str]:
    if not raw:
        return []
    obj = _json_from(raw) or {}
    raw_intents = obj.get("intents", []) if isinstance(obj, dict) else []
    valid: list[str] = []
    for name in raw_intents:
        if name in VALID_INTENTS:
            if name not in valid:
                valid.append(name)
        else:
            print(f"  [{chunk_id}] dropping unknown intent from LLM output: {name!r}")
    return valid


async def main() -> None:
    MINING.mkdir(parents=True, exist_ok=True)
    COVERAGE_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    chunks = json.loads(POLICIES_PATH.read_text(encoding="utf-8"))
    print(f"{len(chunks)} KB chunks loaded from {POLICIES_PATH}")

    cached: dict[str, dict] = {}
    if CACHE_PATH.exists():
        for line in open(CACHE_PATH, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            cached[o["id"]] = o

    todo = [c for c in chunks if c["id"] not in cached]
    print(f"labeling {len(todo)} chunks via the LLM ({len(cached)} already cached)")

    if todo:
        sem = asyncio.Semaphore(CONCURRENCY)
        async with httpx.AsyncClient(timeout=120) as client:
            results = await asyncio.gather(*(_label_chunk(client, sem, c) for c in todo))

        failed = [r for r in results if r["error"]]
        if failed:
            for r in failed:
                print(f"  FAILED [{r['id']}]: {r['error']}")
            raise SystemExit(
                f"BLOCKED: {len(failed)}/{len(todo)} chunk(s) failed to label via the "
                "LLM endpoint. Not fabricating labels -- fix the endpoint/config and "
                "re-run (already-succeeded chunks are cached and will be skipped)."
            )

        # Append newly labeled chunks to the resumable cache.
        with open(CACHE_PATH, "a", encoding="utf-8") as f:
            for r in results:
                cached[r["id"]] = r
                f.write(json.dumps(r) + "\n")

    # Validate + attach per-chunk intents, in file order.
    for chunk in chunks:
        entry = cached.get(chunk["id"])
        raw = entry["raw"] if entry else None
        chunk["intents"] = _parse_intents(raw, chunk["id"])

    # Assert every chunk got a (possibly empty) intents list before writing anything.
    missing = [c["id"] for c in chunks if not isinstance(c.get("intents"), list)]
    assert not missing, f"chunks missing a labeled intents list: {missing}"

    # Write policies.json back in place (same dumps convention as the source file:
    # indent=2, ensure_ascii=False, no trailing newline — verified round-trip-identical
    # before this run touched it).
    POLICIES_PATH.write_text(
        json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"updated {POLICIES_PATH} ({len(chunks)} chunks, each with an intents list)")

    with open(LABELS_PATH, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps({"id": chunk["id"], "intents": chunk["intents"]}) + "\n")
    print(f"wrote {LABELS_PATH}")

    coverage: dict[str, list[str]] = {name: [] for name in VALID_INTENTS}
    for chunk in chunks:
        for name in chunk["intents"]:
            coverage[name].append(chunk["id"])

    coverage_json = json.dumps(coverage, indent=2)
    COVERAGE_MINING_PATH.write_text(coverage_json, encoding="utf-8")
    COVERAGE_REPORT_PATH.write_text(coverage_json, encoding="utf-8")
    print(f"wrote {COVERAGE_MINING_PATH}")
    print(f"wrote {COVERAGE_REPORT_PATH}")

    print("\n=== intent -> #chunks coverage ===")
    for name in VALID_INTENTS:
        print(f"  {name:<28} {len(coverage[name])}")


if __name__ == "__main__":
    asyncio.run(main())
