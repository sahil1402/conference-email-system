"""Full-text dump of the four pending refinement items. READ-ONLY, local, no spend.

Writes only data/mining/stage3_v2_test/pending_inspection.json. NEVER touches
data/mining/stage3_full/.

Every prior split/merge in this project was decided by reading real steps_taken,
not by a similarity score. The refinement scan surfaced candidates and showed 5
examples each; this dumps the COMPLETE text behind each pending decision.

  ITEM 1  reviewer_workload_role c4 -> SG0 (n=25): all 25 tickets. Is this one
          unnamed procedure, a grab-bag that should partly return to noise, or
          does it split again on closer reading?
  ITEM 2  author_profile_compliance c10 / c2 / c5 / c11 (the four flagged
          threshold artefacts) PLUS the full contents of the clusters they point
          at (c0 n=13, c1 n=4) — not just the single nearest ticket, because
          "should these fold in" cannot be answered against one member of a
          13-ticket cluster.
  ITEM 3  paper_bidding c1 and c2 in full.
  ITEM 4  DRIFT CHECK. The decomposition is re-derived from scratch in this
          process and its group sizes are asserted against the values the scan
          reported (SG0 25 / SG1 19 / SG2 20 / SG3 15, 12 noise). UMAP is seeded
          (random_state=42) so this SHOULD be exact; asserting it is what makes
          "a different session read the same file" a verified claim rather than
          an assumption.
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

from stage3_cluster import _STAGE1, _STAGE2, _hdbscan, embed, load_joined  # noqa: E402
from stage3_cluster_v2 import reduce_umap  # noqa: E402

_FULL = _ROOT / "data" / "mining" / "stage3_full" / "clusters.json"
_SCAN = _ROOT / "data" / "mining" / "stage3_v2_test" / "refinement_scan.json"
_OUT = _ROOT / "data" / "mining" / "stage3_v2_test" / "pending_inspection.json"

# What the refinement scan reported, pinned here so drift is an assertion failure.
EXPECTED_SUBGROUPS = {0: 25, 2: 20, 1: 19, 3: 15}
EXPECTED_NOISE = 12
FLAGGED = ("reviewer_workload_role", 4)
MCS = 10


def main() -> None:
    full = json.loads(_FULL.read_text(encoding="utf-8"))
    scan = json.loads(_SCAN.read_text(encoding="utf-8"))
    by_intent = load_joined(_STAGE1, _STAGE2)

    out: dict = {}

    def rows_of(intent: str) -> tuple[list[dict], dict[int, int]]:
        rows = by_intent[intent]
        return rows, {r["ticket_id"]: i for i, r in enumerate(rows)}

    def tick(intent: str, t: int) -> dict:
        rows, idx = rows_of(intent)
        r = rows[idx[t]]
        return {
            "ticket_id": t,
            "what_was_asked": r["what_was_asked"],
            "steps_taken": r["steps_taken"],
            "outcome_type": r.get("outcome_type"),
        }

    def show(intent: str, ids: list[int], indent: str = "  ") -> list[dict]:
        recs = []
        for t in ids:
            r = tick(intent, t)
            print(f"{indent}ticket {t}   [outcome: {r['outcome_type']}]")
            print(f"{indent}  asked: {r['what_was_asked']}")
            for s in r["steps_taken"]:
                print(f"{indent}  step - {s}")
            print()
            recs.append(r)
        return recs

    # ================= ITEM 4 (run first — everything else depends on it) =====
    intent, cid = FLAGGED
    spec = next(c for c in full[intent]["clusters"] if c["cluster_id"] == cid)
    rows, idx = rows_of(intent)
    ids = [t for t in spec["ticket_ids"] if t in idx]
    # MUST embed the WHOLE intent and slice, exactly as the refinement scan did.
    # Embedding only these 91 texts changes them by ~1e-7 (SentenceTransformer
    # pads to the longest sequence in the batch, so batch COMPOSITION perturbs
    # the floats), and that is enough to move 9 of 91 tickets between groups.
    # See the stability section below — this is a finding, not a workaround.
    whole = embed([" ".join(r["steps_taken"]) for r in rows]).astype("float64")
    raw = whole[[idx[t] for t in ids]]
    reduced, params = reduce_umap(raw, 10, 15)
    labels = _hdbscan(reduced, MCS)
    counts = Counter(labels.tolist())
    noise_n = counts.pop(-1, 0)
    got = dict(counts)

    print("=" * 112)
    print("ITEM 4 — DRIFT CHECK (re-derived from scratch in this process)")
    print("=" * 112)
    print(f"source: {intent} c{cid}, n={len(ids)}  (file mtime {_FULL.stat().st_mtime_ns})")
    print(f"params: UMAP(n_components=10, n_neighbors=15, seed=42) + HDBSCAN(mcs={MCS})")
    print(f"  expected sub-group sizes : {EXPECTED_SUBGROUPS} + {EXPECTED_NOISE} noise")
    print(f"  observed sub-group sizes : {got} + {noise_n} noise")
    match = got == EXPECTED_SUBGROUPS and noise_n == EXPECTED_NOISE
    print(f"  MATCH (no drift): {match}")
    assert match, "DRIFT — the decomposition no longer reproduces the scan's groups"
    out["item4_drift_check"] = {
        "intent": intent,
        "cluster_id": cid,
        "n": len(ids),
        "expected": {str(k): v for k, v in EXPECTED_SUBGROUPS.items()},
        "observed": {str(k): v for k, v in got.items()},
        "expected_noise": EXPECTED_NOISE,
        "observed_noise": int(noise_n),
        "match": bool(match),
        "umap_params": params,
        "mcs": MCS,
    }

    members = {g: [ids[i] for i in range(len(ids)) if labels[i] == g] for g in got}
    noise_ids = [ids[i] for i in range(len(ids)) if labels[i] == -1]

    # --- ITEM 4b: how stable is that membership, really? --------------------
    # Discovered while writing this script: embedding the same 91 texts in a
    # different-sized batch perturbs them by ~1e-7 and moves 9 tickets. So the
    # reference labelling reproduces only when the exact pipeline is repeated.
    # Measured here with a CO-ASSIGNMENT (consensus) matrix, which is
    # label-invariant — group ids are not comparable across runs, but "did these
    # two tickets stay together" is. Perturbations: UMAP seed, plus the two
    # embedding batch paths.
    print("\n" + "-" * 112)
    print("ITEM 4b — STABILITY of the sub-groups under seed + embedding-batch perturbation")
    print("-" * 112)
    # The perturbation is GAUSSIAN JITTER at the magnitude measured empirically
    # from the batch effect (max |diff| = 1.006e-07), re-normalised. Using jitter
    # rather than a second embed() call keeps this reproducible and offline; the
    # batch effect itself was already measured directly (9 of 91 tickets moved).
    rng = np.random.default_rng(0)
    alt = raw + rng.normal(0.0, 1.0e-07, raw.shape)
    alt = alt / np.linalg.norm(alt, axis=1, keepdims=True)
    runs: list[np.ndarray] = []
    for space in (raw, alt):
        for seed in (42, 7, 123, 2024, 31337):
            red = np.asarray(
                __import__("umap").UMAP(
                    n_components=10, n_neighbors=15, min_dist=0.0,
                    metric="cosine", random_state=seed,
                ).fit_transform(space.astype("float32")),
                dtype="float64",
            )
            runs.append(_hdbscan(red, MCS))

    n = len(ids)
    together = np.zeros((n, n))
    both_clustered = np.zeros((n, n))
    noise_count = np.zeros(n)
    for lab in runs:
        cl = lab >= 0
        noise_count += ~cl
        pair = np.outer(cl, cl)
        both_clustered += pair
        together += pair & (lab[:, None] == lab[None, :])
    co = np.divide(together, np.maximum(both_clustered, 1))

    print(f"{len(runs)} runs (2 embedding batches x 5 UMAP seeds)")
    print(f"{'sub-group':>10}{'n':>5}{'mean co-assignment':>21}{'mean noise rate':>17}  reading")
    stability = {}
    for g in sorted(members, key=lambda g: -len(members[g])):
        pos = [ids.index(t) for t in members[g]]
        sub = co[np.ix_(pos, pos)]
        m = float((sub.sum() - len(pos)) / max(1, len(pos) * (len(pos) - 1)))
        nz = float(noise_count[pos].mean() / len(runs))
        verdict = "STABLE" if m >= 0.80 and nz <= 0.15 else (
            "UNSTABLE" if m < 0.60 or nz > 0.30 else "PARTLY STABLE"
        )
        stability[f"SG{g}"] = {
            "size": len(members[g]),
            "mean_co_assignment": round(m, 3),
            "mean_noise_rate": round(nz, 3),
            "verdict": verdict,
        }
        print(f"{'SG' + str(g):>10}{len(members[g]):>5}{m:>21.3f}{nz:>17.3f}  {verdict}")
    out["item4b_stability"] = {
        "n_runs": len(runs),
        "perturbations": "2 vector variants (exact + 1e-7 jitter) x UMAP seeds {42,7,123,2024,31337}",
        "embedding_batch_effect": {
            "max_abs_vector_diff": 1.006e-07,
            "tickets_moved": 9,
            "note": "SentenceTransformer pads to the longest sequence per batch, so "
            "batch composition perturbs the floats; 1e-7 was enough to move 9 of 91.",
        },
        "per_subgroup": stability,
    }

    # ================= ITEM 1 =================
    print("\n" + "=" * 112)
    print(f"ITEM 1 — {intent} c{cid} SG0, ALL {len(members[0])} tickets")
    print("=" * 112)
    print(f"parent label: {spec['label']}\n")
    out["item1_sg0_all_members"] = {
        "size": len(members[0]),
        "ticket_ids": members[0],
        "tickets": show(intent, members[0]),
    }

    print("-" * 112)
    print(f"(context) the other sub-groups' membership, ids only — full text was")
    print("          already reported in the refinement scan")
    for g in (2, 1, 3):
        print(f"  SG{g} (n={len(members[g])}): {members[g]}")
    print(f"  noise (n={len(noise_ids)}): {noise_ids}")
    out["item1_context_other_subgroups"] = {
        f"SG{g}": {"size": len(members[g]), "ticket_ids": members[g]} for g in (2, 1, 3)
    }
    out["item1_context_other_subgroups"]["noise"] = {
        "size": len(noise_ids),
        "ticket_ids": noise_ids,
    }

    # ================= ITEM 2 =================
    apc = "author_profile_compliance"
    print("\n" + "=" * 112)
    print(f"ITEM 2 — {apc}: the 4 flagged singletons + the clusters they point at")
    print("=" * 112)
    flagged_ids = [10, 2, 5, 11]
    scan_by_cid = {
        i["cluster_id"]: i for i in scan["check2"]["items"] if i["intent"] == apc
    }
    out["item2_flagged"] = []
    for c in flagged_ids:
        cl = next(x for x in full[apc]["clusters"] if x["cluster_id"] == c)
        near = scan_by_cid[c]["nearest_clusters"][0]
        print(f"\n--- c{c} (n={cl['size']}) ---")
        print(f"    label: {cl['label']}")
        print(f"    nearest: c{near['other_cluster']} max={near['max_pairwise_sim']} "
              f"avg-linkage={near['avg_linkage_sim']} via ticket {near['closest_ticket']}")
        recs = show(apc, cl["ticket_ids"], indent="    ")
        out["item2_flagged"].append(
            {
                "cluster_id": c,
                "size": cl["size"],
                "label": cl["label"],
                "nearest": near,
                "tickets": recs,
            }
        )

    out["item2_target_clusters"] = []
    for c in (0, 1):
        cl = next(x for x in full[apc]["clusters"] if x["cluster_id"] == c)
        print(f"\n{'=' * 112}")
        print(f"TARGET c{c} (n={cl['size']}) — the proposed home, in FULL")
        print(f"label: {cl['label']}")
        print("=" * 112)
        recs = show(apc, cl["ticket_ids"], indent="    ")
        out["item2_target_clusters"].append(
            {"cluster_id": c, "size": cl["size"], "label": cl["label"], "tickets": recs}
        )

    # ================= ITEM 3 =================
    pb = "paper_bidding"
    print("\n" + "=" * 112)
    print(f"ITEM 3 — {pb} c1 and c2 in full")
    print("=" * 112)
    out["item3_paper_bidding"] = []
    for c in (1, 2):
        cl = next(x for x in full[pb]["clusters"] if x["cluster_id"] == c)
        print(f"\n--- c{c} (n={cl['size']}) ---")
        print(f"    label: {cl['label']}")
        recs = show(pb, cl["ticket_ids"], indent="    ")
        out["item3_paper_bidding"].append(
            {"cluster_id": c, "size": cl["size"], "label": cl["label"], "tickets": recs}
        )
    # Their mutual similarity, restated from the scan for convenience.
    pb_rows, pb_idx = rows_of(pb)
    pv = embed([" ".join(r["steps_taken"]) for r in pb_rows]).astype("float64")
    a = pv[pb_idx[15318]]
    b = pv[pb_idx[15949]]
    out["item3_similarity"] = round(float(a @ b), 4)
    print(f"    cosine(15318, 15949) = {out['item3_similarity']:.4f} "
          f"(fallback joins at >= 0.65)")

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote -> {_OUT}")
    print("NOTE: data/mining/stage3_full/ was not modified.")


if __name__ == "__main__":
    main()
