# ONE-SHOT SCRIPT — already run against the clusters.json state at the time.
# Re-running will fail its own assertions, since it locates clusters by ticket
# membership that no longer exists after this script applied its changes.
# Kept as an audit trail of what was manually applied during Stage 3, not as
# reusable tooling.

"""Apply the second refinement pass: decompose reviewer_workload_role c4, fold 3
author_profile_compliance singletons, and record one reviewed no-change decision.

Every membership is asserted as an EXACT PARTITION before anything is written —
a missing, duplicated, or unaccounted ticket aborts the run with nothing changed.

--- 1. reviewer_workload_role c4 (n=91) DECOMPOSED -------------------------------
c4 was the single cluster flagged by the cohesion scan (0.4152, residual -0.2587,
BELOW the confirmed-mixture calibration anchor). Its own label already named three
different actions. UMAP(10, nn=15, seed=42) + HDBSCAN(mcs=10) split it into
SG0 25 / SG2 20 / SG1 19 / SG3 15 + 12 noise; reading all of them confirmed SG1
and SG2 are OPPOSITE OUTCOMES of the same request (grant vs. refuse a workload
reduction) — the polarity distinction this project has repeatedly kept separate.

  SG1 (19) -> grant the reduction, route to the assignments team
  SG2 (20) -> decline it, require the reviews be completed
  SG3 (15) -> acknowledge unavailability, no further action
  SG0 (25) -> NOT a procedure. A grab-bag; only a 7-ticket core survives.

SG0 RECONCILED (the count query that gated this script): SG0 is 25, and the
partition below covers it exactly. An earlier prose summary appeared to total 24
because it used the UNSUPERVISED service-letter pair (21445, 21449) in place of
the read group of three, dropping 21075 (a session-chair invitation letter,
routed to the registration team). A COUNTING NOTE in the summary, not a data
issue — asserted here rather than assumed:
    7 core + 4 adjacent + 5 pc-status + 3 service-letter + 6 unrelated = 25

Only the 7-ticket core is kept. It was found by an UNSUPERVISED re-clustering of
SG0 alone (average-linkage at the project's own 0.35 threshold, cohesion 0.7572),
not by hand-picking: every member answers an unsolicited offer to review with
"recruitment for this cycle has closed, apply for the next one". The 18 others
return to noise; the tightest of them (21445/21449, service letters, cohesion
0.9177) is only n=2, below the pipeline's mcs>=5 floor, so it cannot be a cluster.

--- 2. author_profile_compliance DBLP folds -------------------------------------
  21676 (old c5)         -> c0. Verbatim c0's own rule ("authors with no papers
                                 indexed by DBLP may omit the URL").
  21742, 21855 (old c2)  -> c0. Career-stage-conditional delivery of c0's two
                                 rules; c0's label is extended to say so.
  21882 (old c11)        -> c1, NOT c0. Same chair ACTION as c1's 21880 — adding
                                 a submission-form field to unblock a reciprocal-
                                 reviewer nomination.
  21874 (c10)            -> UNCHANGED, deliberately. It is about the declaration
                                 form's per-author granularity and presupposes the
                                 form is already in use — shared vocabulary,
                                 different question. Asserted untouched below.

--- 3. paper_bidding: NO DATA CHANGE, note only ---------------------------------
c1/c2 reviewed and deliberately kept separate on outcome polarity. Recorded so a
future pass does not mistake it for an unreviewed gap.
"""

from __future__ import annotations

import json
import shutil
import sys
import warnings
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

warnings.filterwarnings("ignore")

from stage3_cluster import (  # noqa: E402
    _STAGE1,
    _STAGE2,
    MERGE_SIMILARITY,
    embed,
    load_joined,
)

_FILE = _ROOT / "data" / "mining" / "stage3_full" / "clusters.json"
_SNAP = _ROOT / "data" / "mining" / "stage3_full" / "clusters_pre_refine2.json"
_INSP = _ROOT / "data" / "mining" / "stage3_v2_test" / "pending_inspection.json"

RWR = "reviewer_workload_role"
APC = "author_profile_compliance"
PB = "paper_bidding"

CORE7 = [15839, 19700, 20099, 20125, 20777, 20897, 20911]
SG0_PARTITION = {
    "core_volunteer_inquiry": CORE7,
    "adjacent_not_core": [18603, 19648, 21453, 21646],
    "pc_membership_status": [15272, 15879, 16110, 17755, 19162],
    "service_letters": [21445, 21449, 21075],
    "unrelated": [16003, 17880, 18588, 19364, 21782, 21870],
}

LABELS = {
    "core_volunteer_inquiry": "Respond to volunteer/recruitment inquiries after the official "
    "reviewer-recruitment window has closed.",
    "SG1": "Grant a reviewer workload reduction and route execution to the assignments team.",
    "SG2": "Decline a workload-reduction request and require the requester to complete the "
    "assigned reviews.",
    "SG3": "Acknowledge a reviewer's reported unavailability with no further action.",
}

SG1_STABILITY = (
    "Boundary stability weaker than the sibling groups: 0.879 mean co-assignment across 10 "
    "reruns (2 vector variants x 5 UMAP seeds) vs 1.000 for the core/SG2/SG3. Core membership "
    "is trustworthy; the exact edge count may vary by about +/-3 on a re-embed. See E013."
)

APC_C0_LABEL_SUFFIX = (
    "; for career-stage-conditional cases, students/early-career authors may leave it blank "
    "while senior authors are asked to supply the disambiguation URL directly."
)


def main() -> None:
    data = json.loads(_FILE.read_text(encoding="utf-8"))
    insp = json.loads(_INSP.read_text(encoding="utf-8"))
    by_intent = load_joined(_STAGE1, _STAGE2)

    # ---------------- reviewer_workload_role ----------------
    spec = data[RWR]
    rows = by_intent[RWR]
    idx = {r["ticket_id"]: i for i, r in enumerate(rows)}
    c4 = next(c for c in spec["clusters"] if c["size"] == 91)
    old_members = list(c4["ticket_ids"])

    sub = insp["item1_context_other_subgroups"]
    sg0 = insp["item1_sg0_all_members"]["ticket_ids"]
    sg = {
        "SG1": sub["SG1"]["ticket_ids"],
        "SG2": sub["SG2"]["ticket_ids"],
        "SG3": sub["SG3"]["ticket_ids"],
    }
    dnoise = sub["noise"]["ticket_ids"]

    # --- EXACT PARTITION ASSERTIONS (nothing is written if any of these fail) --
    assert len(sg0) == 25, len(sg0)
    flat = [t for v in SG0_PARTITION.values() for t in v]
    assert len(flat) == len(set(flat)) == 25, "SG0 partition is not a partition"
    assert set(flat) == set(sg0), "SG0 partition does not cover SG0 exactly"
    assert set(CORE7) <= set(sg0)

    covered = set(sg0) | set(sg["SG1"]) | set(sg["SG2"]) | set(sg["SG3"]) | set(dnoise)
    assert covered == set(old_members), "sub-groups do not reconstruct c4 exactly"
    assert (
        len(sg0) + len(sg["SG1"]) + len(sg["SG2"]) + len(sg["SG3"]) + len(dnoise)
        == len(old_members)
        == 91
    ), "c4 membership does not add up"
    for a in ("SG1", "SG2", "SG3"):
        for b in ("SG1", "SG2", "SG3"):
            if a != b:
                assert not (set(sg[a]) & set(sg[b])), f"{a}/{b} overlap"
    assert (len(sg["SG1"]), len(sg["SG2"]), len(sg["SG3"])) == (19, 20, 15)
    to_noise = [t for t in sg0 if t not in set(CORE7)]
    assert len(to_noise) == 18, len(to_noise)
    print(f"{RWR}: partition verified — 91 = 7 core + 19 + 20 + 15 + {len(to_noise)} SG0-noise "
          f"+ {len(dnoise)} decomposition-noise")

    def _example(intent: str, t: int) -> dict:
        r = by_intent[intent][{x["ticket_id"]: i for i, x in enumerate(by_intent[intent])}[t]]
        return {
            "ticket_id": t,
            "what_was_asked": r["what_was_asked"],
            "steps_taken": r["steps_taken"],
        }

    spec["clusters"] = [c for c in spec["clusters"] if c is not c4]
    for key, ids in (
        ("core_volunteer_inquiry", CORE7),
        ("SG2", sg["SG2"]),
        ("SG1", sg["SG1"]),
        ("SG3", sg["SG3"]),
    ):
        entry = {
            "cluster_id": -1,
            "size": len(ids),
            "recovered_from_noise": True,
            "split_from": "c4 (n=91, UMAP decomposition, E012 method)",
            "split_group": key,
            "assigned_by_inspection": [],
            "label": LABELS[key],
            "ticket_ids": list(ids),
            "examples": [_example(RWR, t) for t in ids[:3]],
        }
        if key == "SG1":
            entry["stability_note"] = SG1_STABILITY
        if key == "core_volunteer_inquiry":
            entry["derivation_note"] = (
                "Extracted from SG0 (n=25), which is a grab-bag, not a procedure. This core was "
                "found by an UNSUPERVISED re-clustering of SG0 alone (average-linkage at "
                "LINK_THRESHOLD=0.35, cohesion 0.7572), not hand-picked; the other 18 SG0 members "
                "returned to noise. The tightest of those (21445/21449, service letters, cohesion "
                "0.9177) is n=2, below the mcs>=5 floor, so it cannot be a cluster."
            )
        spec["clusters"].append(entry)
        print(f"  + {key}: n={len(ids)}")

    spec["c4_split_note"] = (
        "Refinement pass 2: c4 (n=91) was the ONLY cluster corpus-wide flagged by the cohesion "
        "scan (0.4152; trend residual -0.2587, below the confirmed-mixture calibration anchor). "
        "Decomposed in isolation via UMAP(n_components=10, n_neighbors=15, seed=42) + "
        "HDBSCAN(mcs=10) -> SG0 25 / SG2 20 / SG1 19 / SG3 15 + 12 noise. SG1 and SG2 are "
        "opposite outcomes of the same request (grant vs. refuse a workload reduction) and are "
        "kept separate on the outcome-polarity rule this corpus applies elsewhere. SG0 was not a "
        "procedure: only a 7-ticket volunteer-inquiry core was kept, 18 members returned to "
        "noise. See docs/exp_tracking/E012 (method) and E013 (reproducibility)."
    )

    # ---------------- author_profile_compliance ----------------
    aspec = data[APC]

    def _find(pred, what):
        hits = [c for c in aspec["clusters"] if pred(c)]
        assert len(hits) == 1, f"{what}: matched {len(hits)}"
        return hits[0]

    a_c0 = _find(lambda c: 21705 in c["ticket_ids"], "apc c0")
    a_c1 = _find(lambda c: 21880 in c["ticket_ids"], "apc c1")
    a_c5 = _find(lambda c: c["ticket_ids"] == [21676], "apc c5")
    a_c2 = _find(lambda c: set(c["ticket_ids"]) == {21742, 21855}, "apc c2")
    a_c11 = _find(lambda c: c["ticket_ids"] == [21882], "apc c11")
    a_c10 = _find(lambda c: c["ticket_ids"] == [21874], "apc c10")
    assert (a_c0["size"], a_c1["size"]) == (13, 4), (a_c0["size"], a_c1["size"])
    c10_before = list(a_c10["ticket_ids"])

    a_c0["ticket_ids"] = list(a_c0["ticket_ids"]) + [21676, 21742, 21855]
    a_c0["size"] = len(a_c0["ticket_ids"])
    a_c0["label"] = a_c0["label"].rstrip(".") + APC_C0_LABEL_SUFFIX
    a_c0["merged_from"] = "c0 (n=13) + c5 (21676) + c2 (21742, 21855)"
    a_c0["merge_note"] = (
        "Refinement pass 2: 21676 states c0's own rule verbatim (no DBLP-indexed papers -> the "
        "URL may be omitted); 21742/21855 deliver c0's two rules as a branch on career stage, "
        "now reflected in the label."
    )
    a_c1["ticket_ids"] = list(a_c1["ticket_ids"]) + [21882]
    a_c1["size"] = len(a_c1["ticket_ids"])
    a_c1["merged_from"] = "c1 (n=4) + c11 (21882)"
    a_c1["merge_note"] = (
        "Refinement pass 2: folded into c1 rather than c0 — 21882 is the same chair ACTION as "
        "c1's 21880 (add a submission-form field to unblock a reciprocal-reviewer nomination), "
        "not c0's advice-giving."
    )
    aspec["clusters"] = [c for c in aspec["clusters"] if c not in (a_c5, a_c2, a_c11)]
    a_c0["examples"] = [_example(APC, t) for t in a_c0["ticket_ids"][:3]]
    a_c1["examples"] = [_example(APC, t) for t in a_c1["ticket_ids"][:3]]
    assert a_c0["size"] == 16 and a_c1["size"] == 5
    assert a_c10["ticket_ids"] == c10_before, "c10 must be untouched"
    print(f"{APC}: c0 13->16, c1 4->5, c10 untouched ({c10_before})")

    aspec["refinement_note"] = (
        "Refinement pass 2: four size<=2 clusters were flagged as possible average-linkage "
        "threshold artefacts (max pairwise similarity above the fallback's 0.65 join criterion "
        "while their average linkage fell below it). Reading them resolved three folds and one "
        "false positive: 21676 and 21742/21855 -> c0, 21882 -> c1, while 21874 (c10) is KEPT "
        "SEPARATE — it concerns the declaration form's per-author granularity and presupposes the "
        "form is already in use: shared vocabulary, different question."
    )

    # ---------------- paper_bidding (note only) ----------------
    pb_before = json.dumps(data[PB]["clusters"], sort_keys=True)
    data[PB]["refinement_note"] = (
        "Refinement pass 2: c1 (15318) and c2 (15949) were reviewed together and DELIBERATELY "
        "KEPT SEPARATE. Both are triggered by a late bidding-deadline email and both open by "
        "verifying the requester's assignment record, but they reach opposite conclusions — "
        "15318 concedes and apologises for a real communication error, 15949 explains that no "
        "error occurred. cos=0.5617, below the fallback's 0.65 join criterion. This matches the "
        "outcome-polarity rule already applied to desk_reject_appeal (upheld vs. reversed) and "
        "reviewer_assignment (grant vs. decline). Reviewed, not an unexamined gap."
    )
    assert json.dumps(data[PB]["clusters"], sort_keys=True) == pb_before, "paper_bidding changed"
    print(f"{PB}: note only, cluster data byte-identical")

    # ---------------- recount + recompute merges ----------------
    for intent in (RWR, APC):
        s = data[intent]
        s["clusters"].sort(key=lambda c: -c["size"])
        for i, c in enumerate(s["clusters"]):
            c["cluster_id"] = i
        clustered = sum(c["size"] for c in s["clusters"])
        s["n_noise"] = s["n_tickets"] - clustered
        s["n_recovered"] = sum(1 for c in s["clusters"] if c.get("recovered_from_noise"))
        s["n_primary"] = len(s["clusters"]) - s["n_recovered"]

        r = by_intent[intent]
        ix = {x["ticket_id"]: i for i, x in enumerate(r)}
        V = embed([" ".join(x["steps_taken"]) for x in r]).astype("float64")
        cents = []
        for c in s["clusters"]:
            v = V[[ix[t] for t in c["ticket_ids"]]].mean(axis=0)
            cents.append(v / max(1e-12, float(np.linalg.norm(v))))
        sim = np.vstack(cents) @ np.vstack(cents).T
        s["candidate_merges"] = [
            {"candidate_merge": [a, b], "similarity": round(float(sim[a, b]), 3)}
            for a in range(len(s["clusters"]))
            for b in range(a + 1, len(s["clusters"]))
            if sim[a, b] >= MERGE_SIMILARITY
        ]

        ids = [t for c in s["clusters"] for t in c["ticket_ids"]]
        assert len(ids) == len(set(ids)), f"{intent}: duplicate ticket"
        assert clustered + s["n_noise"] == s["n_tickets"], f"{intent}: count mismatch"

    shutil.copy2(_FILE, _SNAP)
    _FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nsnapshot -> {_SNAP}\nwrote    -> {_FILE}")

    for intent in (RWR, APC):
        s = data[intent]
        cl = sum(c["size"] for c in s["clusters"])
        print(f"\n{intent}: {len(s['clusters'])} clusters, {cl} clustered, "
              f"{s['n_noise']} noise, coverage {cl / s['n_tickets']:.1%}")
        for c in s["clusters"]:
            tag = f"  [{c.get('split_group') or c.get('merged_from') or ''}]" if (
                c.get("split_group") or c.get("merged_from")
            ) else ""
            print(f"  c{c['cluster_id']:<3} n={c['size']:>3}{tag}")
        print(f"  candidate_merges: {s['candidate_merges']}")

    tc = sum(len(s["clusters"]) for s in data.values())
    tn = sum(s["n_noise"] for s in data.values())
    tt = sum(s["n_tickets"] for s in data.values())
    print(f"\nCORPUS: {tc} clusters, {tt - tn}/{tt} clustered, {tn} noise, "
          f"coverage {(tt - tn) / tt:.1%}")


if __name__ == "__main__":
    main()
