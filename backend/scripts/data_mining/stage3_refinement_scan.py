"""Two refinement audits over the production Stage 3 clusters. READ-ONLY, no spend.

Writes only data/mining/stage3_v2_test/refinement_scan.json. NEVER touches
data/mining/stage3_full/.

CHECK 1 — hidden-mixture hunt across every cluster with n >= 30
  Cohesion = mean pairwise cosine among a cluster's members in the RAW 384-dim
  space (the embeddings are L2-normalised, so cosine is a plain dot product).

  THE THRESHOLD PROBLEM, and how it is avoided. Cohesion falls with size for
  purely mechanical reasons — more members means more chances to be far apart —
  so a flat cutoff would flag every large cluster and clear every small one. Two
  independent references are used instead of a number picked by hand:

    (a) SIZE-CONTROLLED TREND. Cohesion is regressed on log(n) across all 19
        n>=30 clusters corpus-wide, and each cluster scored by its RESIDUAL from
        that trend. A cluster is only "low" relative to what its size predicts.
        Corpus-wide rather than per-intent because most intents have 0-5 clusters
        at n>=30 — far too few to form a baseline (reported per-intent anyway,
        where enough exist, as a cross-check).

    (b) CALIBRATION ANCHORS with known ground truth, read from the pre-E012
        snapshot. These fix the scale empirically:
          KNOWN MIXTURE : reviewer_assignment old c0 (n=180) — confirmed a 61%
                          emergency-reviewer mixture; UMAP split it 67/65/20/20.
          KNOWN CLEAN   : review_submission_help c0 (n=806) — humans MERGED two
                          clusters into this one procedure, so it is confirmed
                          single despite its size.
          KNOWN CLEAN   : reviewer_assignment old c1 (n=73) — confirmed clean
                          emergency-invite procedure.
        The flag threshold is then "at or below where the one CONFIRMED mixture
        sat", which is a measured boundary rather than an invented sigma.

  Flagged clusters get the SAME decomposition already validated on
  reviewer_assignment's residual: UMAP(n_components=10, n_neighbors=15, seed=42)
  + HDBSCAN, in isolation over that cluster's members only.
  mcs=20 is kept verbatim where n is large enough to support it; below n=120 it is
  scaled at the validated ratio (180/20 = 9) and clamped to >=5, since mcs=20 on
  an n=40 cluster can only ever return one group and would test nothing.

CHECK 2 — singleton audit in the agglomerative-fallback intents
  Only author_profile_compliance (n=29) and paper_bidding (n=11) use that path;
  it assigns every point, so singletons are unclustered one-offs by construction,
  not clusters.

  The test is grounded in the ALGORITHM, not a guess: the fallback is
  AverageLinkage at LINK_THRESHOLD=0.35 cosine distance, i.e. it refuses to join
  anything whose AVERAGE similarity to a cluster is below 0.65. So for each
  singleton we report both linkages to every other cluster:
      avg-linkage sim  — what the algorithm actually decided on
      max pairwise sim — its single closest neighbour anywhere
  A singleton whose MAX is comfortably above 0.65 while its AVG is below it was
  split by average-linkage DILUTION (one close relative, outvoted by that
  cluster's other members) — a threshold artefact worth a human look. A singleton
  whose max is also low is a genuine one-off.
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
_SNAP = _ROOT / "data" / "mining" / "stage3_full" / "clusters_pre_e012.json"
_OUT = _ROOT / "data" / "mining" / "stage3_v2_test" / "refinement_scan.json"

MIN_N = 30
LINK_SIM = 0.65  # 1 - LINK_THRESHOLD(0.35): the fallback's own join criterion
VALIDATED_MCS = 20
VALIDATED_N = 180


def cohesion(vecs: np.ndarray) -> float:
    """Mean pairwise cosine among members (excluding the identity diagonal)."""
    n = len(vecs)
    if n < 2:
        return 1.0
    g = vecs @ vecs.T
    return float((g.sum() - np.trace(g)) / (n * (n - 1)))


def _mcs_for(n: int) -> int:
    if n >= 120:
        return VALIDATED_MCS
    return max(5, int(round(n / (VALIDATED_N / VALIDATED_MCS))))


def main() -> None:
    full = json.loads(_FULL.read_text(encoding="utf-8"))
    snap = json.loads(_SNAP.read_text(encoding="utf-8"))
    by_intent = load_joined(_STAGE1, _STAGE2)

    vecs: dict[str, np.ndarray] = {}
    idx: dict[str, dict[int, int]] = {}
    for intent, rows in by_intent.items():
        vecs[intent] = embed([" ".join(r["steps_taken"]) for r in rows]).astype("float64")
        idx[intent] = {r["ticket_id"]: i for i, r in enumerate(rows)}

    def _vecs_of(intent: str, ticket_ids: list[int]) -> np.ndarray:
        return vecs[intent][[idx[intent][t] for t in ticket_ids if t in idx[intent]]]

    def _example(intent: str, t: int) -> dict:
        r = by_intent[intent][idx[intent][t]]
        return {
            "ticket_id": t,
            "what_was_asked": r["what_was_asked"],
            "steps_taken": r["steps_taken"],
        }

    report: dict = {"check1": {}, "check2": {}}

    # ================= CHECK 1 =================
    records = []
    for intent, spec in full.items():
        for c in spec["clusters"]:
            if c["size"] < MIN_N:
                continue
            records.append(
                {
                    "intent": intent,
                    "cluster_id": c["cluster_id"],
                    "size": c["size"],
                    "cohesion": round(cohesion(_vecs_of(intent, c["ticket_ids"])), 4),
                    "label": c["label"],
                }
            )

    # Calibration anchors (from the snapshot, where the known-mixture still exists).
    anchors = []
    for tag, intent, size, truth in (
        ("KNOWN MIXTURE", "reviewer_assignment", 180, "confirmed 61% emergency mixture; UMAP split 67/65/20/20"),
        ("KNOWN CLEAN", "review_submission_help", 806, "humans merged 440+366 into ONE outage procedure"),
        ("KNOWN CLEAN", "reviewer_assignment", 73, "confirmed clean emergency-invite procedure"),
    ):
        c = next((x for x in snap[intent]["clusters"] if x["size"] == size), None)
        if c is None:
            continue
        anchors.append(
            {
                "tag": tag,
                "intent": intent,
                "size": size,
                "cohesion": round(cohesion(_vecs_of(intent, c["ticket_ids"])), 4),
                "ground_truth": truth,
            }
        )

    # Size-controlled trend over all n>=30 production clusters.
    ln = np.log([r["size"] for r in records])
    co = np.array([r["cohesion"] for r in records])
    slope, intercept = np.polyfit(ln, co, 1)
    resid = co - (slope * ln + intercept)
    sd = float(resid.std(ddof=1))
    for r, e in zip(records, resid):
        r["trend_residual"] = round(float(e), 4)
        r["residual_sd"] = round(float(e / sd), 2) if sd else 0.0
    for a in anchors:
        a["trend_residual"] = round(float(a["cohesion"] - (slope * np.log(a["size"]) + intercept)), 4)
        a["residual_sd"] = round(a["trend_residual"] / sd, 2) if sd else 0.0

    mixture = next((a for a in anchors if a["tag"] == "KNOWN MIXTURE"), None)
    cutoff = mixture["trend_residual"] if mixture else -sd
    flagged = [r for r in records if r["trend_residual"] <= cutoff]

    records.sort(key=lambda r: r["trend_residual"])
    report["check1"] = {
        "n_clusters_scored": len(records),
        "min_n": MIN_N,
        "trend": {
            "form": "cohesion ~ a*log(n) + b",
            "slope": round(float(slope), 4),
            "intercept": round(float(intercept), 4),
            "residual_sd": round(sd, 4),
        },
        "calibration_anchors": anchors,
        "flag_rule": (
            "trend_residual <= the KNOWN-MIXTURE anchor's residual "
            f"({cutoff:+.4f}) — a measured boundary, not a chosen sigma"
        ),
        "distribution": records,
        "flagged": [],
    }

    print("=" * 118)
    print(f"CHECK 1 — cohesion of all {len(records)} clusters with n >= {MIN_N} (raw 384-dim)")
    print("=" * 118)
    print(f"trend: cohesion = {slope:+.4f}*log(n) {intercept:+.4f}   residual sd = {sd:.4f}")
    print("\ncalibration anchors (known ground truth):")
    for a in anchors:
        print(f"  {a['tag']:<14} {a['intent']:<24} n={a['size']:>4} "
              f"cohesion={a['cohesion']:.4f} residual={a['trend_residual']:+.4f} "
              f"({a['residual_sd']:+.2f} sd)")
        print(f"                 -> {a['ground_truth']}")
    print(f"\nFLAG RULE: residual <= {cutoff:+.4f} (the known-mixture anchor)\n")
    print(f"{'intent':26s}{'c':>4}{'n':>6}{'cohesion':>10}{'residual':>10}{'sd':>7}  flag")
    print("-" * 118)
    for r in records:
        flag = "<== FLAG" if r["trend_residual"] <= cutoff else ""
        print(f"{r['intent']:26s}{r['cluster_id']:>4}{r['size']:>6}{r['cohesion']:>10.4f}"
              f"{r['trend_residual']:>+10.4f}{r['residual_sd']:>+7.2f}  {flag}")

    # --- decompose the flagged ones -----------------------------------------
    print("\n" + "=" * 118)
    print(f"CHECK 1b — targeted UMAP decomposition of the {len(flagged)} flagged cluster(s)")
    print("=" * 118)
    for r in flagged:
        intent, cid = r["intent"], r["cluster_id"]
        c = next(x for x in full[intent]["clusters"] if x["cluster_id"] == cid)
        ids = [t for t in c["ticket_ids"] if t in idx[intent]]
        raw = _vecs_of(intent, ids)
        mcs = _mcs_for(len(ids))
        reduced, params = reduce_umap(raw, 10, 15)
        labels = _hdbscan(reduced, mcs)
        counts = Counter(labels.tolist())
        n_noise = counts.pop(-1, 0)

        print(f"\n{'-' * 118}\n{intent} c{cid}  n={len(ids)}  cohesion={r['cohesion']:.4f} "
              f"(residual {r['trend_residual']:+.4f})")
        print(f"label: {r['label']}")
        print(f"UMAP(10, nn=15, seed=42) + HDBSCAN(mcs={mcs}"
              f"{' — validated value' if mcs == VALIDATED_MCS else ' — scaled at the validated n/mcs ratio'})"
              f" -> {len(counts)} groups + {n_noise} noise")

        sub = []
        for g, _ in sorted(counts.items(), key=lambda kv: -kv[1]):
            m = labels == g
            gids = [ids[i] for i in range(len(ids)) if m[i]]
            step = max(1, len(gids) // 5)
            picked = gids[::step][:5]
            print(f"\n  SUB-GROUP {g}  n={int(m.sum())}  "
                  f"internal cohesion={cohesion(raw[m]):.4f}")
            for t in picked:
                ex = _example(intent, t)
                print(f"    ticket {t}")
                print(f"      asked: {ex['what_was_asked']}")
                for s in ex["steps_taken"]:
                    print(f"      step - {s}")
                print()
            sub.append(
                {
                    "sub_group": int(g),
                    "size": int(m.sum()),
                    "cohesion": round(cohesion(raw[m]), 4),
                    "examples": [_example(intent, t) for t in picked],
                }
            )
        report["check1"]["flagged"].append(
            {**r, "umap_params": params, "mcs": mcs, "n_noise": int(n_noise), "sub_groups": sub}
        )

    # ================= CHECK 2 =================
    print("\n" + "=" * 118)
    print("CHECK 2 — singleton / pair audit in the agglomerative-fallback intents")
    print("=" * 118)
    fb = [i for i, s in full.items() if s["method"].startswith("agglomerative")]
    print(f"intents using the fallback: {fb}")
    print(f"criterion: the fallback joins only at AVG cosine >= {LINK_SIM} "
          f"(LINK_THRESHOLD=0.35). A singleton whose MAX pairwise sim clears "
          f"{LINK_SIM} but whose AVG does not was split by average-linkage dilution.\n")

    report["check2"] = {"fallback_intents": fb, "link_sim_threshold": LINK_SIM, "items": []}
    for intent in fb:
        spec = full[intent]
        smalls = [c for c in spec["clusters"] if c["size"] <= 2]
        print(f"\n{'=' * 118}\n{intent}  (n={spec['n_tickets']}, {len(spec['clusters'])} clusters, "
              f"{len(smalls)} of size <= 2)\n{'=' * 118}")
        for c in smalls:
            ids = [t for t in c["ticket_ids"] if t in idx[intent]]
            v = _vecs_of(intent, ids)
            best = []
            for o in spec["clusters"]:
                if o["cluster_id"] == c["cluster_id"]:
                    continue
                ov = _vecs_of(intent, o["ticket_ids"])
                if not len(ov):
                    continue
                g = v @ ov.T
                best.append(
                    {
                        "other_cluster": o["cluster_id"],
                        "other_size": o["size"],
                        "avg_linkage_sim": round(float(g.mean()), 4),
                        "max_pairwise_sim": round(float(g.max()), 4),
                        "closest_ticket": int(
                            o["ticket_ids"][int(np.unravel_index(g.argmax(), g.shape)[1])]
                        ),
                        "other_label": o["label"],
                    }
                )
            best.sort(key=lambda b: -b["max_pairwise_sim"])
            top = best[0] if best else None
            if top is None:
                verdict = "no comparison available"
            elif top["max_pairwise_sim"] >= LINK_SIM and top["avg_linkage_sim"] < LINK_SIM:
                verdict = "POSSIBLE THRESHOLD ARTEFACT — close individual neighbour, diluted by average linkage"
            elif top["max_pairwise_sim"] >= LINK_SIM:
                verdict = "POSSIBLE THRESHOLD ARTEFACT — clears the join criterion outright"
            elif top["max_pairwise_sim"] >= 0.55:
                verdict = "BORDERLINE — near but under the join criterion"
            else:
                verdict = "GENUINE ONE-OFF — no close relative in this intent"

            print(f"\n--- c{c['cluster_id']} (n={c['size']}) --- {verdict}")
            print(f"    label: {c['label']}")
            for t in ids:
                ex = _example(intent, t)
                print(f"    ticket {t}")
                print(f"      asked: {ex['what_was_asked']}")
                for s in ex["steps_taken"]:
                    print(f"      step - {s}")
            if top:
                print(f"    nearest: c{top['other_cluster']} (n={top['other_size']}) "
                      f"max={top['max_pairwise_sim']:.4f} via ticket {top['closest_ticket']}, "
                      f"avg-linkage={top['avg_linkage_sim']:.4f}")
                print(f"      its label: {top['other_label'][:100]}")
            report["check2"]["items"].append(
                {
                    "intent": intent,
                    "cluster_id": c["cluster_id"],
                    "size": c["size"],
                    "label": c["label"],
                    "tickets": [_example(intent, t) for t in ids],
                    "nearest_clusters": best[:3],
                    "verdict": verdict,
                }
            )

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote -> {_OUT}")
    print("NOTE: data/mining/stage3_full/ was not modified.")


if __name__ == "__main__":
    main()
