"""Manual-inspection dump for the ONE validated E012 finding. READ-ONLY, no spend.

E012 concluded: don't adopt the v2 pipeline, but DO use UMAP as a targeted
decomposition tool on stubborn residual clusters. This script produces the
evidence a human needs to accept or reject that decomposition for
reviewer_assignment's residual — real ticket content, not just purity numbers.

Reproduces the validation-test split EXACTLY (same isolation logic as
stage3_split_c0.py, same reduce_umap call as stage3_v2_residual_probe.py):

    v1 clusters.json -> reviewer_assignment cluster_id 0  (the residual, n=180)
    UMAP(n_components=10, n_neighbors=15, seed=42, min_dist=0, cosine)
    HDBSCAN(min_cluster_size=20, min_samples=1)

Then prints, per discovered group: size, emergency-keyword fraction, raw-space
centroid similarity to the EXISTING v1 c1 (the emergency-reviewer invite
cluster), and 5 real tickets with their full steps_taken. v1 c1 itself is
printed alongside so the fold-in question can be judged by reading both.

WRITES NOTHING to data/mining/stage3_full/. Decision-support only.

CENTROID-SIMILARITY CAVEAT, carried forward from stage3_split_c0.py: on this
exact pair, a 0.906 raw centroid similarity between the residual core and c1 was
already investigated and found MISLEADING — it reflected the emergency-reviewer
FRACTION pulling the centroid, not the cluster matching c1's procedure. The
number is reported here for continuity, but the ticket text is the evidence.

Usage:
    python scripts/data_mining/stage3_v2_residual_inspect.py
    python scripts/data_mining/stage3_v2_residual_inspect.py --examples 8
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from collections import Counter
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

warnings.filterwarnings("ignore")

from stage3_cluster import _STAGE1, _STAGE2, _hdbscan, embed, load_joined  # noqa: E402
from stage3_cluster_v2 import reduce_umap  # noqa: E402

_V1 = _ROOT / "data" / "mining" / "stage3_full" / "clusters.json"
_OUT = _ROOT / "data" / "mining" / "stage3_v2_test" / "residual_inspection.json"

RESIDUAL_CLUSTER_ID = 0  # v1 reviewer_assignment c0 — the mixed residual
EMERGENCY_CLUSTER_ID = 1  # v1 reviewer_assignment c1 — emergency-reviewer invites
MCS = 20
N_COMPONENTS = 10
N_NEIGHBORS = 15


def _is_emergency(row: dict) -> bool:
    blob = (" ".join(row["steps_taken"]) + " " + str(row.get("what_was_asked"))).lower()
    return "emergency" in blob


def _show(rows: list[dict], idxs: list[int], k: int, indent: str = "    ") -> list[dict]:
    """Print k tickets in full and return them for the JSON artefact.

    Deterministic spread rather than the first k: takes them evenly across the
    group so a long tail is represented, not just whichever landed first.
    """
    if not idxs:
        return []
    step = max(1, len(idxs) // k)
    picked = idxs[::step][:k]
    out = []
    for j in picked:
        r = rows[j]
        flag = "EMERGENCY-kw" if _is_emergency(r) else "no-kw"
        print(f"{indent}ticket {r['ticket_id']}  [{flag}]")
        print(f"{indent}  asked : {r['what_was_asked']}")
        for s in r["steps_taken"]:
            print(f"{indent}  step  - {s}")
        print()
        out.append(
            {
                "ticket_id": r["ticket_id"],
                "emergency_keyword": _is_emergency(r),
                "what_was_asked": r["what_was_asked"],
                "steps_taken": r["steps_taken"],
                "outcome_type": r.get("outcome_type"),
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--examples", type=int, default=5)
    args = ap.parse_args()

    v1 = json.loads(_V1.read_text(encoding="utf-8"))["reviewer_assignment"]
    rows_all = load_joined(_STAGE1, _STAGE2)["reviewer_assignment"]
    idx = {r["ticket_id"]: i for i, r in enumerate(rows_all)}

    def _cluster(cid: int) -> list[int]:
        ids = next(c["ticket_ids"] for c in v1["clusters"] if c["cluster_id"] == cid)
        return [idx[t] for t in ids if t in idx]

    res_idx = _cluster(RESIDUAL_CLUSTER_ID)
    c1_idx = _cluster(EMERGENCY_CLUSTER_ID)

    rows = [rows_all[i] for i in res_idx]
    flag = np.array([_is_emergency(r) for r in rows])

    print("=" * 100)
    print("STEP 1 — reproduce the validated split")
    print("=" * 100)
    print(f"residual  = v1 reviewer_assignment c{RESIDUAL_CLUSTER_ID}, n={len(rows)}, "
          f"emergency-keyword {flag.mean():.1%}")
    print(f"reference = v1 reviewer_assignment c{EMERGENCY_CLUSTER_ID}, n={len(c1_idx)} "
          f"(emergency-reviewer invites)")
    print(f"params    = UMAP(n_components={N_COMPONENTS}, n_neighbors={N_NEIGHBORS}, "
          f"seed=42) + HDBSCAN(mcs={MCS})")

    raw = embed([" ".join(r["steps_taken"]) for r in rows]).astype("float64")
    reduced, params = reduce_umap(raw, N_COMPONENTS, N_NEIGHBORS)
    labels = _hdbscan(reduced, MCS)

    counts = Counter(labels.tolist())
    n_noise = counts.pop(-1, 0)
    groups = sorted(counts, key=lambda lab: -counts[lab])

    # Raw-space centroid of v1 c1, for the fold-in similarity question.
    raw_all = embed([" ".join(r["steps_taken"]) for r in rows_all]).astype("float64")
    c1_cent = raw_all[c1_idx].mean(axis=0)
    c1_cent = c1_cent / max(1e-12, float(np.linalg.norm(c1_cent)))

    print(f"\nresult: {len(groups)} groups + {n_noise} noise")
    print(f"{'group':>7}{'size':>7}{'emergency%':>13}{'cos to v1 c1':>15}")
    print("-" * 46)
    profile = []
    for g in groups:
        m = labels == g
        cent = raw[m].mean(axis=0)
        cent = cent / max(1e-12, float(np.linalg.norm(cent)))
        sim = float(cent @ c1_cent)
        profile.append(
            {
                "group": int(g),
                "size": int(m.sum()),
                "emergency_fraction": round(float(flag[m].mean()), 3),
                "cosine_to_v1_c1": round(sim, 3),
            }
        )
        print(f"{g:>7}{int(m.sum()):>7}{flag[m].mean():>12.0%}{sim:>15.3f}")

    expected = [(67, 0.10), (65, 0.92), (20, 0.80), (20, 0.85)]
    got = sorted(((p["size"], p["emergency_fraction"]) for p in profile), reverse=True)
    print(f"\nexpected profile (validation test): {expected}")
    print(f"observed profile                  : {got}")
    match = len(got) == len(expected) and all(
        abs(a[0] - b[0]) <= 2 and abs(a[1] - b[1]) <= 0.05
        for a, b in zip(got, sorted(expected, reverse=True))
    )
    print(f"REPRODUCED: {match}")

    # --- step 2: real content ------------------------------------------------
    print("\n" + "=" * 100)
    print("STEP 2 — real ticket content, for the distinct-procedure judgement")
    print("=" * 100)
    dump: dict = {"params": params, "profile": profile, "groups": {}, "reproduced": bool(match)}

    for p in profile:
        g = p["group"]
        gi = [i for i in range(len(rows)) if labels[i] == g]
        print("\n" + "-" * 100)
        print(f"GROUP {g}  |  n={p['size']}  |  emergency-keyword {p['emergency_fraction']:.0%}"
              f"  |  cos to v1 c1 = {p['cosine_to_v1_c1']:.3f}")
        print("-" * 100)
        dump["groups"][f"group_{g}"] = {
            **p,
            "examples": _show(rows, gi, args.examples),
        }

    print("\n" + "=" * 100)
    print(f"REFERENCE — existing v1 c{EMERGENCY_CLUSTER_ID} (emergency-reviewer invites, "
          f"n={len(c1_idx)}), the proposed fold-in target")
    print("=" * 100)
    dump["v1_c1_reference"] = {
        "size": len(c1_idx),
        "label": next(
            c["label"] for c in v1["clusters"] if c["cluster_id"] == EMERGENCY_CLUSTER_ID
        ),
        "examples": _show(
            rows_all, c1_idx, args.examples, indent="    "
        ),
    }
    print(f"    v1 label: {dump['v1_c1_reference']['label']}")

    if n_noise:
        noise_i = [i for i in range(len(rows)) if labels[i] == -1]
        print("\n" + "=" * 100)
        print(f"NOISE — {n_noise} tickets returned to the intent's noise pool")
        print("=" * 100)
        dump["noise"] = {
            "size": int(n_noise),
            "emergency_fraction": round(float(flag[np.array(noise_i)].mean()), 3),
            "examples": _show(rows, noise_i, min(args.examples, n_noise)),
        }

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(dump, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote -> {_OUT}")
    print("NOTE: nothing under data/mining/stage3_full/ was modified.")


if __name__ == "__main__":
    main()
