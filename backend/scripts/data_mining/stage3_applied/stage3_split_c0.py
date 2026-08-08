# ONE-SHOT SCRIPT — already run against the clusters.json state at the time.
# Re-running will fail its own assertions, since it locates clusters by size/
# membership that no longer exist after this script applied its changes.
# Kept as an audit trail of what was manually applied during Stage 3, not as
# reusable tooling.

"""Decompose reviewer_assignment's residual c0 (n=301) into sub-clusters.

c0 came from the recovery pass and absorbed several procedures. Re-clustered in
ISOLATION so density is estimated over these 301 points alone rather than over
the whole 597-ticket intent.

TIER JUDGEMENT (deliberate deviation from the n>=200 rule): the size tiers were
calibrated on whole intents. c0's members are, by construction, exactly the points
that already failed density at mcs=15 in the parent context, so the tier value does
not transfer. Measured in isolation:

    mcs=15 (tier rule) -> 2 clusters, 149 noise (50%)   <- poor
    mcs=12 (coverage-first select_trial) -> 2 clusters, 55 noise (18%)
    mcs=5  -> 6 clusters, 81 noise (27%)                <- chosen

mcs=5 is used because the GOAL here is decomposition, and the coverage-first rule
optimises the wrong thing for this job: its pick leaves a single 233-ticket blob
(77% of c0) and surfaces one small tail. mcs=5 extracts five procedurally coherent
tails that mcs=12 buries. The cost is honest and reported: 81 points return to the
intent's noise pool.

NOTHING IS FOLDED INTO c1. The task anticipated moving emergency-reviewer
sub-clusters into c1, but no sub-cluster qualifies: the large core is only ~46%
emergency-reviewer tickets and the five clean tails are 0%. Only mcs=3 begins to
isolate emergency-heavy pockets (40 at 80%, 10 at 100%) and it costs 14 clusters
at 41% noise. The 0.906 centroid similarity between the core and c1 is the same
misleading signal already identified: it reflects the emergency-reviewer fraction
pulling the centroid, not the cluster matching c1's procedure.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stage1_extract import load_mining_api_key  # noqa: E402
from stage3_cluster import (  # noqa: E402
    _STAGE1,
    _STAGE2,
    MERGE_SIMILARITY,
    _hdbscan,
    embed,
    label_all,
    load_joined,
)

_FILE = _ROOT / "data" / "mining" / "stage3_full" / "clusters.json"
SUB_MCS = 5
INTENT = "reviewer_assignment"


def main() -> None:
    data = json.loads(_FILE.read_text(encoding="utf-8"))
    section = data[INTENT]
    rows = {r["ticket_id"]: r for r in load_joined(_STAGE1, _STAGE2)[INTENT]}
    print()

    c0 = next(c for c in section["clusters"] if c["size"] == 301 and c["recovered_from_noise"])
    ids = c0["ticket_ids"]
    vecs = embed([" ".join(rows[t]["steps_taken"]) for t in ids])
    labels = _hdbscan(vecs, SUB_MCS)

    groups: dict[int, list[int]] = {}
    for i, lab in enumerate(labels):
        groups.setdefault(int(lab), []).append(i)
    new_noise = groups.pop(-1, [])
    ordered = sorted(groups.values(), key=len, reverse=True)

    section["clusters"] = [c for c in section["clusters"] if c is not c0]
    for k, idxs in enumerate(ordered):
        member_ids = [ids[i] for i in idxs]
        section["clusters"].append(
            {
                "cluster_id": -1,
                "size": len(member_ids),
                "recovered_from_noise": True,
                "split_from": "c0 (n=301, recovery-pass residual)",
                "split_rank": k,
                "label": "",
                "ticket_ids": member_ids,
                "examples": [
                    {
                        "ticket_id": t,
                        "what_was_asked": rows[t]["what_was_asked"],
                        "steps_taken": rows[t]["steps_taken"],
                    }
                    for t in member_ids[:3]
                ],
            }
        )
    section["n_noise"] += len(new_noise)
    section["c0_split_note"] = (
        f"c0 (n=301) re-clustered in isolation at mcs={SUB_MCS} -> {len(ordered)} sub-clusters; "
        f"{len(new_noise)} members returned to noise. Nothing folded into the "
        f"emergency-reviewer cluster: no sub-cluster is procedurally equivalent to it "
        f"(largest core is ~46% emergency-reviewer, the clean tails are 0%)."
    )
    print(f"c0 (301) -> {len(ordered)} sub-clusters {[len(g) for g in ordered]}, "
          f"{len(new_noise)} -> noise")

    # renumber
    section["clusters"].sort(key=lambda c: c["size"], reverse=True)
    for i, c in enumerate(section["clusters"]):
        c["cluster_id"] = i
    section["n_primary"] = sum(1 for c in section["clusters"] if not c["recovered_from_noise"])
    section["n_recovered"] = sum(1 for c in section["clusters"] if c["recovered_from_noise"])

    # label only the new, unlabelled sub-clusters
    todo = {INTENT: {"clusters": [c for c in section["clusters"] if not c["label"]]}}
    if todo[INTENT]["clusters"]:
        print(f"labelling {len(todo[INTENT]['clusters'])} new sub-clusters...")
        asyncio.run(label_all(todo, load_mining_api_key()))

    # recompute flags for this intent only
    all_rows = load_joined(_STAGE1, _STAGE2)[INTENT]
    v_all = embed([" ".join(r["steps_taken"]) for r in all_rows])
    index = {r["ticket_id"]: k for k, r in enumerate(all_rows)}
    cents = []
    for c in section["clusters"]:
        idxs = [index[t] for t in c["ticket_ids"] if t in index]
        v = v_all[idxs].mean(axis=0)
        cents.append(v / max(1e-12, float(np.linalg.norm(v))))
    sim = np.vstack(cents) @ np.vstack(cents).T
    section["candidate_merges"] = [
        {"candidate_merge": [a, b], "similarity": round(float(sim[a, b]), 3)}
        for a in range(len(section["clusters"]))
        for b in range(a + 1, len(section["clusters"]))
        if sim[a, b] >= MERGE_SIMILARITY
    ]

    _FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    n, noise = section["n_tickets"], section["n_noise"]
    print(f"\ncoverage: {n - noise}/{n} = {100 * (n - noise) / n:.0f}%  (noise {noise})")
    print(f"wrote -> {_FILE}")


if __name__ == "__main__":
    main()
