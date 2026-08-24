"""Stage 3 v2 — UMAP reduction + DBCV-selected clustering. PARALLEL TEST ONLY.

Writes to ``data/mining/stage3_v2_test/clusters_v2.json``. Reads, but NEVER
writes, anything under ``data/mining/stage3_full/`` — the human-reviewed v1
clusters are the production artefact and this script must not touch them.

Same inputs, same embeddings, same tickets as v1 (``load_joined`` and ``embed``
are imported from stage3_cluster, not reimplemented) so the only variables are
the two changes below.


CHANGE 1 — UMAP BEFORE CLUSTERING
---------------------------------
v1 ran HDBSCAN directly on 384-dim MiniLM vectors. Density estimation in 384
dimensions is the documented cause of reviewer_assignment's residual staying a
~46% mixture even after isolated re-clustering: at that dimensionality every
point is roughly equidistant from every other, so there is no density contrast
left for HDBSCAN to cut on. UMAP is a density-PRESERVING reduction (unlike PCA,
which preserves variance), so reducing first is the standard remedy.

  n_components = 10  (the task's starting point, kept — see MEASURED below)
  n_neighbors  = SWEPT {5, 15, 30}, because this is the parameter that actually
                 trades local against global structure, and the right value is
                 intent-dependent.
  min_dist     = 0.0, the correct setting when the output feeds a clusterer
                 rather than a plot: it lets UMAP pack points as tightly as the
                 topology allows instead of spreading them for legibility.
  metric       = cosine, matching the space the embeddings actually live in.
  random_state = 42, so the run is reproducible (UMAP drops to single-threaded
                 when seeded; accepted, this is a one-off local job).

Pure local CPU compute. No API call, no credential, no spend.

MEASURED, on n_components: 10 was kept after checking 5 / 10 / 15 / 25 on the
three largest intents (``--probe-ncomp``). Best DBCV moved by only 0.05-0.13
across that whole range — below this pipeline's own noise floor (see MEASURED
OUTCOME below), so nothing in the sweep beat 10 by an amount that means
anything. 10 stands as the task's starting point, not as a tuned optimum.


CHANGE 2 — DBCV REPLACES THE HAND-ROLLED SELECTION RULE
-------------------------------------------------------
v1's ``select_trial`` was, in sequence: "longest plateau wins", then corrected
to "lowest noise, plateau as tie-break". Both are proxies invented because
nothing was measuring cluster quality directly. The failure mode is on record:
the first version picked mcs=48 on reviewer_assignment (2 clusters, 62% noise)
over mcs=18 (4 clusters, 54% noise) purely because two swept values happened to
agree on "2 clusters" — a stability artefact, not structure.

v2 selects the trial with the HIGHEST DBCV (Moulavi et al. 2014), a real
density-based validity index. Rationale, matching how the prior fix was
documented:

  - Noise is no longer a separate criterion, because it is already inside the
    metric. The faithful DBCV weights each cluster by |C_i| / |O| over the WHOLE
    intent, so unclustered points contribute exactly 0. A high-mcs run that
    discards 62% of the data cannot win on a technicality — it is arithmetically
    penalised. This is why "coverage first" could be dropped rather than kept as
    a tie-break.
  - Plateau length is gone entirely. It was a proxy for "this structure is
    real"; DBCV measures that directly, as within-cluster density against
    between-cluster separation.
  - Ties are broken toward FEWER clusters, then LARGER mcs — the same
    conservative preference v1 ended on, retained deliberately.

v1's DOCUMENTED REMAINING LIMITATION IS ALSO FIXED HERE. v1 could not sweep the
n >= 200 tier: candidates were proportional to n, so 0.03 * 597 = 18 meant
mcs=15 was never tried on a large intent, which forced a hard-coded LARGE_MCS.
With a real quality metric there is no reason to keep the tier: v2 sweeps a grid
that UNIONS absolute values {5..30} with proportional ones, at every n >= 30, and
lets DBCV choose. The size tiers collapse from three to two.

Where sklearn stands on this (task item 2, checked not assumed): sklearn 1.9.0's
``HDBSCAN`` does NOT expose ``relative_validity_`` — a fitted estimator carries
only ``labels_``, ``probabilities_``, ``n_features_in_``. No ``dbcv`` package
exists on PyPI either. Hence the local implementation in ``dbcv.py``, which is
required regardless: only a standalone scorer can grade v1's human-edited
labelling, which no estimator ever emitted. ``hdbscan.relative_validity_`` (the
McInnes package, which does have it) is recorded alongside the chosen trial as
an INDEPENDENT CROSS-CHECK of our implementation, never as the selector.


MEASURED OUTCOME — READ THIS BEFORE ADOPTING ANY OF IT
------------------------------------------------------
This pipeline ran on all 14 intents and was then validated against the four
settled human decisions (stage3_compare_v1_v2.py), ablated (stage3_v2_ablation.py)
and probed (stage3_v2_residual_probe.py). The verdict is NOT "v2 is better":

  * THE DBCV ARGMAX IS SELECTING ON SEED NOISE, so change 2 above fails on its
    own terms. Refitting UMAP under five random seeds at a FIXED (n_neighbors,
    mcs) moves DBCV by 0.19-0.49. The margins the argmax actually decided on
    were 0.0045 (reviewer_assignment: 2 clusters at 0.5463 beating 21 clusters
    at 0.5418) and 0.0083 (desk_reject_appeal: 2 clusters beating 13) — forty
    to fifty times SMALLER than the seed spread. reviewer_assignment's winning
    trial scores +0.546 on seed 42 and +0.315 on seed 123; the 2-cluster result
    is a lucky draw, not a finding. This is the SAME pathology as v1's original
    plateau rule (an arbitrary tie-break presented as a criterion), which is
    the one thing this rewrite was supposed to eliminate.
  * DBCV MEASURED IN UMAP SPACE IS CIRCULAR. UMAP with min_dist=0 manufactures
    compact well-separated blobs, so it inflates any density index computed on
    its own output (+0.61 to +1.44 over v1's labels in that space). Scored back
    in the neutral raw 384-dim space, v2's partitions are NEGATIVE on 11 of 12
    intents where v1's human-reviewed ones are POSITIVE on 10 of 12. The
    apparent win does not survive leaving UMAP's coordinate system.
  * ABLATION: the coarsening comes from the SPACE, not the metric. With v1's
    rule held fixed, UMAP drops review_submission_help from 24 clusters to 3
    and pushes noise to 0 nearly everywhere. With the space held raw, DBCV
    argmax goes the other way and picks FINER partitions than v1's rule
    (reviewer_assignment 4 -> 17). The two changes push in opposite directions
    and were confounded by shipping them together.
  * WHERE UMAP GENUINELY WINS — and it does, decisively, on exactly the defect
    it was brought in for. Decomposing reviewer_assignment's residual in
    isolation: raw 384-dim yields a 136-ticket blob at 61% emergency-reviewer
    (reproducing the documented ~46% mixture that isolated re-clustering could
    not split), while UMAP splits the same 180 tickets into 67 at 10% and 65 at
    92%. That is confirmed by a purity metric INDEPENDENT of both DBCV and
    UMAP, so unlike the headline numbers it is not circular.

Conclusion carried into the report: keep UMAP as a targeted decomposition tool
for stubborn residual clusters; do NOT adopt DBCV argmax as the selection rule,
and do not adopt this pipeline wholesale over the human-reviewed v1.


WHAT IS DELIBERATELY UNCHANGED
------------------------------
  - n < 30 stays raw-space agglomerative. UMAP has no density to preserve at
    n=11, and LINK_THRESHOLD=0.35 is a COSINE distance calibrated on unit
    embeddings — it is meaningless against UMAP coordinates. Reducing here would
    change the method without measuring anything. Affects author_profile_
    compliance (29) and paper_bidding (11), which v1 also handled this way.
  - Noise recovery (v1 refinement 2) still runs, in the reduced space.
  - Merge flagging (v1 refinement 3) still runs, and still computes centroid
    similarity in the RAW 384-dim space, because MERGE_SIMILARITY=0.85 was
    calibrated there. Scoring it against UMAP centroids would silently change
    the threshold's meaning.
  - min_samples=1, as in v1, so the space and the selector are the only moving
    parts.
  - Nothing is auto-merged. Candidates are flagged for a human, exactly as v1.

Usage:
    python scripts/data_mining/stage3_cluster_v2.py                  # all 14
    python scripts/data_mining/stage3_cluster_v2.py --probe-ncomp    # n_components study
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from collections import Counter
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

warnings.filterwarnings("ignore")

from app.core.config import settings  # noqa: E402

from dbcv import dbcv  # noqa: E402
from stage3_cluster import (  # noqa: E402
    _STAGE1,
    _STAGE2,
    LINK_THRESHOLD,
    MERGE_SIMILARITY,
    RECOVERY_MIN_MCS,
    SMALL_N,
    _agglomerative,
    _hdbscan,
    embed,
    load_joined,
)

_OUT = _ROOT / "data" / "mining" / "stage3_v2_test" / "clusters_v2.json"

N_COMPONENTS = 10
N_NEIGHBORS_GRID = (5, 15, 30)
UMAP_SEED = 42
UMAP_MIN_DIST = 0.0
UMAP_METRIC = "cosine"


# --- reduction ---------------------------------------------------------------
def reduce_umap(
    vectors: np.ndarray, n_components: int = N_COMPONENTS, n_neighbors: int = 15
) -> tuple[np.ndarray, dict]:
    """UMAP to a low-dimensional density-preserving space.

    Both parameters are CLAMPED against n: UMAP needs n_neighbors < n, and
    spectral initialisation needs n_components < n - 1. Silently exceeding
    either raises or falls back to a random init, so clamp explicitly and record
    what was actually used.
    """
    import umap

    n = len(vectors)
    n_comp = max(2, min(n_components, n - 2))
    n_neigh = max(2, min(n_neighbors, n - 1))
    reduced = umap.UMAP(
        n_components=n_comp,
        n_neighbors=n_neigh,
        min_dist=UMAP_MIN_DIST,
        metric=UMAP_METRIC,
        random_state=UMAP_SEED,
    ).fit_transform(np.asarray(vectors, dtype="float32"))
    return np.asarray(reduced, dtype="float64"), {
        "n_components": int(n_comp),
        "n_neighbors": int(n_neigh),
        "min_dist": UMAP_MIN_DIST,
        "metric": UMAP_METRIC,
        "random_state": UMAP_SEED,
    }


# --- selection ---------------------------------------------------------------
def mcs_grid(n: int) -> list[int]:
    """Candidate min_cluster_size values: absolute UNION proportional.

    v1 used proportional values only, which is what made mcs=15 untriable on a
    597-ticket intent (0.03 * 597 = 18) and forced the fixed LARGE_MCS tier.
    Unioning in absolute values removes that blind spot; DBCV can be trusted to
    reject the bad ones, which is the whole reason a real metric was needed.
    """
    prop = {int(round(n * f)) for f in (0.03, 0.05, 0.08, 0.12)}
    absolute = {5, 8, 10, 12, 15, 20, 25, 30}
    hi = max(3, n // 3)
    return sorted({max(3, min(m, hi)) for m in (prop | absolute)})


def select_by_dbcv(trials: list[dict]) -> dict | None:
    """Highest DBCV wins. REPLACES v1's coverage-first/plateau rule.

    Ties (identical rounded DBCV) break toward FEWER clusters, then LARGER mcs —
    the simpler, more conservative structure, which is the preference v1 also
    settled on. Trials that found no clusters, or whose DBCV is undefined (fewer
    than two non-singleton clusters), are not eligible.
    """
    usable = [t for t in trials if t["n_clusters"] > 0 and t["dbcv"] is not None]
    if not usable:
        return None
    return sorted(usable, key=lambda t: (-t["dbcv"], t["n_clusters"], -t["mcs"]))[0]


def _relative_validity(vectors: np.ndarray, mcs: int) -> float | None:
    """hdbscan (McInnes) relative_validity_ — INDEPENDENT cross-check of our DBCV.

    Never used for selection. Reported so the two implementations can be
    compared on the chosen trial; sklearn's HDBSCAN has no such attribute, which
    is why this second package is consulted only here.
    """
    try:
        import hdbscan as _h

        n = len(vectors)
        fit = _h.HDBSCAN(
            min_cluster_size=max(2, min(mcs, n)), min_samples=1, gen_min_span_tree=True
        ).fit(vectors)
        return round(float(fit.relative_validity_), 4)
    except Exception:  # noqa: BLE001 - research script, cross-check only
        return None


# --- per-intent pipeline -----------------------------------------------------
def cluster_intent_v2(rows: list[dict], raw: np.ndarray) -> dict:
    """UMAP + DBCV-selected HDBSCAN + noise recovery + merge flagging, one intent."""
    n = len(rows)

    # --- small tier: unchanged from v1, raw space (see module docstring) ------
    if n < SMALL_N:
        labels = _agglomerative(raw, LINK_THRESHOLD)
        return _assemble(
            rows,
            raw,
            raw,
            labels,
            method="agglomerative_fallback_raw",
            mcs=0,
            umap_params=None,
            sweep=[],
            recovered=0,
        )

    # --- sweep (n_neighbors x mcs), scored by DBCV in the reduced space ------
    sweep: list[dict] = []
    spaces: dict[int, tuple[np.ndarray, dict]] = {}

    for n_neigh in sorted({max(2, min(k, n - 1)) for k in N_NEIGHBORS_GRID}):
        reduced, params = reduce_umap(raw, N_COMPONENTS, n_neigh)
        spaces[n_neigh] = (reduced, params)
        for mcs in mcs_grid(n):
            labels = _hdbscan(reduced, mcs)
            counts = Counter(labels.tolist())
            n_noise = counts.pop(-1, 0)
            score = dbcv(reduced, labels)
            trial = {
                "n_neighbors": n_neigh,
                "mcs": mcs,
                "n_clusters": len(counts),
                "n_noise": int(n_noise),
                "dbcv": score["dbcv"],
                "dbcv_clustered": score["dbcv_clustered"],
            }
            sweep.append(trial)

    # Selection is a single pass over the recorded sweep — no running "best", so
    # the persisted sweep and the chosen trial provably come from one rule.
    chosen = select_by_dbcv(sweep)
    if chosen is None:
        # No trial produced a scorable partition — fall back rather than report nothing.
        labels = _agglomerative(raw, LINK_THRESHOLD)
        return _assemble(
            rows,
            raw,
            raw,
            labels,
            method="agglomerative_fallback_raw",
            mcs=0,
            umap_params=None,
            sweep=sweep,
            recovered=0,
        )

    reduced, params = spaces[chosen["n_neighbors"]]
    labels = _hdbscan(reduced, chosen["mcs"])

    # --- v1 refinement 2: recovery pass over noise only, in reduced space ----
    noise_idx = [i for i, lab in enumerate(labels) if lab == -1]
    recovered = 0
    if len(noise_idx) >= RECOVERY_MIN_MCS * 2:
        rec_mcs = max(RECOVERY_MIN_MCS, chosen["mcs"] // 2)
        sub = _hdbscan(reduced[noise_idx], rec_mcs)
        next_lab = int(labels.max()) + 1
        for j, lab in enumerate(sub):
            if lab >= 0:
                labels[noise_idx[j]] = next_lab + int(lab)
        recovered = len({int(v) for v in sub if v >= 0})

    return _assemble(
        rows,
        raw,
        reduced,
        labels,
        method="umap_hdbscan_dbcv",
        mcs=chosen["mcs"],
        umap_params=params,
        sweep=sweep,
        recovered=recovered,
        rel_validity=_relative_validity(reduced, chosen["mcs"]),
    )


def _assemble(
    rows: list[dict],
    raw: np.ndarray,
    space: np.ndarray,
    labels: np.ndarray,
    *,
    method: str,
    mcs: int,
    umap_params: dict | None,
    sweep: list[dict],
    recovered: int,
    rel_validity: float | None = None,
) -> dict:
    """Group, flag candidate merges, and score — shared by every branch."""
    groups: dict[int, list[int]] = {}
    for i, lab in enumerate(labels):
        groups.setdefault(int(lab), []).append(i)
    noise_idx = groups.pop(-1, [])
    ordered = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)

    # Renumber so cluster_id is positional-by-size, matching v1's convention.
    final = np.full(len(rows), -1, dtype=int)
    for new_id, (_old, idxs) in enumerate(ordered):
        final[idxs] = new_id

    # v1 refinement 3: centroid similarity in the RAW space, where 0.85 was set.
    merges: list[dict] = []
    if len(ordered) > 1:
        cents = []
        for _old, idxs in ordered:
            v = raw[idxs].mean(axis=0)
            cents.append(v / max(1e-12, float(np.linalg.norm(v))))
        sim = np.vstack(cents) @ np.vstack(cents).T
        for a in range(len(ordered)):
            for b in range(a + 1, len(ordered)):
                if sim[a, b] >= MERGE_SIMILARITY:
                    merges.append(
                        {"candidate_merge": [a, b], "similarity": round(float(sim[a, b]), 3)}
                    )

    score_space = dbcv(space, final)
    score_raw = dbcv(raw, final)

    def _example(j: int) -> dict:
        return {
            "ticket_id": rows[j]["ticket_id"],
            "what_was_asked": rows[j]["what_was_asked"],
            "steps_taken": rows[j]["steps_taken"],
        }

    return {
        "n_tickets": len(rows),
        "method": method,
        "min_cluster_size": mcs,
        "umap": umap_params,
        "sweep": sweep,
        "n_primary": len(ordered) - recovered,
        "n_recovered": recovered,
        "n_noise": len(noise_idx),
        "dbcv_cluster_space": score_space["dbcv"],
        "dbcv_cluster_space_clustered": score_space["dbcv_clustered"],
        "dbcv_raw_384d": score_raw["dbcv"],
        "dbcv_raw_384d_clustered": score_raw["dbcv_clustered"],
        "hdbscan_relative_validity": rel_validity,
        "clusters": [
            {
                "cluster_id": i,
                "size": len(idxs),
                "label": "",
                "ticket_ids": [rows[j]["ticket_id"] for j in idxs],
                "examples": [_example(j) for j in idxs[:3]],
            }
            for i, (_old, idxs) in enumerate(ordered)
        ],
        "candidate_merges": merges,
        "noise_examples": [_example(j) for j in noise_idx[:5]],
    }


# --- n_components probe ------------------------------------------------------
def probe_ncomp(by_intent: dict[str, list[dict]], intents: list[str]) -> None:
    """Justify n_components=10 rather than asserting it (task item 1)."""
    print("\nn_components probe — best DBCV over the mcs grid, n_neighbors=15\n")
    print(f"{'intent':28s} " + "".join(f"{f'nc={c}':>12}" for c in (5, 10, 15, 25)))
    for intent in intents:
        rows = by_intent[intent]
        raw = embed([" ".join(r["steps_taken"]) for r in rows])
        cells = []
        for nc in (5, 10, 15, 25):
            reduced, _ = reduce_umap(raw, nc, 15)
            best = -2.0
            for mcs in mcs_grid(len(rows)):
                s = dbcv(reduced, _hdbscan(reduced, mcs))
                if s["dbcv"] is not None:
                    best = max(best, s["dbcv"])
            cells.append(f"{best:>12.4f}")
        print(f"{intent:28s} " + "".join(cells))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--intent", action="append", help="restrict to this intent (repeatable)")
    ap.add_argument("--stage1", type=Path, default=_STAGE1)
    ap.add_argument("--stage2", type=Path, default=_STAGE2)
    ap.add_argument("--out", type=Path, default=_OUT)
    ap.add_argument(
        "--probe-ncomp", action="store_true", help="n_components study on the 3 largest intents"
    )
    args = ap.parse_args()

    if "stage3_full" in str(args.out):
        raise SystemExit("refusing to write into stage3_full/ — v1 is the production artefact")

    by_intent = load_joined(args.stage1, args.stage2)
    if args.intent:
        missing = [i for i in args.intent if i not in by_intent]
        if missing:
            raise SystemExit(f"unknown intent(s): {missing}")
        by_intent = {i: by_intent[i] for i in args.intent}

    order = sorted(by_intent, key=lambda k: len(by_intent[k]), reverse=True)

    if args.probe_ncomp:
        probe_ncomp(by_intent, order[:3])
        return

    print(f"\nembedding model: {settings.FAISS_MODEL_NAME} (local CPU, no API, no spend)")
    print(f"UMAP: n_components={N_COMPONENTS} n_neighbors grid={N_NEIGHBORS_GRID} "
          f"min_dist={UMAP_MIN_DIST} metric={UMAP_METRIC} seed={UMAP_SEED}")
    print("selection: highest DBCV (faithful, noise-inclusive)\n")

    data: dict[str, dict] = {}
    for intent in order:
        rows = by_intent[intent]
        t0 = time.time()
        raw = embed([" ".join(r["steps_taken"]) for r in rows])
        res = cluster_intent_v2(rows, raw)
        data[intent] = res
        print(
            f"{intent:28s} n={res['n_tickets']:>5}  {res['method']:26s} "
            f"mcs={res['min_cluster_size']:>3} nn={(res['umap'] or {}).get('n_neighbors', '-'):>3} "
            f"clusters={len(res['clusters']):>2} noise={res['n_noise']:>4} "
            f"dbcv={res['dbcv_cluster_space']} relval={res['hdbscan_relative_validity']} "
            f"({time.time() - t0:.0f}s)",
            flush=True,
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote -> {args.out}")


if __name__ == "__main__":
    main()
