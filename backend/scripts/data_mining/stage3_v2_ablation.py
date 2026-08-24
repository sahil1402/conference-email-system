"""Ablation + noise-floor study for the Stage 3 v2 proposal. READ-ONLY, local, no spend.

Two questions the headline comparison cannot answer on its own.

Q1. WHICH of the two changes is responsible? v2 alters the space (UMAP) AND the
    selection rule (DBCV argmax) at once, so a joint result cannot attribute
    anything. Four configurations, one grid, same embeddings:

        A  raw 384-dim  + v1 rule (lowest noise, plateau tie-break)
        B  UMAP 10-dim  + v1 rule
        C  raw 384-dim  + DBCV argmax
        D  UMAP 10-dim  + DBCV argmax        (= v2 as shipped)

    A vs B isolates the space. A vs C isolates the rule. D is the product.
    NOTE: v1-in-production is not exactly A — for n >= 200 it used a hard-coded
    mcs=15 rather than sweeping. A applies the v1 RULE over the shared grid at
    every n, which is what makes the four cells differ in one variable each.

Q2. Is the argmax margin real? The sweep showed 2-cluster solutions beating
    13-cluster ones by ~0.008 DBCV. If re-running UMAP under a different random
    seed moves DBCV by more than that, then "highest DBCV wins" is selecting on
    seed noise, and the rule reproduces the exact pathology the v1 plateau fix
    was written to remove. Estimated by refitting UMAP over several seeds at a
    fixed (n_neighbors, mcs) and reporting the spread.

Usage:
    python scripts/data_mining/stage3_v2_ablation.py
"""

from __future__ import annotations

import json
import statistics
import sys
import warnings
from collections import Counter
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

warnings.filterwarnings("ignore")

from dbcv import dbcv  # noqa: E402
from stage3_cluster import (  # noqa: E402
    _STAGE1,
    _STAGE2,
    SMALL_N,
    _hdbscan,
    embed,
    load_joined,
    select_trial,
)
from stage3_cluster_v2 import N_NEIGHBORS_GRID, mcs_grid, select_by_dbcv  # noqa: E402

_OUT = _ROOT / "data" / "mining" / "stage3_v2_test" / "ablation.json"
_SEEDS = (42, 7, 123, 2024, 31337)
_PROBE = ("review_submission_help", "reviewer_assignment", "desk_reject_appeal")


def _umap(vectors: np.ndarray, n_comp: int, n_neigh: int, seed: int) -> np.ndarray:
    import umap

    n = len(vectors)
    return np.asarray(
        umap.UMAP(
            n_components=max(2, min(n_comp, n - 2)),
            n_neighbors=max(2, min(n_neigh, n - 1)),
            min_dist=0.0,
            metric="cosine",
            random_state=seed,
        ).fit_transform(np.asarray(vectors, dtype="float32")),
        dtype="float64",
    )


def _trials(space: np.ndarray, n: int, n_neigh: int | None) -> list[dict]:
    out = []
    for mcs in mcs_grid(n):
        labels = _hdbscan(space, mcs)
        counts = Counter(labels.tolist())
        n_noise = counts.pop(-1, 0)
        s = dbcv(space, labels)
        out.append(
            {
                "mcs": mcs,
                "n_neighbors": n_neigh,
                "n_clusters": len(counts),
                "n_noise": int(n_noise),
                "dbcv": s["dbcv"],
                "labels": labels,
            }
        )
    return out


def _pub(t: dict | None) -> dict:
    if t is None:
        return {"n_clusters": 0, "n_noise": None, "mcs": None, "dbcv": None}
    return {k: t[k] for k in ("mcs", "n_neighbors", "n_clusters", "n_noise", "dbcv")}


def main() -> None:
    by_intent = load_joined(_STAGE1, _STAGE2)
    intents = [
        i
        for i in sorted(by_intent, key=lambda k: len(by_intent[k]), reverse=True)
        if len(by_intent[i]) >= SMALL_N
    ]
    report: dict = {"ablation": {}, "noise_floor": {}, "margin_vs_floor": {}}

    print("\n" + "=" * 124)
    print("Q1 — ABLATION: which change does what?  (A/B = space, A/C = rule, D = v2)")
    print("=" * 124)
    print(
        f"{'intent':27s}{'n':>6} | {'A raw+v1rule':>22} | {'B umap+v1rule':>22}"
        f" | {'C raw+DBCV':>22} | {'D umap+DBCV (v2)':>22}"
    )
    print(f"{'':27s}{'':>6} | {'cl/noise/dbcv':>22} | {'cl/noise/dbcv':>22}"
          f" | {'cl/noise/dbcv':>22} | {'cl/noise/dbcv':>22}")
    print("-" * 124)

    for intent in intents:
        rows = by_intent[intent]
        n = len(rows)
        raw = embed([" ".join(r["steps_taken"]) for r in rows]).astype("float64")

        raw_trials = _trials(raw, n, None)
        # UMAP arm sweeps n_neighbors exactly as v2 does, so B and D see one grid.
        um_trials: list[dict] = []
        for nn in sorted({max(2, min(k, n - 1)) for k in N_NEIGHBORS_GRID}):
            um_trials += _trials(_umap(raw, 10, nn, _SEEDS[0]), n, nn)

        cells = {
            "A_raw_v1rule": select_trial(raw_trials),
            "B_umap_v1rule": select_trial(um_trials),
            "C_raw_dbcv": select_by_dbcv(raw_trials),
            "D_umap_dbcv": select_by_dbcv(um_trials),
        }
        report["ablation"][intent] = {k: _pub(v) for k, v in cells.items()}

        def _c(k: str) -> str:
            t = cells[k]
            if t is None:
                return f"{'-':>22}"
            dv = "-" if t["dbcv"] is None else f"{t['dbcv']:+.3f}"
            return f"{t['n_clusters']:>6} /{t['n_noise']:>5} /{dv:>8}"

        print(
            f"{intent:27s}{n:>6} | {_c('A_raw_v1rule')} | {_c('B_umap_v1rule')}"
            f" | {_c('C_raw_dbcv')} | {_c('D_umap_dbcv')}"
        )

    # --- Q2: seed noise floor -------------------------------------------------
    print("\n" + "=" * 124)
    print("Q2 — NOISE FLOOR: how much does DBCV move when only the UMAP seed changes?")
    print("=" * 124)
    print(f"{'intent':27s}{'mcs':>6}{'nn':>5} | {'DBCV per seed':>46} | {'spread':>8}{'stdev':>8}"
          f"{'clusters':>12}")
    print("-" * 124)

    for intent in _PROBE:
        rows = by_intent[intent]
        n = len(rows)
        raw = embed([" ".join(r["steps_taken"]) for r in rows]).astype("float64")
        grid = mcs_grid(n)
        # A coarse and a fine mcs — the two ends the argmax was choosing between.
        for mcs in (grid[0], grid[len(grid) // 2], grid[-1]):
            vals, ks = [], []
            for sd in _SEEDS:
                sp = _umap(raw, 10, 15, sd)
                lab = _hdbscan(sp, mcs)
                s = dbcv(sp, lab)
                vals.append(s["dbcv"])
                ks.append(len({int(v) for v in lab if v >= 0}))
            ok = [v for v in vals if v is not None]
            spread = (max(ok) - min(ok)) if len(ok) > 1 else 0.0
            sd_ = statistics.stdev(ok) if len(ok) > 1 else 0.0
            report["noise_floor"].setdefault(intent, []).append(
                {
                    "mcs": mcs,
                    "n_neighbors": 15,
                    "dbcv_per_seed": ok,
                    "spread": round(spread, 4),
                    "stdev": round(sd_, 4),
                    "clusters_per_seed": ks,
                }
            )
            shown = " ".join(f"{v:+.3f}" if v is not None else "  -   " for v in vals)
            print(
                f"{intent:27s}{mcs:>6}{15:>5} | {shown:>46} | {spread:>8.4f}{sd_:>8.4f}"
                f"{str(ks):>12}"
            )

    # --- Q2b: put the observed argmax margins next to that floor -------------
    v2 = json.loads(
        (_ROOT / "data" / "mining" / "stage3_v2_test" / "clusters_v2.json").read_text(
            encoding="utf-8"
        )
    )
    print("\n" + "=" * 124)
    print("Q2b — MARGIN vs FLOOR: was the winning trial actually distinguishable?")
    print("=" * 124)
    print(f"{'intent':27s}{'won @cl':>9}{'DBCV':>9} | {'finest >=8 cl':>14}{'DBCV':>9}"
          f"{'margin':>9} | {'seed floor':>11}  verdict")
    print("-" * 124)
    for intent in _PROBE:
        sw = [t for t in v2[intent]["sweep"] if t["dbcv"] is not None]
        best = max(sw, key=lambda t: t["dbcv"])
        fine = [t for t in sw if t["n_clusters"] >= 8]
        floor = max(e["spread"] for e in report["noise_floor"][intent])
        if not fine:
            continue
        bf = max(fine, key=lambda t: t["dbcv"])
        margin = best["dbcv"] - bf["dbcv"]
        verdict = "INDISTINGUISHABLE" if margin < floor else "real"
        report["margin_vs_floor"][intent] = {
            "winner_clusters": best["n_clusters"],
            "winner_dbcv": best["dbcv"],
            "best_fine_clusters": bf["n_clusters"],
            "best_fine_dbcv": bf["dbcv"],
            "margin": round(margin, 4),
            "seed_floor": round(floor, 4),
            "verdict": verdict,
        }
        print(
            f"{intent:27s}{best['n_clusters']:>9}{best['dbcv']:>9.4f} | "
            f"{bf['n_clusters']:>14}{bf['dbcv']:>9.4f}{margin:>9.4f} | {floor:>11.4f}  {verdict}"
        )

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote -> {_OUT}")


if __name__ == "__main__":
    main()
