# ONE-SHOT SCRIPT — already run against the clusters.json state at the time.
# Re-running will fail its own assertions, since it locates clusters by size/
# membership that no longer exist after this script applied its changes.
# Kept as an audit trail of what was manually applied during Stage 3, not as
# reusable tooling.

"""Apply human-approved cluster merges and re-sweep one intent.

Deliberately narrow: applies ONLY the six merges reviewed and approved by hand,
and re-clusters ONLY reviewer_assignment. Nothing else is split, merged, or
relabelled — desk_reject_appeal c8 and the review_submission_help c0/c1/c7 trio
are left exactly as-is pending review.

Traceability: every merged cluster records ``merged_from`` (the original
cluster_ids) so the operation is auditable and reversible from the Stage 3
inputs. Because cluster_ids are positional, they are renumbered after merging —
``merged_from`` is what preserves the link to the pre-merge file.

candidate_merges are RECOMPUTED afterwards (centroids move when clusters
combine, and the old flags reference stale ids). Recomputing flags is not the
same as acting on them: nothing is auto-merged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stage3_cluster import (  # noqa: E402
    _STAGE1,
    _STAGE2,
    MERGE_SIMILARITY,
    RECOVERY_MIN_MCS,
    _hdbscan,
    _sweep_stable,
    embed,
    load_joined,
)

_FILE = _ROOT / "data" / "mining" / "stage3_full" / "clusters.json"

# (intent, [cluster_ids to combine], new label)
MERGES: list[tuple[str, list[int], str]] = [
    (
        "review_submission_help",
        [5, 6],
        "Guide SPCs through meta-review submission: proceed on the available reviews "
        "when enough exist, and advance or escalate papers below the minimum.",
    ),
    (
        "review_submission_help",
        [4, 12],
        "Explain that formal review submission is closed and route late feedback "
        "through the paper's comment system for the SPC to consider.",
    ),
    (
        "review_decision_appeal",
        [2, 5],
        "Explain that the final decision reflected the full reviewer/SPC/Area Chair "
        "process and direct the author to use the feedback for future submissions.",
    ),
    (
        "desk_reject_appeal",
        [5, 6],
        "Review the reconsideration request against the stated policy, uphold the "
        "desk rejection as final, and clarify it does not assess the work's quality.",
    ),
    (
        "submission_requirements",
        [1, 8],
        "Explain that the requested details or dates are not yet available and "
        "direct the requester to await a future announcement.",
    ),
    (
        "committee_invitation",
        [0, 6, 7],
        "Acknowledge a declined invitation or withdrawal, thank the person for "
        "notifying the organizers, and invite future participation.",
    ),
]

RESWEEP_INTENT = "reviewer_assignment"


def _centroids(vectors: np.ndarray, clusters: list[dict], index: dict[int, int]) -> np.ndarray:
    mats = []
    for c in clusters:
        idxs = [index[t] for t in c["ticket_ids"] if t in index]
        v = vectors[idxs].mean(axis=0)
        mats.append(v / max(1e-12, float(np.linalg.norm(v))))
    return np.vstack(mats)


def recompute_flags(vectors: np.ndarray, clusters: list[dict], index: dict[int, int]) -> list[dict]:
    if len(clusters) < 2:
        return []
    sim = _centroids(vectors, clusters, index) @ _centroids(vectors, clusters, index).T
    out = []
    for a in range(len(clusters)):
        for b in range(a + 1, len(clusters)):
            if sim[a, b] >= MERGE_SIMILARITY:
                out.append({"candidate_merge": [a, b], "similarity": round(float(sim[a, b]), 3)})
    return out


def main() -> None:
    data = json.loads(_FILE.read_text(encoding="utf-8"))
    by_intent = load_joined(_STAGE1, _STAGE2)
    print()

    touched: set[str] = set()

    # --- 1. apply approved merges -------------------------------------------
    for intent, ids, label in MERGES:
        section = data[intent]
        byid = {c["cluster_id"]: c for c in section["clusters"]}
        parts = [byid[i] for i in ids]
        combined = {
            "cluster_id": -1,  # renumbered below
            "size": sum(p["size"] for p in parts),
            # True only if EVERY source was recovered; a mixed merge is primary.
            "recovered_from_noise": all(p["recovered_from_noise"] for p in parts),
            "merged_from": list(ids),
            "label": label,
            "ticket_ids": [t for p in parts for t in p["ticket_ids"]],
            "examples": [e for p in parts for e in p["examples"]][:3],
        }
        section["clusters"] = [c for c in section["clusters"] if c["cluster_id"] not in ids]
        section["clusters"].append(combined)
        touched.add(intent)
        print(f"merged {intent} c{'+c'.join(map(str, ids))} -> n={combined['size']}")

    # --- 2. re-sweep reviewer_assignment ------------------------------------
    rows = by_intent[RESWEEP_INTENT]
    vecs = embed([" ".join(r["steps_taken"]) for r in rows])
    labels, mcs, sweep = _sweep_stable(vecs, len(rows))
    groups: dict[int, list[int]] = {}
    for i, lab in enumerate(labels):
        groups.setdefault(int(lab), []).append(i)
    noise = groups.pop(-1, [])
    ordered = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)

    # Refinement 2 applies to EVERY HDBSCAN intent, including a re-swept one.
    # Omitting it here would compare a swept primary pass against the original's
    # primary+recovery result — not a like-for-like comparison.
    recovered_groups: list[list[int]] = []
    if len(noise) >= RECOVERY_MIN_MCS * 2:
        rec_mcs = max(RECOVERY_MIN_MCS, (mcs or 15) // 2)
        sub_labels = _hdbscan(vecs[noise], rec_mcs)
        sub: dict[int, list[int]] = {}
        for j, lab in enumerate(sub_labels):
            sub.setdefault(int(lab), []).append(noise[j])
        noise = sub.pop(-1, [])
        recovered_groups = [
            idxs for _l, idxs in sorted(sub.items(), key=lambda kv: len(kv[1]), reverse=True)
        ]
        print(f"   recovery pass (mcs={rec_mcs}): +{len(recovered_groups)} clusters, noise -> {len(noise)}")
    data[RESWEEP_INTENT] = {
        "n_tickets": len(rows),
        "method": "hdbscan_swept",
        "min_cluster_size": mcs,
        "sweep": sweep,
        "n_primary": len(ordered),
        "n_recovered": len(recovered_groups),
        "n_noise": len(noise),
        "resweep_note": (
            "Re-clustered with the mid-tier mcs sweep after the fixed mcs=15 result "
            "flagged c0(73)/c6(301,recovered) at 0.889 — evidence one procedure had "
            "been split. Labels below are regenerated positionally; old ids do not carry over."
        ),
        "clusters": [
            {
                "cluster_id": i,
                "size": len(idxs),
                "recovered_from_noise": False,
                "label": "",
                "ticket_ids": [rows[j]["ticket_id"] for j in idxs],
                "examples": [
                    {
                        "ticket_id": rows[j]["ticket_id"],
                        "what_was_asked": rows[j]["what_was_asked"],
                        "steps_taken": rows[j]["steps_taken"],
                    }
                    for j in idxs[:3]
                ],
            }
            for i, (_l, idxs) in enumerate(ordered)
        ]
        + [
            {
                "cluster_id": len(ordered) + i,
                "size": len(idxs),
                "recovered_from_noise": True,
                "label": "",
                "ticket_ids": [rows[j]["ticket_id"] for j in idxs],
                "examples": [
                    {
                        "ticket_id": rows[j]["ticket_id"],
                        "what_was_asked": rows[j]["what_was_asked"],
                        "steps_taken": rows[j]["steps_taken"],
                    }
                    for j in idxs[:3]
                ],
            }
            for i, idxs in enumerate(recovered_groups)
        ],
        "candidate_merges": [],
        "noise_examples": [
            {
                "ticket_id": rows[j]["ticket_id"],
                "what_was_asked": rows[j]["what_was_asked"],
                "steps_taken": rows[j]["steps_taken"],
            }
            for j in noise[:5]
        ],
    }
    touched.add(RESWEEP_INTENT)
    print(f"\nresweep {RESWEEP_INTENT}: mcs={mcs} -> {len(ordered)} clusters, {len(noise)} noise")
    for t in sweep:
        print(f"   mcs={t['mcs']:>3} clusters={t['n_clusters']:>3} noise={t['n_noise']:>4}")

    # --- 3. renumber + recompute flags for touched intents -------------------
    for intent in touched:
        section = data[intent]
        section["clusters"].sort(key=lambda c: c["size"], reverse=True)
        for i, c in enumerate(section["clusters"]):
            c["cluster_id"] = i
        section["n_primary"] = sum(1 for c in section["clusters"] if not c["recovered_from_noise"])
        section["n_recovered"] = sum(1 for c in section["clusters"] if c["recovered_from_noise"])
        rows_i = by_intent[intent]
        vecs_i = embed([" ".join(r["steps_taken"]) for r in rows_i])
        index = {r["ticket_id"]: k for k, r in enumerate(rows_i)}
        section["candidate_merges"] = recompute_flags(vecs_i, section["clusters"], index)

    _FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote -> {_FILE}")


if __name__ == "__main__":
    main()
