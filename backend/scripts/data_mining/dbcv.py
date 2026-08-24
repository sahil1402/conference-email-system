"""DBCV — Density-Based Clustering Validation (Moulavi et al., SDM 2014).

WHY THIS FILE EXISTS (and why we did not just use a library):

  1. ``sklearn.cluster.HDBSCAN`` does NOT expose ``relative_validity_``. Checked
     directly against the installed sklearn 1.9.0: a fitted estimator carries
     only ``labels_``, ``probabilities_``, ``n_features_in_``. That attribute
     belongs to the standalone ``hdbscan`` (McInnes) package, not to sklearn.
  2. There is no ``dbcv`` distribution on PyPI (``dbcv`` / ``DBCV`` /
     ``dbcv-metric`` all resolve to nothing).
  3. ``hdbscan.HDBSCAN(gen_min_span_tree=True).relative_validity_`` IS available
     and IS used here as an independent cross-check, but it can only score the
     labelling that estimator itself just produced. The point of this exercise
     is an apples-to-apples comparison against v1's HUMAN-EDITED clusters
     (merged, split, reverted) — a label vector no estimator ever emitted.
     Scoring an arbitrary ``(X, labels)`` pair is therefore a hard requirement,
     and only a standalone implementation can do it.

DEFINITION IMPLEMENTED (paper, section 4):

  all-points-core-distance of x in cluster C (|C| = n, dimensionality d):
      coredist(x) = ( sum_{y in C, y != x} (1 / dist(x,y))**d / (n - 1) ) ** (-1/d)

  mutual reachability:
      mreach(x, y) = max( coredist(x), coredist(y), dist(x, y) )

  DSC(C_i)  = max edge weight of the mreach-MST over C_i, restricted to INTERNAL
              edges (both endpoints of MST degree > 1), per the paper's
              definition of density sparseness.
  DSPC(i,j) = min mreach between an internal node of C_i and one of C_j.
  V_C(C_i)  = ( min_j DSPC(i,j) - DSC(i) ) / max( min_j DSPC(i,j), DSC(i) )
  DBCV      = sum_i (|C_i| / |O|) * V_C(C_i)          in [-1, 1]

NUMERICAL NOTE — the (1/dist)**d term is the whole difficulty at d = 384. A
direct evaluation overflows to inf or underflows to 0 for essentially every
pair, so the core distance is computed in LOG SPACE via logsumexp:
    log coredist(x) = -(1/d) * ( logsumexp_y( -d * log dist(x,y) ) - log(n-1) )
This is exact, not an approximation.

CONSEQUENCE WORTH KNOWING (a property of the metric, not a bug): as d grows,
logsumexp is dominated by its largest term, so coredist converges to the
point's NEAREST-NEIGHBOUR distance and DBCV degenerates toward a single-linkage
criterion. A 384-dim DBCV is therefore far less discriminative than a 10-dim
one. This is exactly why scores from different spaces must never be compared to
each other, and why the v1-vs-v2 comparison scores BOTH labellings in the SAME
space (see stage3_compare_v1_v2.py).

NOISE HANDLING — two numbers, deliberately:
  ``dbcv``          : |O| = ALL points, so unclustered (label -1) points and
                      singleton clusters contribute 0 validity. This is the
                      FAITHFUL Moulavi definition and is what selection uses:
                      being inherently coverage-penalised, it cannot be gamed by
                      a high-mcs run that discards most of the data.
  ``dbcv_clustered``: |O| = clustered points only. Pure cluster geometry with
                      coverage factored out. Reported for diagnosis only.
"""

from __future__ import annotations

import numpy as np
from scipy.sparse.csgraph import minimum_spanning_tree
from scipy.special import logsumexp

# Distance floor. Identical step-strings do occur (canned chair replies), and
# log(0) would poison the whole cluster's core distances.
_EPS = 1e-10


def _pairwise(vectors: np.ndarray) -> np.ndarray:
    """Euclidean distance matrix via the Gram identity, in O(n^2) memory.

    The obvious ``vectors[:, None, :] - vectors[None, :, :]`` materialises an
    n x n x d array — 5.7 GB for one 1367-ticket intent at d=384, which is the
    difference between this running and thrashing. ||x-y||^2 = ||x||^2 + ||y||^2
    - 2 x.y needs only n x n, and the clip absorbs the small negative values
    the subtraction produces on the diagonal.
    """
    sq = np.einsum("ij,ij->i", vectors, vectors)
    d2 = sq[:, None] + sq[None, :] - 2.0 * (vectors @ vectors.T)
    np.maximum(d2, 0.0, out=d2)
    dist = np.sqrt(d2, out=d2)
    np.fill_diagonal(dist, 0.0)
    return dist


def _core_distances(d_sub: np.ndarray, dim: int) -> np.ndarray:
    """All-points-core-distance for every member of one cluster, in log space."""
    n = d_sub.shape[0]
    if n < 2:
        return np.zeros(n, dtype="float64")
    t = -dim * np.log(np.clip(d_sub, _EPS, None))
    np.fill_diagonal(t, -np.inf)  # enforce y != x
    log_mean = logsumexp(t, axis=1) - np.log(n - 1)
    return np.exp(-log_mean / dim)


def _mst_structure(mreach_sub: np.ndarray) -> tuple[float, np.ndarray]:
    """(DSC, internal-node mask) for one cluster from its mreach-MST.

    Falls back to the full MST / all nodes when every node is a leaf — which is
    every cluster of size 2, and any perfect star — because the paper's
    internal-edge restriction is empty there.
    """
    n = mreach_sub.shape[0]
    if n < 2:
        return 0.0, np.ones(n, dtype=bool)
    mst = minimum_spanning_tree(mreach_sub).toarray()
    rows, cols = np.nonzero(mst)
    if rows.size == 0:  # all-zero distances (exact duplicates)
        return 0.0, np.ones(n, dtype=bool)
    deg = np.zeros(n, dtype=int)
    np.add.at(deg, rows, 1)
    np.add.at(deg, cols, 1)
    internal = deg > 1
    edge_w = mst[rows, cols]
    internal_edge = internal[rows] & internal[cols]
    if internal_edge.any():
        return float(edge_w[internal_edge].max()), internal
    return float(edge_w.max()), np.ones(n, dtype=bool)


def dbcv(
    vectors: np.ndarray,
    labels: np.ndarray,
    *,
    dim: int | None = None,
    noise_label: int = -1,
) -> dict:
    """Score an ARBITRARY labelling of ``vectors``. Never raises on degenerate input.

    Returns ``{dbcv, dbcv_clustered, n_clusters, n_noise, per_cluster}``.
    ``dbcv`` is None when the index is undefined (fewer than two non-singleton
    clusters — DSPC needs a second cluster to separate from).
    """
    vectors = np.asarray(vectors, dtype="float64")
    labels = np.asarray(labels)
    n_total = len(labels)
    dim = int(dim if dim is not None else vectors.shape[1])

    uniq = [int(u) for u in np.unique(labels) if int(u) != noise_label]
    members = {u: np.flatnonzero(labels == u) for u in uniq}
    # Singletons have no internal density; they are one-offs, scored like noise.
    scorable = [u for u in uniq if len(members[u]) >= 2]
    n_noise = int(np.sum(labels == noise_label))

    base: dict = {
        "n_clusters": len(uniq),
        "n_noise": n_noise,
        "per_cluster": {},
        "dbcv": None,
        "dbcv_clustered": None,
    }
    if len(scorable) < 2 or n_total == 0:
        return base

    dist = _pairwise(vectors)

    coredist = np.zeros(n_total, dtype="float64")
    for u in scorable:
        idx = members[u]
        coredist[idx] = _core_distances(dist[np.ix_(idx, idx)], dim)

    mreach = np.maximum(dist, np.maximum(coredist[:, None], coredist[None, :]))
    del dist

    dsc: dict[int, float] = {}
    internal: dict[int, np.ndarray] = {}
    for u in scorable:
        idx = members[u]
        d_i, mask = _mst_structure(mreach[np.ix_(idx, idx)])
        dsc[u] = d_i
        internal[u] = idx[mask]

    per: dict[int, dict] = {}
    for u in scorable:
        min_dspc = np.inf
        for v in scorable:
            if v == u:
                continue
            block = mreach[np.ix_(internal[u], internal[v])]
            min_dspc = min(min_dspc, float(block.min()))
        denom = max(min_dspc, dsc[u])
        v_c = 0.0 if denom <= 0 else (min_dspc - dsc[u]) / denom
        per[u] = {
            "size": int(len(members[u])),
            "v_c": round(float(v_c), 4),
            "dsc": round(dsc[u], 4),
            "min_dspc": round(float(min_dspc), 4),
        }

    n_clustered = sum(len(members[u]) for u in uniq)
    score_all = sum(per[u]["v_c"] * per[u]["size"] for u in scorable) / n_total
    score_cl = sum(per[u]["v_c"] * per[u]["size"] for u in scorable) / max(1, n_clustered)

    base.update(
        {
            "dbcv": round(float(score_all), 4),
            "dbcv_clustered": round(float(score_cl), 4),
            "per_cluster": per,
        }
    )
    return base
