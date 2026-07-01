"""Tests for the evaluation harness (ground-truth dataset + run_eval.py).

The dataset lives at the project root (data/eval/ground_truth.json), consistent
with the existing data/ layout. The eval is executed once per module via
``run_eval.main`` against a temp output file (BM25 backend → no DB, no network).
"""

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

# Project root: backend/tests/test_eval_harness.py → parents[2].
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_GROUND_TRUTH = _PROJECT_ROOT / "data" / "eval" / "ground_truth.json"

# run_eval.py lives in backend/scripts (not a package) — import it by path.
sys.path.insert(0, str(_BACKEND_DIR / "scripts"))
import run_eval  # noqa: E402


def _load_ground_truth() -> list[dict]:
    with open(_GROUND_TRUTH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def eval_report(tmp_path_factory) -> dict:
    """Run the eval harness once (BM25) and return the written report JSON."""
    out = tmp_path_factory.mktemp("eval") / "report.json"
    run_eval.main(["--retrieval", "bm25", "--output", str(out)])
    with open(out, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Ground-truth dataset
# ---------------------------------------------------------------------------
def test_ground_truth_loads():
    entries = _load_ground_truth()
    # 40 original + 18 Phase 5A boundary cases.
    assert len(entries) == 58
    required = {"id", "subject", "body", "ground_truth_intent", "ground_truth_lane"}
    assert all(required <= set(e) for e in entries)
    assert all(e["ground_truth_lane"] in ("faq", "human_review") for e in entries)
    # Every entry carries a relevant_chunk_id field (may be null when the KB
    # genuinely has no relevant policy) for the retrieval-only metrics.
    assert all("relevant_chunk_id" in e for e in entries)


def test_ground_truth_intent_distribution():
    entries = _load_ground_truth()
    counts = Counter(e["ground_truth_intent"] for e in entries)
    assert len(counts) >= 4
    assert min(counts.values()) >= 2
    human_review = sum(1 for e in entries if e["ground_truth_lane"] == "human_review")
    assert human_review >= 8


# ---------------------------------------------------------------------------
# Eval report
# ---------------------------------------------------------------------------
def test_eval_produces_valid_report(eval_report):
    expected_total = len(_load_ground_truth())
    summary = eval_report["summary"]
    assert summary["total_emails"] == expected_total
    assert 0.0 <= summary["classification_macro_f1"] <= 1.0
    assert 0.0 <= summary["routing_accuracy"] <= 1.0
    assert 0.0 <= summary["retrieval_hit_rate"] <= 1.0
    # end_to_end_metrics and retrieval_metrics are separate, non-blended sections.
    assert eval_report["end_to_end_metrics"] == summary
    rm = eval_report["retrieval_metrics"]
    assert set(rm["backends"]) == {"bm25", "faiss"}
    for k in (1, 3, 5):
        assert 0.0 <= rm["backends"]["bm25"][f"recall@{k}"] <= 1.0
        assert 0.0 <= rm["backends"]["bm25"][f"ndcg@{k}"] <= 1.0


def test_eval_per_email_completeness(eval_report):
    per_email = eval_report["per_email"]
    assert len(per_email) == len(_load_ground_truth())
    for entry in per_email:
        assert isinstance(entry["correct_intent"], bool)
        assert isinstance(entry["correct_lane"], bool)


def test_eval_confusion_matrix_shape(eval_report):
    cm = eval_report["confusion_matrix"]
    labels = cm["labels"]
    matrix = cm["matrix"]
    assert isinstance(labels, list) and len(labels) > 0
    n = len(labels)
    assert len(matrix) == n
    assert all(isinstance(row, list) and len(row) == n for row in matrix)
