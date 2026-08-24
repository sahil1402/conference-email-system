"""Compare Stage 3 v1 (production, human-reviewed) against v2 (UMAP + DBCV).

READ-ONLY on both artefacts. Writes a report to
``data/mining/stage3_v2_test/comparison.json`` and prints the tables.

APPLES-TO-APPLES IS THE WHOLE POINT, so two things are done carefully:

  1. BOTH labellings are scored in the SAME space, twice over:
       - raw 384-dim MiniLM: the space v1 actually clustered in, and the only
         space that is neutral between the two methods.
       - v2's chosen UMAP space: v2's home turf. v1's labels are scored there
         too, so v2 does not get to be graded on a ruler v1 never saw.
     Cross-space score comparison is meaningless (DBCV's core-distance term is
     dimensionality-dependent — see dbcv.py), so no table ever puts a 10-dim
     number next to a 384-dim one.

  2. v1's labels are taken from the FINAL, HUMAN-EDITED clusters.json — merges
     applied, desk_reject_appeal split, reviewer_assignment reverted. That is
     the artefact v2 has to beat, not the raw v1 machine output.

Alignment: both label vectors are built over the row order produced by
``load_joined``, keyed on ticket_id, so index i means the same ticket in both.
Any ticket absent from a file's clusters is label -1 (noise) there.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

warnings.filterwarnings("ignore")

from dbcv import dbcv  # noqa: E402
from stage3_cluster import _STAGE1, _STAGE2, embed, load_joined  # noqa: E402
from stage3_cluster_v2 import reduce_umap  # noqa: E402

_V1 = _ROOT / "data" / "mining" / "stage3_full" / "clusters.json"
_V2 = _ROOT / "data" / "mining" / "stage3_v2_test" / "clusters_v2.json"
_OUT = _ROOT / "data" / "mining" / "stage3_v2_test" / "comparison.json"


def labels_from(spec: dict, ticket_index: dict[int, int], n: int) -> np.ndarray:
    """Cluster spec -> label vector aligned to load_joined row order."""
    lab = np.full(n, -1, dtype=int)
    for c in spec["clusters"]:
        for t in c["ticket_ids"]:
            i = ticket_index.get(t)
            if i is not None:
                lab[i] = c["cluster_id"]
    return lab


# --- ground truth: the four already-settled human calls ----------------------
# Located by (intent, cluster_id) in the FINAL v1 file, each verified against the
# pre-decision snapshots so the identification is not a guess:
#   RSH c0  == premerge c0 (440) + c1 (366), the approved outage merge
#   RSH c4  == premerge c7 (30), the troubleshooting cluster deliberately NOT merged
#   RA  c1  == emergency-reviewer invites; RA c0 == the ~46% mixed residual
#   DRA c1/c10/c3 == the upheld / reversed / triage three-way split
GROUND_TRUTH = {
    "outage": ("review_submission_help", 0),
    "troubleshoot": ("review_submission_help", 4),
    "ra_emergency": ("reviewer_assignment", 1),
    "ra_residual": ("reviewer_assignment", 0),
    "dra_upheld": ("desk_reject_appeal", 1),
    "dra_reversed": ("desk_reject_appeal", 10),
    "dra_triage": ("desk_reject_appeal", 3),
}


def dominant(target: set[int], v2_spec: dict) -> dict:
    """Where did a v1 ground-truth set go in v2?"""
    spread = []
    for c in v2_spec["clusters"]:
        ov = len(target & set(c["ticket_ids"]))
        if ov:
            spread.append(
                {
                    "v2_cluster": c["cluster_id"],
                    "v2_size": c["size"],
                    "overlap": ov,
                    "share_of_target": round(ov / len(target), 3),
                    "purity_of_v2_cluster": round(ov / c["size"], 3),
                }
            )
    spread.sort(key=lambda s: -s["overlap"])
    clustered = sum(s["overlap"] for s in spread)
    return {
        "target_size": len(target),
        "to_v2_noise": len(target) - clustered,
        "n_v2_clusters_touched": len(spread),
        "n_v2_clusters_holding_ge5pct": sum(1 for s in spread if s["share_of_target"] >= 0.05),
        "top": spread[:6],
    }


def main() -> None:
    v1 = json.loads(_V1.read_text(encoding="utf-8"))
    v2 = json.loads(_V2.read_text(encoding="utf-8"))
    by_intent = load_joined(_STAGE1, _STAGE2)

    from sklearn.metrics import adjusted_mutual_info_score, adjusted_rand_score

    report: dict = {"per_intent": {}, "validation": {}}
    rows_out = []

    for intent in sorted(by_intent, key=lambda k: len(by_intent[k]), reverse=True):
        rows = by_intent[intent]
        n = len(rows)
        idx = {r["ticket_id"]: i for i, r in enumerate(rows)}
        raw = embed([" ".join(r["steps_taken"]) for r in rows]).astype("float64")

        l1 = labels_from(v1[intent], idx, n)
        l2 = labels_from(v2[intent], idx, n)

        # Space 1: raw 384-dim, neutral ground.
        s1_raw = dbcv(raw, l1)
        s2_raw = dbcv(raw, l2)

        # Space 2: v2's chosen UMAP space. v1's labels are scored here too.
        um = v2[intent].get("umap")
        if um:
            reduced, _ = reduce_umap(raw, um["n_components"], um["n_neighbors"])
            s1_um = dbcv(reduced, l1)
            s2_um = dbcv(reduced, l2)
        else:
            s1_um = s2_um = {"dbcv": None, "dbcv_clustered": None}

        rec = {
            "n": n,
            "v1": {
                "method": v1[intent]["method"],
                "mcs": v1[intent]["min_cluster_size"],
                "clusters": len(v1[intent]["clusters"]),
                "noise": int((l1 == -1).sum()),
                "coverage": round(1 - (l1 == -1).mean(), 3),
                "dbcv_raw384": s1_raw["dbcv"],
                "dbcv_raw384_clustered": s1_raw["dbcv_clustered"],
                "dbcv_umap": s1_um["dbcv"],
            },
            "v2": {
                "method": v2[intent]["method"],
                "mcs": v2[intent]["min_cluster_size"],
                "n_neighbors": (um or {}).get("n_neighbors"),
                "clusters": len(v2[intent]["clusters"]),
                "noise": int((l2 == -1).sum()),
                "coverage": round(1 - (l2 == -1).mean(), 3),
                "dbcv_raw384": s2_raw["dbcv"],
                "dbcv_raw384_clustered": s2_raw["dbcv_clustered"],
                "dbcv_umap": s2_um["dbcv"],
                "hdbscan_relative_validity": v2[intent].get("hdbscan_relative_validity"),
            },
            "agreement": {
                "adjusted_rand": round(float(adjusted_rand_score(l1, l2)), 3),
                "adjusted_mutual_info": round(float(adjusted_mutual_info_score(l1, l2)), 3),
            },
        }
        report["per_intent"][intent] = rec
        rows_out.append((intent, rec))

    # --- printed comparison table -------------------------------------------
    print("\n" + "=" * 132)
    print("COMPARISON TABLE — v1 (production, human-reviewed) vs v2 (UMAP + DBCV)")
    print("=" * 132)
    print(
        f"{'intent':27s}{'n':>6} | {'v1 cl':>5}{'v1 noi':>7}{'v1 cov':>7}{'v1 DBCV':>9}"
        f" | {'v2 cl':>5}{'v2 noi':>7}{'v2 cov':>7}{'v2 DBCV':>9} | {'ARI':>6}{'AMI':>6}"
    )
    print(
        f"{'':27s}{'':>6} | {'':>5}{'':>7}{'':>7}{'(384d)':>9}"
        f" | {'':>5}{'':>7}{'':>7}{'(384d)':>9} | {'':>6}{'':>6}"
    )
    print("-" * 132)
    for intent, r in rows_out:
        a, b = r["v1"], r["v2"]
        print(
            f"{intent:27s}{r['n']:>6} | {a['clusters']:>5}{a['noise']:>7}{a['coverage']:>7.0%}"
            f"{_f(a['dbcv_raw384']):>9} | {b['clusters']:>5}{b['noise']:>7}{b['coverage']:>7.0%}"
            f"{_f(b['dbcv_raw384']):>9} | {r['agreement']['adjusted_rand']:>6.3f}"
            f"{r['agreement']['adjusted_mutual_info']:>6.3f}"
        )

    print("\n" + "=" * 132)
    print("SAME TABLE, SCORED IN v2's OWN UMAP SPACE (v2's home turf; v1 labels scored there too)")
    print("=" * 132)
    print(f"{'intent':27s}{'n':>6} | {'v1 DBCV(umap)':>15}{'v2 DBCV(umap)':>15}{'delta':>9}"
          f" | {'v2 relvalid':>12}  note")
    print("-" * 132)
    for intent, r in rows_out:
        a, b = r["v1"]["dbcv_umap"], r["v2"]["dbcv_umap"]
        d = f"{b - a:+.3f}" if (a is not None and b is not None) else "-"
        note = "raw-space agglomerative (no UMAP)" if r["v2"]["method"].endswith("raw") else ""
        print(
            f"{intent:27s}{r['n']:>6} | {_f(a):>15}{_f(b):>15}{d:>9}"
            f" | {_f(r['v2']['hdbscan_relative_validity']):>12}  {note}"
        )

    # --- validation checks ---------------------------------------------------
    gt: dict[str, set[int]] = {}
    for key, (intent, cid) in GROUND_TRUTH.items():
        cl = next(c for c in v1[intent]["clusters"] if c["cluster_id"] == cid)
        gt[key] = set(cl["ticket_ids"])

    print("\n" + "=" * 132)
    print("VALIDATION vs FOUR SETTLED HUMAN DECISIONS")
    print("=" * 132)

    checks: dict[str, dict] = {}

    # 1 + 2: review_submission_help outage merge, and troubleshooting kept separate
    out_d = dominant(gt["outage"], v2["review_submission_help"])
    tro_d = dominant(gt["troubleshoot"], v2["review_submission_help"])
    checks["1_outage_merge"] = out_d
    checks["2_troubleshoot_separate"] = tro_d
    out_top = out_d["top"][0]["v2_cluster"] if out_d["top"] else None
    tro_top = tro_d["top"][0]["v2_cluster"] if tro_d["top"] else None
    checks["2_troubleshoot_separate"]["collided_with_outage_cluster"] = bool(
        out_top is not None and out_top == tro_top
    )

    print("\n[1] review_submission_help — the two outage clusters (440+366) humans merged into n=806")
    _print_dom(out_d)
    print("\n[2] review_submission_help — troubleshooting cluster (n=30) humans kept SEPARATE")
    _print_dom(tro_d)
    print(f"    lands in the same v2 cluster as the outage set? "
          f"{checks['2_troubleshoot_separate']['collided_with_outage_cluster']}")

    # 3: reviewer_assignment — did UMAP separate the ~46% mixture?
    ra_rows = by_intent["reviewer_assignment"]
    emerg = {
        r["ticket_id"]
        for r in ra_rows
        if "emergency" in (" ".join(r["steps_taken"]) + " " + str(r.get("what_was_asked"))).lower()
    }
    res_d = dominant(gt["ra_residual"], v2["reviewer_assignment"])
    eme_d = dominant(gt["ra_emergency"], v2["reviewer_assignment"])
    # Emergency purity of every v2 cluster that holds a chunk of the residual.
    frac = []
    for s in res_d["top"]:
        c = next(c for c in v2["reviewer_assignment"]["clusters"] if c["cluster_id"] == s["v2_cluster"])
        ids = set(c["ticket_ids"])
        frac.append(
            {
                "v2_cluster": c["cluster_id"],
                "v2_size": c["size"],
                "from_residual": s["overlap"],
                "emergency_fraction_of_v2_cluster": round(len(ids & emerg) / c["size"], 3),
            }
        )
    checks["3_reviewer_assignment_mixture"] = {
        "v1_residual": res_d,
        "v1_emergency": eme_d,
        "v1_residual_emergency_fraction": round(len(gt["ra_residual"] & emerg) / len(gt["ra_residual"]), 3),
        "v1_emergency_cluster_emergency_fraction": round(len(gt["ra_emergency"] & emerg) / len(gt["ra_emergency"]), 3),
        "v2_destination_emergency_fractions": frac,
        "emergency_keyword_total": len(emerg),
    }
    print("\n[3] reviewer_assignment — does UMAP split the ~46% mixed residual (v1 c0, n=180)?")
    print(f"    keyword 'emergency' present in {len(emerg)}/{len(ra_rows)} tickets of this intent")
    print(f"    v1 residual  (n={len(gt['ra_residual'])}) emergency fraction = "
          f"{checks['3_reviewer_assignment_mixture']['v1_residual_emergency_fraction']:.0%}")
    print(f"    v1 emergency (n={len(gt['ra_emergency'])}) emergency fraction = "
          f"{checks['3_reviewer_assignment_mixture']['v1_emergency_cluster_emergency_fraction']:.0%}")
    _print_dom(res_d, indent="    ")
    print("    emergency purity of each v2 cluster the residual landed in:")
    for f in frac:
        print(f"      v2 c{f['v2_cluster']:<3} size={f['v2_size']:>4} "
              f"gained {f['from_residual']:>4} residual tickets -> "
              f"emergency = {f['emergency_fraction_of_v2_cluster']:.0%}")

    # 4: desk_reject_appeal — three opposite-outcome groups must not merge
    tri = {k: dominant(gt[k], v2["desk_reject_appeal"]) for k in ("dra_upheld", "dra_reversed", "dra_triage")}
    tops = {k: (v["top"][0]["v2_cluster"] if v["top"] else None) for k, v in tri.items()}
    collapsed = len({t for t in tops.values() if t is not None}) < len([t for t in tops.values() if t is not None])
    checks["4_desk_reject_three_way"] = {
        "per_group": tri,
        "dominant_v2_cluster": tops,
        "collapsed_into_one": collapsed,
    }
    print("\n[4] desk_reject_appeal — the three-way split (upheld / reversed / triage)")
    for k in ("dra_upheld", "dra_reversed", "dra_triage"):
        print(f"    {k.replace('dra_', ''):<10}", end="")
        _print_dom(tri[k], indent="", inline=True)
    print(f"    dominant v2 cluster per group: {tops}")
    print(f"    collapsed into one v2 cluster? {collapsed}")

    report["validation"] = checks
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote -> {_OUT}")


def _f(v) -> str:
    return "-" if v is None else f"{v:+.3f}"


def _print_dom(d: dict, indent: str = "    ", inline: bool = False) -> None:
    head = (
        f"n={d['target_size']:>4} -> {d['n_v2_clusters_touched']} v2 clusters "
        f"({d['n_v2_clusters_holding_ge5pct']} holding >=5%), {d['to_v2_noise']} to noise"
    )
    if inline:
        top = d["top"][0] if d["top"] else None
        extra = (
            f" | dominant v2 c{top['v2_cluster']} takes {top['share_of_target']:.0%} "
            f"(purity {top['purity_of_v2_cluster']:.0%})"
            if top
            else ""
        )
        print(head + extra)
        return
    print(indent + head)
    for s in d["top"]:
        print(
            f"{indent}  v2 c{s['v2_cluster']:<3} size={s['v2_size']:>5} overlap={s['overlap']:>5} "
            f"({s['share_of_target']:.0%} of target, purity {s['purity_of_v2_cluster']:.0%})"
        )


if __name__ == "__main__":
    main()
