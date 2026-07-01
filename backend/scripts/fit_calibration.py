"""Fit a confidence calibrator for a classifier backend (research/ops tool).

Runs the chosen classifier over the labeled ground-truth set, records
``(raw_confidence, was_intent_correct)`` pairs, fits a calibrator, saves the
artifact under backend/models/, and prints a decile reliability table showing
raw confidence vs. actual accuracy per bucket — so the miscalibration is visible
directly. This is the same data Phase 5E's reliability diagram will visualize.

Usage:
    cd backend && python scripts/fit_calibration.py --backend keyword
    cd backend && python scripts/fit_calibration.py --backend keyword --method isotonic
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `app...` importable regardless of cwd (script lives in backend/scripts/).
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.pipeline.calibration import (  # noqa: E402
    VALID_METHODS,
    fit_calibrator_for_backend,
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fit a confidence calibrator.")
    parser.add_argument(
        "--backend",
        choices=["keyword", "trainable"],
        default="keyword",
        help="Classifier backend to calibrate (default: keyword).",
    )
    parser.add_argument(
        "--method",
        choices=list(VALID_METHODS),
        default="platt",
        help="Calibration method (default: platt — stable on small sets).",
    )
    parser.add_argument(
        "--ground-truth",
        type=str,
        default=None,
        help="Path to the ground-truth dataset JSON (default: data/eval/ground_truth.json).",
    )
    return parser.parse_args(argv)


def _print_reliability(title: str, rows: list[dict]) -> None:
    """Print a decile reliability table: confidence bucket → mean conf vs accuracy."""
    print(title)
    print(f"  {'bucket':<10} {'n':>4}  {'mean_conf':>9}  {'accuracy':>8}  gap")
    for r in rows:
        gap = r["accuracy"] - r["mean_confidence"]
        print(
            f"  {r['bucket']:<10} {r['count']:>4}  "
            f"{r['mean_confidence']:>9.3f}  {r['accuracy']:>8.3f}  {gap:+.3f}"
        )
    print()


def main(argv: list[str] | None = None) -> dict:
    args = _parse_args(argv)
    report = fit_calibrator_for_backend(args.backend, args.method, args.ground_truth)

    line = "=" * 52
    print(line)
    print(f"Confidence Calibration - backend={report['backend']} method={report['method']}")
    print(line)
    print(f"Samples           : {report['samples']}")
    print(
        f"Intent-correct    : {report['n_correct']}/{report['samples']} "
        f"({report['classification_accuracy'] * 100:.1f}%)"
    )
    print()
    print(f"Brier  before/after: {report['brier_before']:.4f} -> {report['brier_after']:.4f}")
    print(f"ECE    before/after: {report['ece_before']:.4f} -> {report['ece_after']:.4f}")
    print()
    _print_reliability("BEFORE (raw confidence):", report["reliability_before"])
    _print_reliability("AFTER (calibrated confidence):", report["reliability_after"])
    print(f"Artifact saved -> {report['artifact_path']}")
    print(line)
    return report


if __name__ == "__main__":
    main()
