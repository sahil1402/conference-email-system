"""Evaluation harness for the ConfMail pipeline (research tool, not an endpoint).

Runs the classifier, retriever, drafter, and router end-to-end against a
labeled ground-truth dataset and emits a structured JSON report plus a
human-readable summary. The router decides the lane from the DRAFT (not just
the classification) — same contract as the orchestrator — so a real draft is
generated for every email before routing. We still do not run the full
orchestrator or persist anything, so the run needs no database for the
default BM25 backend (the drafter does not import ``app.db`` either).

Usage:
    cd backend && python scripts/run_eval.py
    cd backend && python scripts/run_eval.py --retrieval faiss
    cd backend && python scripts/run_eval.py --top-k 5 --output reports/eval_latest.json

The script does not import app.db: the keyword classifier and BM25 retriever read
from JSON files. The FAISS backend loads policies from the database internally;
if that index comes back empty, the run prints a clear warning and continues
(retrieval hits will simply be 0).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import uuid
from datetime import datetime
from pathlib import Path

from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    precision_recall_fscore_support,
)

# Make `app...` importable regardless of cwd (script lives in backend/scripts/).
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.pipeline import retriever as retriever_module  # noqa: E402
from app.pipeline.classifier import IntentClassifier  # noqa: E402
from app.pipeline.drafter import ResponseDrafter  # noqa: E402
from app.pipeline.retriever import get_retriever  # noqa: E402
from app.pipeline.router import EmailRouter  # noqa: E402

_DEFAULT_GROUND_TRUTH = _PROJECT_ROOT / "data" / "eval" / "ground_truth.json"
_REPORTS_DIR = _BACKEND_DIR / "reports"

# Cutoffs for the retrieval-only ranking metrics (recall@k / nDCG@k).
_RETRIEVAL_KS = (1, 3, 5)
# Backends compared side-by-side in the retrieval-only section (Phase 5C adds
# fusion = Reciprocal Rank Fusion over bm25 + faiss).
_RETRIEVAL_BACKENDS = ("bm25", "faiss", "fusion")


# ---------------------------------------------------------------------------
# Retrieval-only ranking metrics (pure functions — no DB, no I/O)
# ---------------------------------------------------------------------------
# These isolate retrieval quality from classification: they score the ranked
# policy ids a retriever returns against a single hand-labeled relevant chunk
# (``relevant_chunk_id`` in the ground truth). Kept pure so they are unit-tested
# directly on small hand-checked fixtures.
def recall_at_k(retrieved_ids: list[str], relevant_id: str, k: int) -> float:
    """1.0 if the relevant chunk is in the top-k, else 0.0 (single-gold)."""
    return 1.0 if relevant_id in retrieved_ids[:k] else 0.0


def dcg_at_k(retrieved_ids: list[str], relevant_id: str, k: int) -> float:
    """Discounted cumulative gain at k for a single relevant document.

    Binary relevance: the one relevant chunk contributes ``1 / log2(rank + 1)``
    at its 1-based rank if it lands within the top-k, otherwise 0.
    """
    for rank, pid in enumerate(retrieved_ids[:k], start=1):
        if pid == relevant_id:
            return 1.0 / math.log2(rank + 1)
    return 0.0


def ndcg_at_k(retrieved_ids: list[str], relevant_id: str, k: int) -> float:
    """nDCG at k. With a single relevant doc the ideal DCG is 1.0 (rank 1),
    so nDCG reduces to the DCG value itself."""
    return dcg_at_k(retrieved_ids, relevant_id, k)


def score_retrieval(
    scored: list[tuple[str, list[str]]], ks: tuple[int, ...] = _RETRIEVAL_KS
) -> dict:
    """Aggregate recall@k and nDCG@k (mean over queries) for each k.

    ``scored`` is a list of ``(relevant_id, retrieved_ids)`` pairs; queries with
    no gold chunk must be filtered out by the caller before this is called.
    """
    n = len(scored)
    metrics: dict[str, float] = {}
    for k in ks:
        if n == 0:
            metrics[f"recall@{k}"] = 0.0
            metrics[f"ndcg@{k}"] = 0.0
            continue
        recall = sum(recall_at_k(ids, rel, k) for rel, ids in scored) / n
        ndcg = sum(ndcg_at_k(ids, rel, k) for rel, ids in scored) / n
        metrics[f"recall@{k}"] = round(recall, 4)
        metrics[f"ndcg@{k}"] = round(ndcg, 4)
    return metrics


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ConfMail pipeline evaluation harness.")
    parser.add_argument(
        "--retrieval",
        choices=["bm25", "faiss", "fusion"],
        default=settings.RETRIEVAL_BACKEND,
        help="Retrieval backend (default: RETRIEVAL_BACKEND env / config).",
    )
    parser.add_argument("--top-k", type=int, default=3, help="Chunks to retrieve per email.")
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Report output path (default: reports/eval_<timestamp>.json).",
    )
    parser.add_argument(
        "--ground-truth",
        type=str,
        default=str(_DEFAULT_GROUND_TRUTH),
        help="Path to the ground-truth dataset JSON.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print per-email results."
    )
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help=(
            "Only run the retrieval-only ranking metrics (recall@k / nDCG@k) "
            "for every backend; skip the end-to-end classification/routing run."
        ),
    )
    parser.add_argument(
        "--no-retrieval-metrics",
        action="store_true",
        help="Skip the retrieval-only ranking section in a normal run.",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Pipeline run (classifier + retriever + router, per email)
# ---------------------------------------------------------------------------
async def _run_pipeline(entries: list[dict], top_k: int, backend: str, verbose: bool) -> list[dict]:
    classifier = IntentClassifier(strategy=settings.CLASSIFIER_BACKEND)
    router = EmailRouter(strategy=settings.ROUTING_STRATEGY)
    retriever = get_retriever()
    # One drafter instance reused across the loop, same as classifier/retriever/
    # router above — the router's FAQ-lane gate is decided by the DRAFT (see
    # app.pipeline.router.EmailRouter.route), so a real draft is required before
    # routing; the eval harness must mirror the orchestrator's contract here.
    drafter = ResponseDrafter(provider=settings.MODEL_PROVIDER)

    records: list[dict] = []
    faiss_warned = False

    for entry in entries:
        subject = entry.get("subject", "")
        body = entry.get("body", "")

        classification = await classifier.classify(body, subject)

        try:
            chunks = await retriever.retrieve(body, classification.intent, top_k=top_k)
        except Exception as exc:  # noqa: BLE001 - degrade to empty retrieval
            chunks = []
            if backend == "faiss" and not faiss_warned:
                print(
                    f"  [WARN] FAISS retrieval failed ({type(exc).__name__}: {exc}). "
                    "FAISS requires a seeded database — run scripts/seed.py. "
                    "Continuing with empty retrieval.",
                    file=sys.stderr,
                )
                faiss_warned = True

        email_data = {
            "from": entry.get("from") or entry.get("sender") or "",
            "subject": subject,
            "body": body,
        }
        draft = await drafter.draft(email_data, classification, chunks)
        routing = router.route(classification, chunks, draft)

        keywords = [k.lower() for k in entry.get("relevant_policy_keywords", [])]
        hit = any(
            kw in (chunk.content or "").lower() for kw in keywords for chunk in chunks
        )

        record = {
            "id": entry.get("id"),
            "predicted_intent": classification.intent,
            "ground_truth_intent": entry.get("ground_truth_intent"),
            "correct_intent": classification.intent == entry.get("ground_truth_intent"),
            "predicted_lane": routing.lane,
            "ground_truth_lane": entry.get("ground_truth_lane"),
            "correct_lane": routing.lane == entry.get("ground_truth_lane"),
            "confidence": round(float(classification.confidence), 4),
            "retrieval_hit": bool(hit),
            "difficulty": entry.get("difficulty", "easy"),
        }
        records.append(record)

        if verbose:
            ok_i = "OK " if record["correct_intent"] else "XX "
            ok_l = "OK " if record["correct_lane"] else "XX "
            print(
                f"  {record['id']}: "
                f"intent {ok_i}{record['predicted_intent']} (gt {record['ground_truth_intent']}) "
                f"| lane {ok_l}{record['predicted_lane']} (gt {record['ground_truth_lane']}) "
                f"| conf {record['confidence']:.2f} | hit {record['retrieval_hit']}"
            )

    if backend == "faiss" and not faiss_warned and getattr(retriever, "document_count", 0) == 0:
        print(
            "  [WARN] FAISS index is empty (0 documents). FAISS requires a seeded "
            "database — run scripts/seed.py. Retrieval hit-rate will be 0.",
            file=sys.stderr,
        )

    return records


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def _compute_metrics(records: list[dict]) -> dict:
    y_true = [r["ground_truth_intent"] for r in records]
    y_pred = [r["predicted_intent"] for r in records]
    labels = sorted(set(y_true) | set(y_pred))

    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, average=None, zero_division=0
    )
    macro_f1 = float(
        precision_recall_fscore_support(
            y_true, y_pred, labels=labels, average="macro", zero_division=0
        )[2]
    )
    clf_accuracy = float(accuracy_score(y_true, y_pred))

    per_intent = {
        label: {
            "precision": round(float(precision[i]), 4),
            "recall": round(float(recall[i]), 4),
            "f1": round(float(f1[i]), 4),
            "support": int(support[i]),
        }
        for i, label in enumerate(labels)
    }

    cm = confusion_matrix(y_true, y_pred, labels=labels)
    confusion = {"labels": labels, "matrix": [[int(v) for v in row] for row in cm]}

    # Routing accuracy + per-lane breakdown (denominator = ground-truth lane count).
    # NOTE: routing is now DRAFT-DEPENDENT (the router's FAQ-lane gate reads the
    # draft's completeness/groundedness/answer_confidence), so this is an
    # END-TO-END, PROVIDER-DEPENDENT metric, not a router-in-isolation one. With
    # a non-LLM provider (the hermetic default, "fallback"/"template") the
    # draft's answer_confidence is always None, so the draft-quality gate
    # conservatively routes everything to human_review — routing_accuracy will
    # just reflect the ground-truth lane mix, NOT routing quality. A meaningful
    # FAQ-lane number requires running with a real drafting provider
    # (MODEL_PROVIDER=anthropic_api/anthropic/local).
    routing_correct = sum(1 for r in records if r["correct_lane"])
    routing_accuracy = routing_correct / len(records) if records else 0.0

    def _lane_breakdown(lane: str) -> dict:
        total = sum(1 for r in records if r["ground_truth_lane"] == lane)
        correct = sum(
            1 for r in records if r["ground_truth_lane"] == lane and r["correct_lane"]
        )
        return {"correct": correct, "total": total}

    # Retrieval hit-rate.
    hits = sum(1 for r in records if r["retrieval_hit"])
    hit_rate = hits / len(records) if records else 0.0

    return {
        "classification_macro_f1": round(macro_f1, 4),
        "classification_accuracy": round(clf_accuracy, 4),
        "routing_accuracy": round(routing_accuracy, 4),
        "retrieval_hit_rate": round(hit_rate, 4),
        "per_intent": per_intent,
        "confusion_matrix": confusion,
        "routing_breakdown": {
            "faq": _lane_breakdown("faq"),
            "human_review": _lane_breakdown("human_review"),
        },
    }


# ---------------------------------------------------------------------------
# Retrieval-only evaluation (both backends, per email)
# ---------------------------------------------------------------------------
async def _retrieve_ids(entries: list[dict], backend: str, top_k: int) -> list[list[str]]:
    """Return the ranked policy ids each backend retrieves for every email.

    The query pairs the email body with its GROUND-TRUTH intent (not the
    classifier's prediction), so retrieval quality is measured independently of
    classification. Switches the backend and resets the factory singleton so the
    right retriever is built.
    """
    settings.RETRIEVAL_BACKEND = backend
    retriever_module._retriever_singleton = None
    retriever_module._retriever_backend = None
    retriever = get_retriever()

    ranked: list[list[str]] = []
    for entry in entries:
        try:
            chunks = await retriever.retrieve(
                entry.get("body", ""), entry.get("ground_truth_intent", ""), top_k=top_k
            )
            ranked.append([c.policy_id for c in chunks])
        except Exception:  # noqa: BLE001 - degrade to empty retrieval
            ranked.append([])
    return ranked


async def _evaluate_retrieval(entries: list[dict]) -> dict:
    """Compute recall@k / nDCG@k for each backend, side-by-side.

    Emails whose ``relevant_chunk_id`` is null/absent (the KB genuinely has no
    relevant policy) are excluded from scoring and reported separately, so the
    absolute numbers stay meaningful rather than being dragged down by
    unanswerable queries.
    """
    labeled = [e for e in entries if e.get("relevant_chunk_id")]
    excluded = [e.get("id") for e in entries if not e.get("relevant_chunk_id")]
    top_k = max(_RETRIEVAL_KS)

    # Switching backends mutates the global RETRIEVAL_BACKEND + factory singleton;
    # restore both afterwards so this eval never leaks state into the caller.
    original_backend = settings.RETRIEVAL_BACKEND

    backends: dict[str, dict] = {}
    try:
        for backend in _RETRIEVAL_BACKENDS:
            try:
                ranked = await _retrieve_ids(labeled, backend, top_k)
                scored = [
                    (e["relevant_chunk_id"], ids) for e, ids in zip(labeled, ranked)
                ]
                backends[backend] = score_retrieval(scored, _RETRIEVAL_KS)
            except Exception as exc:  # noqa: BLE001 - a backend may be unavailable
                backends[backend] = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        settings.RETRIEVAL_BACKEND = original_backend
        retriever_module._retriever_singleton = None
        retriever_module._retriever_backend = None

    return {
        "ks": list(_RETRIEVAL_KS),
        "query_intent_source": "ground_truth_intent",
        "scored_emails": len(labeled),
        "excluded_no_gold": excluded,
        "backends": backends,
    }


# ---------------------------------------------------------------------------
# Report assembly + output
# ---------------------------------------------------------------------------
def _build_report(
    records: list[dict],
    metrics: dict,
    backend: str,
    top_k: int,
    retrieval_metrics: dict | None = None,
) -> dict:
    report = {
        "run_id": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "retrieval_backend": backend,
            "top_k": top_k,
            "confidence_threshold": settings.FAQ_CONFIDENCE_THRESHOLD,
        },
        # End-to-end pipeline quality (classification + routing + hit-rate).
        "end_to_end_metrics": {
            "total_emails": len(records),
            "classification_macro_f1": metrics["classification_macro_f1"],
            "classification_accuracy": metrics["classification_accuracy"],
            "routing_accuracy": metrics["routing_accuracy"],
            "retrieval_hit_rate": metrics["retrieval_hit_rate"],
        },
        "per_intent": metrics["per_intent"],
        "confusion_matrix": metrics["confusion_matrix"],
        "per_email": records,
    }
    # "summary" kept as an alias of end_to_end_metrics for backward compatibility
    # with existing report consumers (Phase 4B report shape).
    report["summary"] = report["end_to_end_metrics"]
    # Retrieval-only ranking quality lives in its own top-level section so it is
    # never blended into the end-to-end numbers above.
    if retrieval_metrics is not None:
        report["retrieval_metrics"] = retrieval_metrics
    return report


def _print_retrieval_metrics(retrieval: dict | None) -> None:
    """Print the retrieval-only recall@k / nDCG@k comparison, if present."""
    if not retrieval:
        return
    ks = retrieval["ks"]
    print("Retrieval-only ranking (query = body + ground-truth intent)")
    print(
        f"  Scored emails   : {retrieval['scored_emails']}"
        + (
            f" (excluded {len(retrieval['excluded_no_gold'])} with no gold chunk)"
            if retrieval["excluded_no_gold"]
            else ""
        )
    )
    header = "  " + "backend".ljust(8) + "".join(f"  R@{k}   N@{k} " for k in ks)
    print(header)
    for backend, m in retrieval["backends"].items():
        if "error" in m:
            print(f"  {backend.ljust(8)}  (unavailable: {m['error']})")
            continue
        cells = "".join(
            f"  {m[f'recall@{k}']:.3f} {m[f'ndcg@{k}']:.3f}" for k in ks
        )
        print(f"  {backend.ljust(8)}{cells}")
    print()


def _print_summary(report: dict, metrics: dict, output_path: Path) -> None:
    s = report["summary"]
    cfg = report["config"]
    faq = metrics["routing_breakdown"]["faq"]
    rev = metrics["routing_breakdown"]["human_review"]

    line = "=" * 32
    print(line)
    print(f"ConfMail Eval Harness - Run {report['run_id'][:8]}")
    print(line)
    print(f"Retrieval backend : {cfg['retrieval_backend']}")
    print(f"Confidence threshold: {cfg['confidence_threshold']}")
    print(f"Total emails      : {s['total_emails']}")
    print()
    print("Classification")
    print(f"  Accuracy        : {s['classification_accuracy'] * 100:.1f}%")
    print(f"  Macro F1        : {s['classification_macro_f1']:.3f}")
    print()
    print("Routing")
    print(f"  Accuracy        : {s['routing_accuracy'] * 100:.1f}%")
    print(f"  FAQ correct     : {faq['correct']}/{faq['total']}")
    print(f"  Review correct  : {rev['correct']}/{rev['total']}")
    print()
    print("Retrieval (end-to-end hit-rate)")
    print(f"  Hit rate        : {s['retrieval_hit_rate'] * 100:.1f}%")
    print()
    _print_retrieval_metrics(report.get("retrieval_metrics"))
    print("Per-intent F1:")
    width = max((len(k) for k in report["per_intent"]), default=0)
    for intent in sorted(report["per_intent"]):
        print(f"  {intent.ljust(width)} : {report['per_intent'][intent]['f1']:.3f}")
    print()
    print(f"Report saved -> {output_path}")
    print(line)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> dict:
    """Run the eval harness; return the report dict (and write it to disk)."""
    args = _parse_args(argv)

    # Apply the chosen retrieval backend and reset the factory singleton so the
    # right retriever is built for this run.
    settings.RETRIEVAL_BACKEND = args.retrieval
    retriever_module._retriever_singleton = None
    retriever_module._retriever_backend = None

    gt_path = Path(args.ground_truth)
    with open(gt_path, encoding="utf-8") as fh:
        entries = json.load(fh)

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = _BACKEND_DIR / output_path
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = _REPORTS_DIR / f"eval_{stamp}.json"

    if args.verbose:
        print(f"Running eval over {len(entries)} emails (backend={args.retrieval}, top_k={args.top_k})...")

    # --- retrieval-only mode: skip the end-to-end run entirely -------------
    if args.retrieval_only:
        retrieval_metrics = asyncio.run(_evaluate_retrieval(entries))
        report = {
            "run_id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "mode": "retrieval_only",
            "config": {"top_k": max(_RETRIEVAL_KS)},
            "retrieval_metrics": retrieval_metrics,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        line = "=" * 32
        print(line)
        print("ConfMail Retrieval-Only Eval")
        print(line)
        _print_retrieval_metrics(retrieval_metrics)
        print(f"Report saved -> {output_path}")
        print(line)
        return report

    # --- normal end-to-end run --------------------------------------------
    records = asyncio.run(
        _run_pipeline(entries, args.top_k, args.retrieval, args.verbose)
    )
    metrics = _compute_metrics(records)

    # Retrieval-only ranking metrics run AFTER the e2e pass because they switch
    # the backend singleton per backend; the e2e config still reports args.retrieval.
    retrieval_metrics = (
        None if args.no_retrieval_metrics else asyncio.run(_evaluate_retrieval(entries))
    )
    report = _build_report(
        records, metrics, args.retrieval, args.top_k, retrieval_metrics
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    _print_summary(report, metrics, output_path)
    return report


if __name__ == "__main__":
    main()
