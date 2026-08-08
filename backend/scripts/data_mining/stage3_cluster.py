"""Stage 3 — discover recurring workflow patterns inside each intent.

Joins the Stage 1 extractions to the Stage 2 intent tags, then clusters each
intent's ``steps_taken`` by embedding similarity so that tickets worked the same
way land together. One script for both the small test batch and the full run —
pass ``--intent`` to restrict, omit it to run all 14 (same pattern as
stage1_extract.py / stage2_tag_intent.py).

REUSED INFRA (no new seam, no new dependency):
  - embeddings: the SAME local CPU SentenceTransformer the FAISS retriever uses
    (``settings.FAISS_MODEL_NAME``, L2-normalized float32 — mirroring
    ``faiss_retriever._encode``). Runs entirely on this machine: no API call, no
    credential, no mining spend. Only optional cluster LABELLING calls a model,
    via the isolated mining key.
  - clustering: ``sklearn.cluster`` (already a project dependency), so cluster
    counts are DISCOVERED rather than assumed.

``is_fallback`` tickets are excluded: they are taxonomy gaps parked on an intent,
not real workflow instances of it, and would pollute that intent's clusters.

Three refinements, validated on the review_submission_help / paper_bidding
size extremes:

  1. SIZE-TIERED clustering. One global min_cluster_size does not fit an intent
     range spanning 11 to 1,367 tickets:
       n >= 200      -> HDBSCAN, mcs=15 (validated)
       30 <= n < 200 -> HDBSCAN, mcs swept proportional to n, most STABLE result
                        chosen (the cluster count that persists across the widest
                        run of swept values — a plateau, not a lucky value)
       n < 30        -> agglomerative fallback: HDBSCAN cannot estimate density
                        at small n and returns 100% noise even when the tickets
                        are plainly similar (measured on paper_bidding).

  2. NOISE RECOVERY. HDBSCAN's noise bucket is not all one-offs — it held a
     coherent "assignments not visible yet" group sitting just under the density
     threshold. Noise points are re-clustered at half the primary mcs (floor 5);
     anything found is kept and MARKED ``recovered_from_noise``.

  3. CANDIDATE-MERGE FLAGGING, never auto-merge. Cluster centroids above 0.85
     cosine are flagged for human review. Two near-duplicate clusters may be one
     procedure or a real distinction the embedding blurs; that is a judgement
     call, so the script surfaces it and stops.

Usage:
    python scripts/data_mining/stage3_cluster.py                       # all 14
    python scripts/data_mining/stage3_cluster.py --intent paper_bidding \
        --out ../data/mining/stage3_test/clusters.json --no-label      # one, no spend
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

import httpx
import numpy as np

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.config import settings  # noqa: E402
from app.pipeline.openai_compat import post_chat  # noqa: E402

from stage1_extract import _extract_json, load_mining_api_key  # noqa: E402

_STAGE1 = _ROOT / "data" / "mining" / "stage1_full" / "results.json"
_STAGE2 = _ROOT / "data" / "mining" / "stage2_full" / "intent_tags.json"
_OUT = _ROOT / "data" / "mining" / "stage3_full" / "clusters.json"

# Size tiers.
LARGE_N = 200
SMALL_N = 30
LARGE_MCS = 15
# Cosine distance ceiling for the small-n fallback (0.35 => keep pairs >= ~0.65 similar).
LINK_THRESHOLD = 0.35
RECOVERY_MIN_MCS = 5
MERGE_SIMILARITY = 0.85

_TIMEOUT = 120.0


# --- data --------------------------------------------------------------------
def load_joined(stage1: Path, stage2: Path) -> dict[str, list[dict]]:
    """Join on ticket_id, drop is_fallback, bucket by intent."""
    ext = {
        r["ticket_id"]: r
        for r in json.loads(stage1.read_text(encoding="utf-8"))
        if r.get("category") != "merge_closure" and "error" not in r
    }
    by_intent: dict[str, list[dict]] = {}
    dropped_fallback = 0
    unmatched = 0
    for tag in json.loads(stage2.read_text(encoding="utf-8")):
        if "error" in tag:
            continue
        if tag.get("is_fallback"):
            dropped_fallback += 1
            continue
        src = ext.get(tag["ticket_id"])
        if src is None:
            unmatched += 1
            continue
        steps = src.get("steps_taken") or []
        if not steps:
            continue
        by_intent.setdefault(tag["intent"], []).append(
            {
                "ticket_id": tag["ticket_id"],
                "subject": src.get("subject"),
                "steps_taken": steps,
                "what_was_asked": src.get("what_was_asked"),
                "outcome_type": src.get("outcome_type"),
            }
        )
    print(f"joined: {sum(len(v) for v in by_intent.values())} tickets across {len(by_intent)} intents")
    print(f"  excluded is_fallback : {dropped_fallback}")
    print(f"  unmatched ticket_ids : {unmatched}")
    return by_intent


def embed(texts: list[str]) -> np.ndarray:
    """Encode with the retriever's model, L2-normalized (mirrors faiss_retriever._encode)."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(settings.FAISS_MODEL_NAME, device="cpu")
    vecs = np.asarray(model.encode(texts, show_progress_bar=False), dtype="float32")
    if vecs.ndim == 1:
        vecs = vecs.reshape(1, -1)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.clip(norms, 1e-12, None)


# --- clustering primitives ---------------------------------------------------
def _hdbscan(vectors: np.ndarray, mcs: int, min_samples: int = 1) -> np.ndarray:
    """HDBSCAN on unit vectors. Euclidean on L2-normalized vectors is a monotone
    function of cosine distance, so this is cosine clustering by proxy while
    avoiding the slower precomputed-metric path. Its -1 label marks one-offs."""
    from sklearn.cluster import HDBSCAN

    n = len(vectors)
    return HDBSCAN(
        min_cluster_size=max(2, min(mcs, n)),
        min_samples=max(1, min(min_samples, n - 1)) if n > 1 else 1,
        metric="euclidean",
        copy=True,
    ).fit_predict(vectors)


def _agglomerative(vectors: np.ndarray, threshold: float) -> np.ndarray:
    """Average-linkage with a cosine DISTANCE THRESHOLD — discovers the cluster
    count (n_clusters=None) without needing a density estimate. Trade-off: every
    point is assigned, so there is no noise bucket and singletons appear as
    1-member clusters (read those as unclustered one-offs)."""
    from sklearn.cluster import AgglomerativeClustering

    if len(vectors) < 2:
        return np.zeros(len(vectors), dtype=int)
    return AgglomerativeClustering(
        n_clusters=None, distance_threshold=threshold, metric="cosine", linkage="average"
    ).fit_predict(vectors)


# A candidate within this fraction of the best (lowest) noise count is treated as
# tied on coverage, so stability decides between them.
NOISE_TOLERANCE = 0.05


def _plateau_lengths(trials: list[dict]) -> dict[int, int]:
    """Map mcs -> length of the contiguous run of identical cluster count it sits in."""
    lengths: dict[int, int] = {}
    run: list[dict] = []
    for t in trials:
        if run and t["n_clusters"] == run[-1]["n_clusters"]:
            run.append(t)
        else:
            run = [t]
        for r in run:
            lengths[r["mcs"]] = len(run)
    return lengths


def select_trial(trials: list[dict]) -> dict | None:
    """Pick the best swept candidate. COVERAGE FIRST, stability as the tie-break.

    CORRECTED RULE (supersedes 'longest plateau wins'):
      1. Discard candidates that found no clusters at all.
      2. Take the lowest noise count; treat anything within NOISE_TOLERANCE of it
         as tied on coverage.
      3. Among those tied candidates, prefer the one sitting in the LONGEST
         plateau (a cluster count that survives several mcs values is structure,
         not an artefact of one setting).
      4. Break any remaining tie toward fewer clusters, then larger mcs — the
         simpler, more conservative structure.

    WHY THIS CHANGED: the original rule maximised plateau length and ignored
    coverage entirely, so a high-mcs result that dumped most tickets into noise
    could beat a lower-mcs result that explained far more of the intent. Measured
    on reviewer_assignment (n=597): it chose mcs=48 (2 clusters, 369 noise / 62%)
    over mcs=18 (4 clusters, 321 noise / 54%) purely because {30,48} agreed on
    "2 clusters". Noise is now the primary criterion; stability only separates
    candidates that already explain the same share of the data.

    REMAINING LIMITATION (unchanged): candidates are proportional to n, so the
    floor for a large intent can exclude small absolute values — 0.03*597 = 18
    means mcs=15 is never even tried there. This is why the >=200 tier uses a
    fixed mcs=15 rather than sweeping.
    """
    usable = [t for t in trials if t["n_clusters"] > 0]
    if not usable:
        return None
    lengths = _plateau_lengths(trials)
    best_noise = min(t["n_noise"] for t in usable)
    ceiling = best_noise + max(1, round(best_noise * NOISE_TOLERANCE))
    tied = [t for t in usable if t["n_noise"] <= ceiling]
    return sorted(
        tied,
        key=lambda t: (-lengths.get(t["mcs"], 1), t["n_clusters"], -t["mcs"]),
    )[0]


def _sweep_stable(vectors: np.ndarray, n: int) -> tuple[np.ndarray, int, list[dict]]:
    """Sweep mcs proportional to n and choose via ``select_trial`` (coverage first)."""
    candidates = sorted({max(3, int(round(n * f))) for f in (0.03, 0.05, 0.08, 0.12, 0.18, 0.25)})
    trials: list[dict] = []
    for mcs in candidates:
        labels = _hdbscan(vectors, mcs)
        counts = Counter(labels)
        n_noise = counts.pop(-1, 0)
        trials.append(
            {"mcs": mcs, "n_clusters": len(counts), "n_noise": int(n_noise), "labels": labels}
        )

    public = [{k: v for k, v in t.items() if k != "labels"} for t in trials]
    chosen = select_trial(trials)
    if chosen is None:
        return _agglomerative(vectors, LINK_THRESHOLD), -1, public
    return chosen["labels"], chosen["mcs"], public


# --- per-intent pipeline -----------------------------------------------------
def cluster_intent(rows: list[dict], vectors: np.ndarray) -> dict:
    """Tiered clustering + noise recovery + merge flagging for one intent."""
    n = len(rows)
    sweep: list[dict] = []

    if n >= LARGE_N:
        method, mcs = "hdbscan", LARGE_MCS
        labels = _hdbscan(vectors, mcs)
    elif n >= SMALL_N:
        method = "hdbscan_swept"
        labels, mcs, sweep = _sweep_stable(vectors, n)
        if mcs == -1:
            method, mcs = "agglomerative_fallback", 0
    else:
        method, mcs = "agglomerative_fallback", 0
        labels = _agglomerative(vectors, LINK_THRESHOLD)

    if method.startswith("hdbscan") and not (labels >= 0).any():
        # Density estimation failed outright — fall back rather than report nothing.
        method, mcs = "agglomerative_fallback", 0
        labels = _agglomerative(vectors, LINK_THRESHOLD)

    groups: dict[int, list[int]] = {}
    for i, lab in enumerate(labels):
        groups.setdefault(int(lab), []).append(i)
    noise_idx = groups.pop(-1, [])

    clusters = [
        {"members": idxs, "recovered_from_noise": False}
        for _lab, idxs in sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)
    ]

    # --- Refinement 2: recovery pass over the noise points only ---------------
    recovered = 0
    if method.startswith("hdbscan") and len(noise_idx) >= RECOVERY_MIN_MCS * 2:
        rec_mcs = max(RECOVERY_MIN_MCS, (mcs or LARGE_MCS) // 2)
        sub_labels = _hdbscan(vectors[noise_idx], rec_mcs)
        sub_groups: dict[int, list[int]] = {}
        for j, lab in enumerate(sub_labels):
            sub_groups.setdefault(int(lab), []).append(noise_idx[j])
        noise_idx = sub_groups.pop(-1, [])
        for _lab, idxs in sorted(sub_groups.items(), key=lambda kv: len(kv[1]), reverse=True):
            clusters.append({"members": idxs, "recovered_from_noise": True})
            recovered += 1

    # --- Refinement 3: centroid similarity, flag only -------------------------
    merges: list[dict] = []
    if len(clusters) > 1:
        cents = []
        for c in clusters:
            v = vectors[c["members"]].mean(axis=0)
            cents.append(v / max(1e-12, float(np.linalg.norm(v))))
        sim = np.vstack(cents) @ np.vstack(cents).T
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                if sim[a, b] >= MERGE_SIMILARITY:
                    merges.append(
                        {"candidate_merge": [a, b], "similarity": round(float(sim[a, b]), 3)}
                    )

    def _example(j: int) -> dict:
        return {
            "ticket_id": rows[j]["ticket_id"],
            "what_was_asked": rows[j]["what_was_asked"],
            "steps_taken": rows[j]["steps_taken"],
        }

    return {
        "n_tickets": n,
        "method": method,
        "min_cluster_size": mcs,
        "sweep": sweep,
        "n_primary": sum(1 for c in clusters if not c["recovered_from_noise"]),
        "n_recovered": recovered,
        "n_noise": len(noise_idx),
        "clusters": [
            {
                "cluster_id": i,
                "size": len(c["members"]),
                "recovered_from_noise": c["recovered_from_noise"],
                "label": "",
                "ticket_ids": [rows[j]["ticket_id"] for j in c["members"]],
                "examples": [_example(j) for j in c["members"][:3]],
            }
            for i, c in enumerate(clusters)
        ],
        "candidate_merges": merges,
        "noise_examples": [_example(j) for j in noise_idx[:5]],
    }


# --- labelling ---------------------------------------------------------------
_LABEL_SYS = (
    "You name recurring support-workflow patterns. Given example CHAIR WORKFLOWS "
    "that were grouped together by similarity, write ONE short sentence naming the "
    "common procedure they share. Describe the PROCEDURE, not the topic. Respond "
    'STRICT JSON only: {"label": "<one sentence>"}'
)


async def label_all(data: dict, api_key: str, concurrency: int = 8) -> None:
    jobs = [(intent, cl) for intent, s in data.items() for cl in s["clusters"]]
    sem = asyncio.Semaphore(concurrency)
    base = settings.LOCAL_MODEL_BASE_URL.rstrip("/")

    async def one(client: httpx.AsyncClient, cl: dict) -> None:
        prompt = "\n\n".join(
            "WORKFLOW:\n" + "\n".join("  - " + s for s in e["steps_taken"]) for e in cl["examples"]
        )
        payload = {
            "model": settings.LOCAL_MODEL_NAME,
            "messages": [
                {"role": "system", "content": _LABEL_SYS},
                {"role": "user", "content": prompt + "\n\nReturn the JSON."},
            ],
            "max_tokens": 600,
            "temperature": settings.DRAFTER_TEMPERATURE,
            "seed": settings.DRAFTER_SEED,
            "stream": False,
        }
        async with sem:
            try:
                r = await post_chat(
                    client,
                    f"{base}/chat/completions",
                    payload,
                    {"Authorization": f"Bearer {api_key}"},
                )
                r.raise_for_status()
                obj = _extract_json(r.json()["choices"][0]["message"]["content"]) or {}
                cl["label"] = (obj.get("label") or "").strip()
            except Exception as exc:  # noqa: BLE001 - research script
                cl["label"] = f"[label failed: {type(exc).__name__}]"

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        tasks = [one(client, c) for _i, c in jobs]
        for k, coro in enumerate(asyncio.as_completed(tasks), start=1):
            await coro
            if k % 25 == 0 or k == len(tasks):
                print(f"  labelled {k}/{len(tasks)}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--intent",
        action="append",
        help="restrict to this intent (repeatable); omit to run all",
    )
    ap.add_argument("--stage1", type=Path, default=_STAGE1)
    ap.add_argument("--stage2", type=Path, default=_STAGE2)
    ap.add_argument("--out", type=Path, default=_OUT)
    ap.add_argument("--no-label", action="store_true", help="skip LLM labelling (no spend)")
    args = ap.parse_args()

    by_intent = load_joined(args.stage1, args.stage2)
    if args.intent:
        missing = [i for i in args.intent if i not in by_intent]
        if missing:
            raise SystemExit(f"unknown intent(s): {missing}")
        by_intent = {i: by_intent[i] for i in args.intent}
    print(f"\nembedding model: {settings.FAISS_MODEL_NAME} (local CPU, no API, no credential)\n")

    data: dict[str, dict] = {}
    for intent, rows in sorted(by_intent.items(), key=lambda kv: len(kv[1]), reverse=True):
        vecs = embed([" ".join(r["steps_taken"]) for r in rows])
        res = cluster_intent(rows, vecs)
        data[intent] = res
        print(
            f"{intent:28s} n={res['n_tickets']:>5}  {res['method']:22s} "
            f"primary={res['n_primary']:>2} recovered={res['n_recovered']:>2} "
            f"noise={res['n_noise']:>4}  merges={len(res['candidate_merges'])}"
        )

    if not args.no_label:
        total = sum(len(s["clusters"]) for s in data.values())
        print(f"\nlabelling {total} clusters (isolated mining credential)...")
        asyncio.run(label_all(data, load_mining_api_key()))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote -> {args.out}")


if __name__ == "__main__":
    main()
