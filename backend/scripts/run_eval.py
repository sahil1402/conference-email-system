"""Evaluation harness for the ConfMail pipeline (research tool, not an endpoint).

Runs the classifier, retriever, and router independently against a labeled
ground-truth dataset and emits a structured JSON report plus a human-readable
summary. Each component is evaluated on its own (we do not run the orchestrator
or persist anything), so the run needs no database for the default BM25 backend.

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
from app.pipeline.retriever import get_retriever  # noqa: E402
from app.pipeline.router import EmailRouter  # noqa: E402

_DEFAULT_GROUND_TRUTH = _PROJECT_ROOT / "data" / "eval" / "ground_truth.json"
_REPORTS_DIR = _BACKEND_DIR / "reports"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ConfMail pipeline evaluation harness.")
    parser.add_argument(
        "--retrieval",
        choices=["bm25", "faiss"],
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
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Pipeline run (classifier + retriever + router, per email)
# ---------------------------------------------------------------------------
async def _run_pipeline(entries: list[dict], top_k: int, backend: str, verbose: bool) -> list[dict]:
    classifier = IntentClassifier(strategy=settings.CLASSIFIER_BACKEND)
    router = EmailRouter(strategy=settings.ROUTING_STRATEGY)
    retriever = get_retriever()

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

        routing = router.route(classification, chunks)

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
# Report assembly + output
# ---------------------------------------------------------------------------
def _build_report(records: list[dict], metrics: dict, backend: str, top_k: int) -> dict:
    return {
        "run_id": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "retrieval_backend": backend,
            "top_k": top_k,
            "confidence_threshold": settings.FAQ_CONFIDENCE_THRESHOLD,
        },
        "summary": {
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


def _print_summary(report: dict, metrics: dict, output_path: Path) -> None:
    s = report["summary"]
    cfg = report["config"]
    faq = metrics["routing_breakdown"]["faq"]
    rev = metrics["routing_breakdown"]["human_review"]

    line = "=" * 32
    print(line)
    print(f"ConfMail Eval Harness — Run {report['run_id'][:8]}")
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
    print("Retrieval")
    print(f"  Hit rate        : {s['retrieval_hit_rate'] * 100:.1f}%")
    print()
    print("Per-intent F1:")
    width = max((len(k) for k in report["per_intent"]), default=0)
    for intent in sorted(report["per_intent"]):
        print(f"  {intent.ljust(width)} : {report['per_intent'][intent]['f1']:.3f}")
    print()
    print(f"Report saved → {output_path}")
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

    records = asyncio.run(
        _run_pipeline(entries, args.top_k, args.retrieval, args.verbose)
    )
    metrics = _compute_metrics(records)
    report = _build_report(records, metrics, args.retrieval, args.top_k)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    _print_summary(report, metrics, output_path)
    return report


if __name__ == "__main__":
    main()
