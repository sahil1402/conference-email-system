"""Does UMAP fix the ONE problem it was brought in for? READ-ONLY, local, no spend.

The motivating defect: reviewer_assignment's residual cluster stayed a ~46%
emergency-reviewer mixture even after ``stage3_split_c0.py`` re-clustered it in
ISOLATION, which was diagnosed as a high-dimensionality symptom — at d=384 there
is no density contrast left for HDBSCAN to cut on. UMAP is the standard remedy
for exactly that, so the fair test of UMAP is not "does it improve the whole
pipeline" but "does it decompose THIS residual where raw space could not".

Same isolation logic as stage3_split_c0.py (density estimated over the residual
alone, not the parent intent), with the space as the only changed variable:

    raw 384-dim   vs   UMAP 10-dim,  identical mcs ladder, identical purity metric

Purity proxy: the token "emergency" in steps_taken / what_was_asked. Coarse, but
it is the same signal the manual review reasoned about ("only ~46% emergency-
reviewer tickets"), and it is applied identically to both arms, so the
COMPARISON is sound even where the absolute number is rough.
"""

from __future__ import annotations

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

from dbcv import dbcv  # noqa: E402
from stage3_cluster import _STAGE1, _STAGE2, _hdbscan, embed, load_joined  # noqa: E402
from stage3_cluster_v2 import reduce_umap  # noqa: E402

_V1 = _ROOT / "data" / "mining" / "stage3_full" / "clusters.json"
_OUT = _ROOT / "data" / "mining" / "stage3_v2_test" / "residual_probe.json"


def _emergency(rows: list[dict]) -> np.ndarray:
    return np.array(
        [
            "emergency"
            in (" ".join(r["steps_taken"]) + " " + str(r.get("what_was_asked"))).lower()
            for r in rows
        ]
    )


def run(space: np.ndarray, rows: list[dict], flag: np.ndarray, tag: str) -> list[dict]:
    out = []
    print(f"\n  --- {tag} ---")
    print(f"  {'mcs':>4}{'clusters':>10}{'noise':>7}{'DBCV':>9}   purity profile "
          f"(size@emergency%)")
    for mcs in (3, 5, 8, 12, 15, 20):
        if mcs >= len(rows):
            continue
        labels = _hdbscan(space, mcs)
        counts = Counter(labels.tolist())
        n_noise = counts.pop(-1, 0)
        prof = []
        for lab, _ in sorted(counts.items(), key=lambda kv: -kv[1]):
            m = labels == lab
            prof.append({"size": int(m.sum()), "emergency": round(float(flag[m].mean()), 3)})
        s = dbcv(space, labels)
        rec = {
            "mcs": mcs,
            "space": tag,
            "n_clusters": len(counts),
            "n_noise": int(n_noise),
            "dbcv": s["dbcv"],
            "profile": prof,
            # The decisive number: does ANY cluster isolate the emergency
            # procedure cleanly, at a size worth naming as a workflow?
            "best_clean_emergency": max(
                (p["size"] for p in prof if p["emergency"] >= 0.85), default=0
            ),
            "best_clean_nonemergency": max(
                (p["size"] for p in prof if p["emergency"] <= 0.15), default=0
            ),
        }
        out.append(rec)
        shown = " ".join(f"{p['size']}@{p['emergency']:.0%}" for p in prof[:8])
        dv = "-" if s["dbcv"] is None else f"{s['dbcv']:+.3f}"
        print(f"  {mcs:>4}{len(counts):>10}{n_noise:>7}{dv:>9}   {shown}")
    return out


def main() -> None:
    v1 = json.loads(_V1.read_text(encoding="utf-8"))
    by_intent = load_joined(_STAGE1, _STAGE2)
    rows_all = by_intent["reviewer_assignment"]
    idx = {r["ticket_id"]: i for i, r in enumerate(rows_all)}

    residual_ids = next(
        c["ticket_ids"] for c in v1["reviewer_assignment"]["clusters"] if c["cluster_id"] == 0
    )
    sel = [idx[t] for t in residual_ids if t in idx]
    rows = [rows_all[i] for i in sel]
    flag = _emergency(rows)

    print("=" * 100)
    print("RESIDUAL DECOMPOSITION PROBE — reviewer_assignment v1 c0, in isolation")
    print("=" * 100)
    print(f"residual n = {len(rows)}   emergency-keyword fraction = {flag.mean():.1%}"
          f"   (the ~46% mixture the manual review could not split)")

    raw = embed([" ".join(r["steps_taken"]) for r in rows]).astype("float64")
    res = {"n": len(rows), "emergency_fraction": round(float(flag.mean()), 3), "arms": []}

    res["arms"] += run(raw, rows, flag, "raw 384-dim (what v1 tried)")
    reduced, params = reduce_umap(raw, 10, 15)
    res["umap_params"] = params
    res["arms"] += run(reduced, rows, flag, "UMAP 10-dim")

    def _best(tag: str) -> tuple[int, int]:
        arm = [a for a in res["arms"] if a["space"].startswith(tag)]
        return (
            max((a["best_clean_emergency"] for a in arm), default=0),
            max((a["best_clean_nonemergency"] for a in arm), default=0),
        )

    re_, rn = _best("raw")
    ue, un = _best("UMAP")
    res["verdict"] = {
        "raw_largest_clean_emergency_cluster": re_,
        "raw_largest_clean_nonemergency_cluster": rn,
        "umap_largest_clean_emergency_cluster": ue,
        "umap_largest_clean_nonemergency_cluster": un,
        "umap_separates_better": bool(ue > re_),
    }
    print("\n" + "=" * 100)
    print("VERDICT — largest cluster that is cleanly one procedure (>=85% / <=15% emergency)")
    print("=" * 100)
    print(f"  raw 384-dim : emergency-pure {re_:>4}   non-emergency-pure {rn:>4}")
    print(f"  UMAP 10-dim : emergency-pure {ue:>4}   non-emergency-pure {un:>4}")
    print(f"  UMAP separates the mixture better? {res['verdict']['umap_separates_better']}")

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote -> {_OUT}")


if __name__ == "__main__":
    main()
