# ONE-SHOT SCRIPT — already run against the clusters.json state at the time.
# Re-running will fail its own assertions, since it locates clusters by ticket
# membership that no longer exists after this script applied its changes.
# Kept as an audit trail of what was manually applied during Stage 3, not as
# reusable tooling.

"""Apply the E012 reviewer_assignment residual decomposition, with one hand correction.

Acts ONLY on reviewer_assignment. Nothing else in clusters.json is touched.

WHAT THIS APPLIES (reviewed by reading real steps_taken — see E012's
"Manual inspection of the one adopted finding"):

  UMAP(n_components=10, n_neighbors=15, seed=42) + HDBSCAN(mcs=20) on c0's 180
  members, in ISOLATION, yields 4 groups + 8 noise:

    group 1  n=65  92% emergency  cos 0.960 to c1  -> FOLDED INTO c1 (73 -> 138)
    group 2  n=67  10% emergency  cos 0.727        -> NEW cluster
    group 0  n=20  80% emergency  cos 0.806        -> NEW cluster, n=19 after the
                                                      17955 correction below
    group 3  n=20  85% emergency  cos 0.795        -> NEW cluster
    noise    n=8                                   -> returned to the noise pool

  Group 1 folds because the TEXT matches step-for-step, not because the centroid
  is close: its 16730 ("Invited Amish Sethi to serve as requested for paper
  29433 -> Confirmed publicly to the SPC") is c1's 17761 ("Invited Sayed to serve
  as the requested emergency PC member"). This is the INVERSE of the 0.906
  similarity stage3_split_c0.py correctly rejected — there the number came from
  emergency-keyword fraction while the procedures differed.

  Groups 2/0/3 stay separate. Group 2 in particular must NOT join c2 despite the
  shared reassignment topic: c2 predominantly DECLINES reassignment and requires
  completion, group 2 GRANTS it and routes to the assignments team — opposite
  outcomes, the same hazard as desk_reject_appeal's upheld/reversed split.

MANUAL CORRECTION (the one deviation from the machine split):
  ticket 17955 moves from group 0 to the "decline late emergency-reviewer
  requests" cluster. Its steps ("declined both reviewer-addition requests because
  it was too late in the submission process", "Advised the requester to recommend
  desk rejection") are that cluster's procedure, not group 0's explain-the-
  authorised-channel procedure. It was flagged as the single bleed during
  inspection. group 0 -> n=19, decline cluster -> n=23.

TRACEABILITY: every new cluster records split_from / split_group, and the
corrected ticket is recorded in assigned_by_inspection on its destination, the
same fields the desk_reject_appeal three-way split used. cluster_ids are
positional and ARE renumbered by size afterwards, so — following the lesson from
stage3_apply_decisions.py — every target here is located by TICKET MEMBERSHIP,
never by cluster_id.

SUPERSEDES the intent's existing c0_split_note, which asserted "Nothing folded
into the emergency-reviewer cluster: no sub-cluster is procedurally equivalent to
it". That was true in RAW 384-dim space and is now false under UMAP; leaving it
would contradict the state this script writes.

A snapshot is written to clusters_pre_e012.json before anything is modified.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import warnings  # noqa: E402

warnings.filterwarnings("ignore")

from stage3_cluster import (  # noqa: E402
    _STAGE1,
    _STAGE2,
    MERGE_SIMILARITY,
    _hdbscan,
    embed,
    load_joined,
)
from stage3_cluster_v2 import reduce_umap  # noqa: E402

_FILE = _ROOT / "data" / "mining" / "stage3_full" / "clusters.json"
_SNAPSHOT = _ROOT / "data" / "mining" / "stage3_full" / "clusters_pre_e012.json"

INTENT = "reviewer_assignment"
MCS = 20
N_COMPONENTS = 10
N_NEIGHBORS = 15
CORRECTED_TICKET = 17955

# Destination clusters are identified by a ticket KNOWN to be in them, because
# ids are positional. These two anchors were read off the inspection dump.
ANCHOR_EMERGENCY_INVITE = 17761  # c1 — "Invited Sayed to serve as the requested..."
ANCHOR_DECLINE_LATE = 18181  # c4 — "emergency reviewers could no longer be assigned"

NEW_LABELS = {
    0: "Explain who is authorized to invite a reviewer and the correct channel "
    "and process for requesting one, regardless of deadline.",
    2: "Grant a validated reviewer reassignment or removal request and route "
    "execution to the assignments team.",
    3: "Confirm or clarify an individual reviewer's own emergency-assignment "
    "status, including stopping unwanted notifications after removal.",
}


def main() -> None:
    data = json.loads(_FILE.read_text(encoding="utf-8"))
    spec = data[INTENT]

    rows_all = load_joined(_STAGE1, _STAGE2)[INTENT]
    idx = {r["ticket_id"]: i for i, r in enumerate(rows_all)}

    def _find(anchor: int) -> dict:
        hits = [c for c in spec["clusters"] if anchor in c["ticket_ids"]]
        assert len(hits) == 1, f"anchor {anchor} matched {len(hits)} clusters"
        return hits[0]

    residual = next(c for c in spec["clusters"] if c["size"] == 180)
    assert residual.get("split_from", "").startswith("c0"), "expected the c0 residual"
    c1 = _find(ANCHOR_EMERGENCY_INVITE)
    c4 = _find(ANCHOR_DECLINE_LATE)
    assert c1["size"] == 73 and c4["size"] == 22, (c1["size"], c4["size"])

    res_ids = list(residual["ticket_ids"])
    assert len(res_ids) == 180

    # --- reproduce the reviewed split ---------------------------------------
    sel = [idx[t] for t in res_ids]
    rows = [rows_all[i] for i in sel]
    raw = embed([" ".join(r["steps_taken"]) for r in rows]).astype("float64")
    reduced, _ = reduce_umap(raw, N_COMPONENTS, N_NEIGHBORS)
    labels = _hdbscan(reduced, MCS)

    groups: dict[int, list[int]] = {}
    for j, lab in enumerate(labels):
        groups.setdefault(int(lab), []).append(res_ids[j])
    noise_ids = groups.pop(-1, [])

    sizes = {g: len(v) for g, v in groups.items()}
    assert sizes == {1: 65, 2: 67, 0: 20, 3: 20}, f"split did not reproduce: {sizes}"
    assert len(noise_ids) == 8, len(noise_ids)
    print(f"split reproduced: {sizes} + {len(noise_ids)} noise")

    # --- manual correction: 17955  group 0 -> decline-late cluster -----------
    assert CORRECTED_TICKET in groups[0], f"{CORRECTED_TICKET} not in group 0"
    groups[0].remove(CORRECTED_TICKET)
    c4["ticket_ids"] = list(c4["ticket_ids"]) + [CORRECTED_TICKET]
    c4["size"] = len(c4["ticket_ids"])
    c4.setdefault("assigned_by_inspection", []).append(CORRECTED_TICKET)
    c4["correction_note"] = (
        f"E012: ticket {CORRECTED_TICKET} moved here from the c0 split's group 0 "
        "(explain-authorised-channel). Its steps decline the reviewer additions as "
        "too late and advise recommending desk rejection — this cluster's procedure, "
        "not group 0's."
    )
    assert len(groups[0]) == 19 and c4["size"] == 23, (len(groups[0]), c4["size"])
    print(f"correction applied: {CORRECTED_TICKET} -> decline cluster "
          f"(group 0 n=19, decline n=23)")

    # --- fold group 1 into c1 ------------------------------------------------
    c1["ticket_ids"] = list(c1["ticket_ids"]) + groups.pop(1)
    c1["size"] = len(c1["ticket_ids"])
    assert c1["size"] == 138, c1["size"]
    c1["merged_from"] = "c1 (n=73) + c0-split group 1 (n=65)"
    c1["merge_note"] = (
        "E012: the c0 residual's group 1 is procedurally identical to this cluster "
        "(review the emergency-PC request -> invite the named person -> confirm "
        "publicly); raw-space centroid cosine 0.960, and confirmed by reading "
        "steps_taken, not by the similarity alone."
    )
    print(f"folded group 1 into the emergency-invite cluster -> n={c1['size']}")

    # --- drop the residual, add the three new clusters -----------------------
    spec["clusters"] = [c for c in spec["clusters"] if c is not residual]

    def _example(t: int) -> dict:
        r = rows_all[idx[t]]
        return {
            "ticket_id": t,
            "what_was_asked": r["what_was_asked"],
            "steps_taken": r["steps_taken"],
        }

    for g in (2, 0, 3):
        ids = groups[g]
        spec["clusters"].append(
            {
                "cluster_id": -1,  # renumbered below
                "size": len(ids),
                "recovered_from_noise": True,
                "split_from": "c0 (n=180, UMAP decomposition, E012)",
                "split_group": f"group_{g}",
                "assigned_by_inspection": [],
                "label": NEW_LABELS[g],
                "ticket_ids": ids,
                "examples": [_example(t) for t in ids[:3]],
            }
        )
        print(f"added new cluster from group {g}: n={len(ids)}")

    # Refresh c1's examples so they represent the merged cluster.
    c1["examples"] = [_example(t) for t in c1["ticket_ids"][:3]]
    c4["examples"] = [_example(t) for t in c4["ticket_ids"][:3]]

    # --- renumber positionally by size (project convention) ------------------
    spec["clusters"].sort(key=lambda c: -c["size"])
    for new_id, c in enumerate(spec["clusters"]):
        c["cluster_id"] = new_id

    # --- recompute intent-level counts --------------------------------------
    clustered = sum(c["size"] for c in spec["clusters"])
    spec["n_noise"] = spec["n_tickets"] - clustered
    spec["n_recovered"] = sum(1 for c in spec["clusters"] if c["recovered_from_noise"])
    spec["n_primary"] = len(spec["clusters"]) - spec["n_recovered"]

    # --- recompute candidate merges, THIS INTENT ONLY ------------------------
    raw_all = embed([" ".join(r["steps_taken"]) for r in rows_all]).astype("float64")
    cents = []
    for c in spec["clusters"]:
        v = raw_all[[idx[t] for t in c["ticket_ids"]]].mean(axis=0)
        cents.append(v / max(1e-12, float(np.linalg.norm(v))))
    sim = np.vstack(cents) @ np.vstack(cents).T
    merges = [
        {"candidate_merge": [a, b], "similarity": round(float(sim[a, b]), 3)}
        for a in range(len(spec["clusters"]))
        for b in range(a + 1, len(spec["clusters"]))
        if sim[a, b] >= MERGE_SIMILARITY
    ]
    spec["candidate_merges"] = merges

    # --- notes: supersede the stale c0_split_note ---------------------------
    spec["c0_split_note"] = (
        "SUPERSEDED by e012_note. Original (raw 384-dim) finding: c0 (n=301) "
        "re-clustered in isolation at mcs=5 -> 6 sub-clusters, 81 members returned "
        "to noise, and nothing was folded into the emergency-reviewer cluster "
        "because no sub-cluster was procedurally equivalent (largest core ~46% "
        "emergency-reviewer). That held in raw space; UMAP later separated the "
        "same mixture and DID surface an equivalent sub-cluster."
    )
    spec["e012_note"] = (
        "E012: c0's 180-member residual decomposed in isolation via "
        f"UMAP(n_components={N_COMPONENTS}, n_neighbors={N_NEIGHBORS}, seed=42) + "
        f"HDBSCAN(mcs={MCS}) — the one finding adopted from the v2 clustering "
        "evaluation. Raw 384-dim could not split this cluster (a 136-ticket blob at "
        "61% emergency at every mcs); UMAP split it into 67@10% / 65@92% / 20@85% / "
        "20@80%, verified against an emergency-purity metric independent of both "
        "UMAP and DBCV. group 1 (n=65) folded into the emergency-invite cluster; "
        "groups 2/0/3 added as new clusters; ticket 17955 hand-moved from group 0 to "
        "the decline-late cluster; 8 members returned to noise. The pipeline-wide "
        "UMAP+DBCV-argmax method was evaluated and REJECTED — it disagreed with 3 of "
        "4 settled human decisions and its selection criterion was not reproducible "
        "across random seeds. See docs/exp_tracking/E012_stage3_umap_dbcv.md."
    )

    # --- verification --------------------------------------------------------
    all_ids = [t for c in spec["clusters"] for t in c["ticket_ids"]]
    assert len(all_ids) == len(set(all_ids)), "duplicate ticket across clusters"
    assert clustered + spec["n_noise"] == spec["n_tickets"]
    # Every one of the residual's 180 members is accounted for exactly once.
    placed = set(all_ids) & set(res_ids)
    assert len(placed) + len(noise_ids) == 180, (len(placed), len(noise_ids))
    assert set(noise_ids).isdisjoint(all_ids), "a noise member is still clustered"
    c4_now = _find(ANCHOR_DECLINE_LATE)
    assert CORRECTED_TICKET in c4_now["ticket_ids"], "correction lost"
    assert sum(1 for t in all_ids if t == CORRECTED_TICKET) == 1

    shutil.copy2(_FILE, _SNAPSHOT)
    print(f"\nsnapshot -> {_SNAPSHOT}")
    _FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote    -> {_FILE}")

    print(f"\n{INTENT}: {len(spec['clusters'])} clusters, "
          f"{clustered} clustered, {spec['n_noise']} noise, "
          f"coverage {clustered / spec['n_tickets']:.1%}")
    for c in spec["clusters"]:
        prov = f"  [{c.get('split_group') or c.get('merged_from') or ''}]" if (
            c.get("split_group") or c.get("merged_from")
        ) else ""
        print(f"  c{c['cluster_id']:<3} n={c['size']:>4}{prov}")
    print(f"candidate_merges: {merges}")

    tc = sum(len(s["clusters"]) for s in data.values())
    tn = sum(s["n_noise"] for s in data.values())
    tt = sum(s["n_tickets"] for s in data.values())
    print(f"\nCORPUS: {tc} clusters, {tt - tn} clustered / {tt} tickets, "
          f"{tn} noise, coverage {(tt - tn) / tt:.1%}")


if __name__ == "__main__":
    main()
