"""Real-ticket benchmark: coverage, retrieval ablation, classifier eval.

Implements test-roadmap steps 2-4 from docs/PIPELINE_AUDIT.md over the labeled
sample produced by label_real_tickets.py (data/eval_real/sample.jsonl +
labels.jsonl) and the real policy KB (policies.json).

Reports (all PII-free — ids and metrics only):
  1. Coverage — fraction of real traffic answerable from the policy docs,
     overall and by labeled intent. Bounds the FAQ lane.
  2. Retrieval ablation — backends bm25 / dense / fusion(RRF k=60) × query
     variants body300 (orchestrator parity) / body300+kw_intent / subject+body300,
     scored on the answerable subset with hit@k, recall@k, nDCG@k (multi-gold,
     k=1,3,5). Decides the backend and the intent token's fate.
  3. Classifier eval — keyword intent vs LLM label: accuracy, per-intent
     precision/recall/F1, top confusions, taxonomy-gap rate ("other"), and
     chair-routing accuracy (the classifier's actual v1 job).

The dense ranking mirrors app/pipeline/faiss_retriever.py exactly (MiniLM,
"title content", inner product on normalized vectors) but reads the KB JSON
directly instead of the policy_documents table, so the bench needs no DB.

Usage:
    python scripts/bench_real.py [--output reports/real_eval.json]
"""

import argparse
import asyncio
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from scripts._kb_retriever import build_retriever_from_kb  # noqa: E402

KB_PATH = REPO_ROOT / "data" / "knowledge_base" / "policies.json"
SAMPLE_PATH = REPO_ROOT / "data" / "eval_real" / "sample.jsonl"
LABELS_PATH = REPO_ROOT / "data" / "eval_real" / "labels.jsonl"

KS = (1, 3, 5)
RRF_K = 60  # standard constant, matches fusion_retriever.py

# Mirrors the Phase 6A seeded chair areas (migration 1f51f0224943), remapped to
# the 14-intent taxonomy: each content chair owns one family.
CHAIR_AREAS = {
    "Program Chair": {  # submission_compliance
        "author_profile_compliance", "submission_upload_help",
        "submission_requirements", "submission_format_policy", "author_list_change",
    },
    "Diversity & Ethics Chair": {  # appeals_integrity
        "review_decision_appeal", "desk_reject_appeal", "anonymity_violation",
    },
    "Local Arrangements Chair": {  # review_workflow
        "reviewer_assignment", "review_submission_help", "paper_bidding",
    },
    "Publicity/Sponsorship Chair": {  # committee
        "reviewer_workload_role", "committee_invitation",
    },
}


def chair_for(intent: str) -> str:
    for chair, areas in CHAIR_AREAS.items():
        if intent in areas:
            return chair
    return "General Chair (fallback)"


# ---------------------------------------------------------------------------
# Rankers — full rankings over the 93 chunks
# ---------------------------------------------------------------------------
class Rankers:
    def __init__(self) -> None:
        self.chunks = json.load(open(KB_PATH, encoding="utf-8"))
        self.ids = [c["id"] for c in self.chunks]
        self._bm25 = build_retriever_from_kb(KB_PATH)
        from sentence_transformers import SentenceTransformer

        self._embedder = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
        self._doc_vecs = self._embedder.encode(
            [f"{c['title']} {c['content']}" for c in self.chunks],
            normalize_embeddings=True,
        )

    def bm25(self, query: str, intent: str) -> list[str]:
        res = asyncio.run(
            self._bm25.retrieve(query, intent=intent, top_k=len(self.ids))
        )
        return [c.policy_id for c in res]

    def dense(self, query: str, intent: str) -> list[str]:
        text = f"{query} {intent}".strip()
        qv = self._embedder.encode([text], normalize_embeddings=True)
        import numpy as np

        order = np.argsort(-(self._doc_vecs @ qv.T).ravel())
        return [self.ids[i] for i in order]

    @staticmethod
    def fuse(rank_a: list[str], rank_b: list[str]) -> list[str]:
        scores: dict[str, float] = defaultdict(float)
        for ranking in (rank_a, rank_b):
            for pos, pid in enumerate(ranking, 1):
                scores[pid] += 1.0 / (RRF_K + pos)
        return sorted(scores, key=lambda p: (-scores[p], p))


# ---------------------------------------------------------------------------
# Multi-gold metrics
# ---------------------------------------------------------------------------
def score_ranking(ranking: list[str], gold: set[str]) -> dict:
    out = {}
    for k in KS:
        top = ranking[:k]
        hits = sum(1 for p in top if p in gold)
        out[f"hit@{k}"] = 1.0 if hits else 0.0
        out[f"recall@{k}"] = hits / len(gold)
        dcg = sum(
            1.0 / math.log2(i + 2) for i, p in enumerate(top) if p in gold
        )
        ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(gold), k)))
        out[f"ndcg@{k}"] = dcg / ideal if ideal else 0.0
    return out


def mean_scores(rows: list[dict]) -> dict:
    return {
        key: round(sum(r[key] for r in rows) / len(rows), 4)
        for key in rows[0]
    } if rows else {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "backend" / "reports" / f"real_eval_{time.strftime('%Y%m%d_%H%M%S')}.json",
    )
    args = parser.parse_args()

    samples = {r["ticket_id"]: r for r in map(json.loads, open(SAMPLE_PATH, encoding="utf-8"))}
    labels = [json.loads(l) for l in open(LABELS_PATH, encoding="utf-8")]
    labels = [l for l in labels if l["ticket_id"] in samples]
    print(f"labeled tickets: {len(labels)}")

    # ---- 1. Coverage ------------------------------------------------------
    answerable = [l for l in labels if l["policy_answerable"]]
    by_intent_cov = defaultdict(lambda: [0, 0])
    for l in labels:
        b = by_intent_cov[l["intent"]]
        b[1] += 1
        b[0] += l["policy_answerable"]
    coverage = {
        "overall": round(len(answerable) / len(labels), 3),
        "n_labeled": len(labels),
        "n_answerable": len(answerable),
        "by_llm_intent": {
            i: {"answerable": a, "total": t, "rate": round(a / t, 2)}
            for i, (a, t) in sorted(by_intent_cov.items(), key=lambda kv: -kv[1][1])
        },
    }
    print(f"coverage: {coverage['overall']:.1%} ({len(answerable)}/{len(labels)})")

    # ---- 2. Retrieval ablation -------------------------------------------
    scored = [l for l in answerable if l["relevant_chunk_ids"]]
    rankers = Rankers()
    variants = {
        "body300": lambda s, l: (s["question"][:300], ""),
        "body300+kw_intent": lambda s, l: (s["question"][:300], l["kw_intent"]),
        "subject+body300": lambda s, l: (f"{s['subject']} {s['question'][:300]}", ""),
    }
    ablation: dict[str, dict] = {}
    for vname, qfn in variants.items():
        per_backend: dict[str, list[dict]] = {"bm25": [], "dense": [], "fusion": []}
        for l in scored:
            s = samples[l["ticket_id"]]
            query, intent = qfn(s, l)
            gold = set(l["relevant_chunk_ids"])
            rank_b = rankers.bm25(query, intent)
            rank_d = rankers.dense(query, intent)
            per_backend["bm25"].append(score_ranking(rank_b, gold))
            per_backend["dense"].append(score_ranking(rank_d, gold))
            per_backend["fusion"].append(score_ranking(Rankers.fuse(rank_b, rank_d), gold))
        ablation[vname] = {b: mean_scores(rows) for b, rows in per_backend.items()}
        print(f"ablation[{vname}] done ({len(scored)} tickets)")

    # ---- 3. Classifier eval ----------------------------------------------
    taxonomy = [l for l in labels if l["intent"] != "other"]
    correct = [l for l in taxonomy if l["kw_intent"] == l["intent"]]
    confusion = Counter(
        (l["intent"], l["kw_intent"]) for l in taxonomy if l["kw_intent"] != l["intent"]
    )
    per_intent = {}
    for intent in {l["intent"] for l in taxonomy}:
        tp = sum(1 for l in taxonomy if l["intent"] == intent and l["kw_intent"] == intent)
        fn = sum(1 for l in taxonomy if l["intent"] == intent and l["kw_intent"] != intent)
        fp = sum(1 for l in taxonomy if l["intent"] != intent and l["kw_intent"] == intent)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_intent[intent] = {
            "precision": round(prec, 3), "recall": round(rec, 3),
            "f1": round(f1, 3), "support": tp + fn,
        }
    chair_ok = sum(
        1 for l in taxonomy if chair_for(l["kw_intent"]) == chair_for(l["intent"])
    )
    other_rate = (len(labels) - len(taxonomy)) / len(labels)
    other_labels = Counter(
        (l.get("intent_other") or "unspecified").lower()
        for l in labels
        if l["intent"] == "other"
    )
    classifier = {
        "n_in_taxonomy": len(taxonomy),
        "intent_accuracy": round(len(correct) / len(taxonomy), 3) if taxonomy else None,
        "chair_routing_accuracy": round(chair_ok / len(taxonomy), 3) if taxonomy else None,
        "taxonomy_gap_rate": round(other_rate, 3),
        "other_intents_top": other_labels.most_common(10),
        "per_intent": dict(sorted(per_intent.items(), key=lambda kv: -kv[1]["support"])),
        "top_confusions": [
            {"llm": a, "kw": b, "n": n} for (a, b), n in confusion.most_common(10)
        ],
    }
    print(
        f"classifier: intent acc {classifier['intent_accuracy']}, "
        f"chair-routing acc {classifier['chair_routing_accuracy']}, "
        f"taxonomy gap {other_rate:.1%}"
    )

    report = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kb": str(KB_PATH.name),
        "n_scored_retrieval": len(scored),
        "coverage": coverage,
        "retrieval_ablation": ablation,
        "classifier": classifier,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(f"report -> {args.output}")

    # Compact console table
    print("\n=== retrieval ablation (hit@3 / recall@3 / ndcg@3) ===")
    for vname, backends in ablation.items():
        for b, m in backends.items():
            print(f"  {vname:20s} {b:7s} {m['hit@3']:.3f} / {m['recall@3']:.3f} / {m['ndcg@3']:.3f}")


if __name__ == "__main__":
    main()
