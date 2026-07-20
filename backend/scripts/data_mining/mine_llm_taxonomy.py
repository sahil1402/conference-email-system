"""Phase 2 — blind LLM taxonomy induction from the over-clusters.

Reads data/mining/all_clusters.json (Phase 1b) + all_inbound.jsonl. For each
over-cluster: PII-scrubs ~12 representative questions, and asks the LLM to name the
single topic — **without ever seeing the existing 11 intents** (blind induction, per
the design decision). Then a merge pass consolidates the ~50 raw cluster names into
a clean 2-level taxonomy (family → intent). Finally validates each induced intent
against the chair-applied Zendesk tags (weak ground truth) via the clusters' tag
mixes.

~k+1 LLM calls (not per-ticket). Egress: only the scrubbed representative examples.
Per-cluster naming is cached (resumable).

Outputs (gitignored):
  data/mining/cluster_names.jsonl        per raw cluster: {cluster, label, definition}
  data/mining/induced_taxonomy.json      merged taxonomy + tag validation
  console summary (the analysis)

Usage (background, thread caps + local provider):
    cd backend && MODEL_PROVIDER=local OMP_NUM_THREADS=2 HF_HUB_OFFLINE=1 \
      python scripts/mine_llm_taxonomy.py
"""
import asyncio
import json
import re
import sys
from collections import Counter
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.config import settings  # noqa: E402
from app.pipeline.openai_compat import post_chat  # noqa: E402

import os

MINING = REPO_ROOT / "data" / "mining"
# Paths are env-overridable so a refined (e.g. inbound-only) run can reuse this
# script without clobbering the first run's outputs.
CLUSTERS = MINING / os.environ.get("MINE_CLUSTERS", "all_clusters.json")
INBOUND = MINING / os.environ.get("MINE_INBOUND", "all_inbound.jsonl")
NAMES_CACHE = MINING / os.environ.get("MINE_NAMES_CACHE", "cluster_names.jsonl")
OUT_PATH = MINING / os.environ.get("MINE_OUT", "induced_taxonomy.json")

CONCURRENCY = 4
REP_CHARS = 300

# --- PII scrub (best-effort; examples also come from signature-stripped bodies) ---
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_URL = re.compile(r"https?://\S+")
_NUM = re.compile(r"\b\d{4,}\b")  # paper ids, phone, long digit runs


def scrub(text: str) -> str:
    text = _EMAIL.sub("[EMAIL]", text)
    text = _URL.sub("[URL]", text)
    text = _NUM.sub("[NUM]", text)
    return " ".join(text.split())[:REP_CHARS]


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


async def _chat(client, system: str, user: str, max_tokens: int):
    base = settings.LOCAL_MODEL_BASE_URL.rstrip("/")
    payload = {
        "model": settings.LOCAL_MODEL_NAME,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": settings.DRAFTER_TEMPERATURE,
        "seed": settings.DRAFTER_SEED,
        "stream": False,
    }
    headers = ({"Authorization": f"Bearer {settings.LOCAL_MODEL_API_KEY}"}
               if settings.LOCAL_MODEL_API_KEY else None)
    for attempt in range(2):
        try:
            r = await post_chat(client, f"{base}/chat/completions", payload, headers)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            if attempt == 1:
                print(f"  chat failed: {type(e).__name__} {str(e)[:120]}")
                return None
            await asyncio.sleep(2)


_NAME_SYS = (
    "You are building a taxonomy of the topics people email a computer-science "
    "conference help-desk about. You are shown representative example messages from "
    "ONE cluster plus its frequent keywords. Identify the single dominant topic. "
    "Output ONLY a JSON object: {\"label\": \"<snake_case_intent>\", \"definition\": "
    "\"<one sentence on what the sender wants>\", \"coherent\": <true|false>}. "
    "Set coherent=false if the examples are a mix of unrelated topics or spam. "
    "Invent the label from the content — do not use any preset list."
)


async def name_cluster(client, sem, c, reps):
    async with sem:
        user = ("Frequent keywords: " + ", ".join(c["top_terms"]) + "\n\nExamples:\n" +
                "\n".join(f"{i+1}. {scrub(r)}" for i, r in enumerate(reps)))
        out = await _chat(client, _NAME_SYS, user, max_tokens=1200)
        obj = _json_from(out or "") or {}
        return {"cluster": c["cluster"], "size": c["size"],
                "label": obj.get("label", "unnamed"),
                "definition": obj.get("definition", ""),
                "coherent": obj.get("coherent", True),
                "top_terms": c["top_terms"], "top_tags": c["top_tags"]}


_MERGE_SYS = (
    "You are consolidating an over-clustered set of conference help-desk email topics "
    "into a clean taxonomy. Merge near-duplicate topics, drop incoherent/spam ones, "
    "and group the rest into a two-level taxonomy of families -> intents. Output ONLY "
    "JSON: {\"taxonomy\": [{\"family\": \"<name>\", \"intent\": \"<snake_case>\", "
    "\"definition\": \"<one sentence>\", \"cluster_ids\": [<int>...]}]}. Every input "
    "cluster id must appear in exactly one intent unless it is incoherent/spam."
)


async def main() -> None:
    clusters = json.loads(CLUSTERS.read_text())["clusters"]
    rows = [json.loads(l) for l in open(INBOUND, encoding="utf-8")]
    print(f"{len(clusters)} raw clusters over {len(rows)} questions")

    cached = {}
    if NAMES_CACHE.exists():
        for l in open(NAMES_CACHE, encoding="utf-8"):
            o = json.loads(l)
            cached[o["cluster"]] = o
    todo = [c for c in clusters if c["cluster"] not in cached]
    print(f"naming {len(todo)} clusters ({len(cached)} cached)")

    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=120) as client:
        if todo:
            reps_of = {c["cluster"]: [rows[i]["question"] for i in c["rep_indices"]] for c in todo}
            named = await asyncio.gather(*(name_cluster(client, sem, c, reps_of[c["cluster"]]) for c in todo))
            for o in named:
                cached[o["cluster"]] = o
            with open(NAMES_CACHE, "w", encoding="utf-8") as f:
                for o in sorted(cached.values(), key=lambda x: -x["size"]):
                    f.write(json.dumps(o) + "\n")

        named = sorted(cached.values(), key=lambda x: -x["size"])
        print("\n=== raw cluster labels (by size) ===")
        for o in named:
            flag = "" if o["coherent"] else "  [INCOHERENT]"
            print(f"  {o['size']:>5}  {o['label']:<34} {', '.join(o['top_terms'][:5])}{flag}")

        # --- merge pass ---
        merge_user = "Clusters:\n" + "\n".join(
            f"id={o['cluster']} size={o['size']} label={o['label']} :: {o['definition']}"
            for o in named)
        merged_raw = await _chat(client, _MERGE_SYS, merge_user, max_tokens=6000)

    merged = _json_from(merged_raw or "") or {}
    taxonomy = merged.get("taxonomy", []) if isinstance(merged, dict) else []

    # --- validate each induced intent against chair Zendesk tags ---
    tags_by_cluster = {o["cluster"]: dict(o["top_tags"]) for o in named}
    size_by_cluster = {o["cluster"]: o["size"] for o in named}
    for t in taxonomy:
        agg_tags = Counter()
        vol = 0
        for cid in t.get("cluster_ids", []):
            vol += size_by_cluster.get(cid, 0)
            for tag, n in tags_by_cluster.get(cid, {}).items():
                agg_tags[tag] += n
        t["volume"] = vol
        t["top_chair_tags"] = agg_tags.most_common(4)
    taxonomy.sort(key=lambda t: -t.get("volume", 0))

    OUT_PATH.write_text(json.dumps({"taxonomy": taxonomy, "n_raw_clusters": len(named)}, indent=2))

    print("\n================ INDUCED TAXONOMY (blind; by volume) ================")
    fam = None
    for t in taxonomy:
        if t.get("family") != fam:
            fam = t.get("family")
            print(f"\n[{fam}]")
        tags = ", ".join(f"{k}({v})" for k, v in t.get("top_chair_tags", []))
        print(f"  {t.get('volume',0):>5}  {t.get('intent','?'):<32} {t.get('definition','')}")
        if tags:
            print(f"          chair-tags: {tags}")
    print(f"\n-> {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
