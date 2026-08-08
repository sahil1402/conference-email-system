# ONE-SHOT SCRIPT — already run against the clusters.json state at the time.
# Re-running will fail its own assertions, since it locates clusters by size/
# membership that no longer exist after this script applied its changes.
# Kept as an audit trail of what was manually applied during Stage 3, not as
# reusable tooling.

"""Apply reviewed Stage 3 decisions: revert one intent, split one cluster, merge two.

Deliberately narrow and idempotent-by-identification: target clusters are located
by TICKET MEMBERSHIP, not by cluster_id, because ids are positional and were
renumbered when the earlier six merges were applied. Locating by id here would
silently hit the wrong cluster.

  1. reviewer_assignment REVERTED to the pre-resweep mcs=15 + recovery result
     (6 primary + 2 recovered, 79 noise / 87% coverage). The mcs=48 resweep is
     discarded: it explained 56% of the intent where the original explained 87%.
  2. desk_reject_appeal c8 (n=30, recovered) SPLIT three ways — inspection showed
     it held three procedures with opposite outcomes (appeal denied vs. rejection
     reversed) plus a different requester type entirely (SPC/reviewer triage).
  3. review_submission_help c0 + c1 MERGED (n=806): both are the same September
     OpenReview outage procedure. c7 is deliberately NOT merged despite similarity
     flags — it is individual access troubleshooting with no deadline extension.

candidate_merges are recomputed at the end (centroids move). Informational only.
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
    embed,
    load_joined,
)

_FILE = _ROOT / "data" / "mining" / "stage3_full" / "clusters.json"
_PREMERGE = _ROOT / "data" / "mining" / "stage3_full" / "clusters_premerge.json"

# --- desk_reject_appeal c8 three-way split -----------------------------------
# Provenance matters here: the first 18 ids are the ones surfaced in the review
# report. The remaining 12 were NOT in those example lists and were assigned by
# reading their steps_taken; they are marked so the call can be audited.
SPLIT_GROUPS: dict[str, dict] = {
    "upheld": {
        "label": "Review the appeal against the applicable policy, uphold the desk "
        "rejection as final, and clarify it does not reflect the work's quality.",
        "reported": [15795, 15852, 16675, 19407, 19860, 18427],
        "assigned_by_inspection": [15591, 15749, 15781, 15785, 15946, 18512, 18608, 19540, 21060],
    },
    "reversed": {
        "label": "Investigate the desk rejection, find it was applied in error or "
        "outweighed, and reinstate the submission into the review phase.",
        "reported": [16377, 18876, 18980, 18985],
        "assigned_by_inspection": [],
    },
    "triage": {
        "label": "Answer an SPC/reviewer asking whether a paper should be desk-rejected: "
        "judge whether the formatting breach is material and direct them to proceed or reject.",
        "reported": [16156, 16423, 16444, 16513, 16639, 16649, 18169, 19868],
        "assigned_by_inspection": [16239, 16496, 18250],
    },
}

MERGE_RSH_LABEL = (
    "Handle the OpenReview outage: confirm the platform was down, refuse review "
    "submission by email, extend the deadline without penalty, and give access "
    "troubleshooting guidance."
)


def _find(section: dict, size: int, recovered: bool | None = None) -> dict:
    """Locate a cluster by size (+ optional recovered flag), asserting uniqueness."""
    hits = [
        c
        for c in section["clusters"]
        if c["size"] == size and (recovered is None or c["recovered_from_noise"] is recovered)
    ]
    if len(hits) != 1:
        raise SystemExit(f"expected exactly 1 cluster of size {size}, found {len(hits)}")
    return hits[0]


def _renumber(section: dict) -> None:
    section["clusters"].sort(key=lambda c: c["size"], reverse=True)
    for i, c in enumerate(section["clusters"]):
        c["cluster_id"] = i
    section["n_primary"] = sum(1 for c in section["clusters"] if not c["recovered_from_noise"])
    section["n_recovered"] = sum(1 for c in section["clusters"] if c["recovered_from_noise"])


def recompute_flags(section: dict, vectors: np.ndarray, index: dict[int, int]) -> list[dict]:
    clusters = section["clusters"]
    if len(clusters) < 2:
        return []
    cents = []
    for c in clusters:
        idxs = [index[t] for t in c["ticket_ids"] if t in index]
        v = vectors[idxs].mean(axis=0)
        cents.append(v / max(1e-12, float(np.linalg.norm(v))))
    sim = np.vstack(cents) @ np.vstack(cents).T
    return [
        {"candidate_merge": [a, b], "similarity": round(float(sim[a, b]), 3)}
        for a in range(len(clusters))
        for b in range(a + 1, len(clusters))
        if sim[a, b] >= MERGE_SIMILARITY
    ]


def main() -> None:
    data = json.loads(_FILE.read_text(encoding="utf-8"))
    pre = json.loads(_PREMERGE.read_text(encoding="utf-8"))
    rows_by_intent = load_joined(_STAGE1, _STAGE2)
    print()

    # --- 1. revert reviewer_assignment --------------------------------------
    data["reviewer_assignment"] = json.loads(json.dumps(pre["reviewer_assignment"]))
    ra = data["reviewer_assignment"]
    ra.pop("resweep_note", None)
    ra["revert_note"] = (
        "Reverted to the original mcs=15 + noise-recovery result. The mcs=48 resweep "
        "was discarded: it explained 56% of the intent vs 87% here. The selection bug "
        "that produced it is fixed in stage3_cluster.select_trial (coverage first)."
    )
    print(
        f"1. reverted reviewer_assignment -> {ra['n_primary']} primary + "
        f"{ra['n_recovered']} recovered, noise={ra['n_noise']} "
        f"({100 * (ra['n_tickets'] - ra['n_noise']) / ra['n_tickets']:.0f}% coverage)"
    )

    # --- 2. split desk_reject_appeal c8 --------------------------------------
    dra = data["desk_reject_appeal"]
    c8 = _find(dra, size=30, recovered=True)
    members = set(c8["ticket_ids"])
    ext = {r["ticket_id"]: r for r in rows_by_intent["desk_reject_appeal"]}

    planned = {
        name: g["reported"] + g["assigned_by_inspection"] for name, g in SPLIT_GROUPS.items()
    }
    flat = [t for ids in planned.values() for t in ids]
    if len(flat) != len(set(flat)):
        raise SystemExit("split groups overlap")
    if set(flat) != members:
        raise SystemExit(
            f"split does not partition c8: missing={sorted(members - set(flat))} "
            f"extra={sorted(set(flat) - members)}"
        )

    dra["clusters"] = [c for c in dra["clusters"] if c is not c8]
    for name, g in SPLIT_GROUPS.items():
        ids = planned[name]
        dra["clusters"].append(
            {
                "cluster_id": -1,
                "size": len(ids),
                "recovered_from_noise": True,
                "split_from": "c8 (n=30, recovered)",
                "split_group": name,
                "assigned_by_inspection": g["assigned_by_inspection"],
                "label": g["label"],
                "ticket_ids": ids,
                "examples": [
                    {
                        "ticket_id": t,
                        "what_was_asked": ext[t]["what_was_asked"],
                        "steps_taken": ext[t]["steps_taken"],
                    }
                    for t in ids[:3]
                ],
            }
        )
        print(f"2. split c8 -> {name}: n={len(ids)}")

    # --- 3. merge review_submission_help c0 + c1 -----------------------------
    rsh = data["review_submission_help"]
    a, b = _find(rsh, 440), _find(rsh, 366)
    merged = {
        "cluster_id": -1,
        "size": a["size"] + b["size"],
        "recovered_from_noise": False,
        "merged_from_sizes": [a["size"], b["size"]],
        "label": MERGE_RSH_LABEL,
        "ticket_ids": a["ticket_ids"] + b["ticket_ids"],
        "examples": (a["examples"] + b["examples"])[:3],
    }
    rsh["clusters"] = [c for c in rsh["clusters"] if c not in (a, b)]
    rsh["clusters"].append(merged)
    print(f"3. merged review_submission_help 440+366 -> n={merged['size']} (c7/n=30 kept separate)")

    # --- 4. renumber + recompute flags ---------------------------------------
    for intent in ("reviewer_assignment", "desk_reject_appeal", "review_submission_help"):
        section = data[intent]
        _renumber(section)
        rows = rows_by_intent[intent]
        vecs = embed([" ".join(r["steps_taken"]) for r in rows])
        index = {r["ticket_id"]: k for k, r in enumerate(rows)}
        section["candidate_merges"] = recompute_flags(section, vecs, index)

    _FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote -> {_FILE}")


if __name__ == "__main__":
    main()
