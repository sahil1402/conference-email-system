"""Tests for the retrieval-only ranking metrics (Phase 5A).

Exercises the pure recall@k / nDCG@k functions in run_eval on small,
hand-checked fixtures where the correct answer is obvious by inspection — so a
regression in the ranking math is caught without needing a retriever or a DB.
"""

import math
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR / "scripts"))

import run_eval  # noqa: E402


# ---------------------------------------------------------------------------
# recall@k — single relevant chunk
# ---------------------------------------------------------------------------
def test_recall_at_k_hit_and_miss():
    ranked = ["policy_002", "policy_001", "policy_004"]
    # Relevant at rank 1 → in every top-k.
    assert run_eval.recall_at_k(ranked, "policy_002", 1) == 1.0
    assert run_eval.recall_at_k(ranked, "policy_002", 3) == 1.0
    # Relevant at rank 3 → missed at k=1, found at k=3.
    assert run_eval.recall_at_k(ranked, "policy_004", 1) == 0.0
    assert run_eval.recall_at_k(ranked, "policy_004", 3) == 1.0
    # Not retrieved at all → always a miss.
    assert run_eval.recall_at_k(ranked, "policy_999", 5) == 0.0


# ---------------------------------------------------------------------------
# nDCG@k — position-weighted, ideal DCG = 1.0 for a single relevant doc
# ---------------------------------------------------------------------------
def test_ndcg_at_k_position_weighting():
    ranked = ["A", "B", "C", "D", "E"]
    # Rank 1 → 1/log2(2) = 1.0
    assert run_eval.ndcg_at_k(ranked, "A", 3) == 1.0
    # Rank 2 → 1/log2(3) ≈ 0.6309
    assert math.isclose(run_eval.ndcg_at_k(ranked, "B", 3), 1 / math.log2(3), rel_tol=1e-9)
    # Rank 3 → 1/log2(4) = 0.5
    assert math.isclose(run_eval.ndcg_at_k(ranked, "C", 3), 0.5, rel_tol=1e-9)
    # Beyond the cutoff → 0.0
    assert run_eval.ndcg_at_k(ranked, "D", 3) == 0.0
    assert run_eval.ndcg_at_k(ranked, "E", 5) == 1 / math.log2(6)


def test_dcg_equals_ndcg_for_single_relevant():
    # With one relevant doc the ideal DCG is 1.0, so nDCG == DCG.
    ranked = ["X", "Y", "Z"]
    for rel in ("X", "Y", "Z", "missing"):
        assert run_eval.dcg_at_k(ranked, rel, 3) == run_eval.ndcg_at_k(ranked, rel, 3)


# ---------------------------------------------------------------------------
# score_retrieval — aggregate over a known 4-query fixture
# ---------------------------------------------------------------------------
def test_score_retrieval_known_fixture():
    # Four queries with hand-computed expected values:
    #   q1: gold at rank 1
    #   q2: gold at rank 2
    #   q3: gold at rank 3
    #   q4: gold not retrieved
    scored = [
        ("g1", ["g1", "x", "y"]),   # rank 1
        ("g2", ["x", "g2", "y"]),   # rank 2
        ("g3", ["x", "y", "g3"]),   # rank 3
        ("g4", ["x", "y", "z"]),    # miss
    ]
    m = run_eval.score_retrieval(scored, ks=(1, 3, 5))

    # recall@1: only q1 hits → 1/4 = 0.25
    assert m["recall@1"] == 0.25
    # recall@3: q1,q2,q3 hit → 3/4 = 0.75
    assert m["recall@3"] == 0.75
    assert m["recall@5"] == 0.75

    # nDCG@3 = mean(1.0, 1/log2(3), 0.5, 0.0)
    expected_ndcg3 = (1.0 + 1 / math.log2(3) + 0.5 + 0.0) / 4
    assert math.isclose(m["ndcg@3"], round(expected_ndcg3, 4), abs_tol=1e-4)
    # nDCG@1 = mean(1.0, 0, 0, 0) = 0.25
    assert m["ndcg@1"] == 0.25


def test_score_retrieval_empty_is_zero():
    m = run_eval.score_retrieval([], ks=(1, 3, 5))
    assert m == {
        "recall@1": 0.0,
        "ndcg@1": 0.0,
        "recall@3": 0.0,
        "ndcg@3": 0.0,
        "recall@5": 0.0,
        "ndcg@5": 0.0,
    }
