# E012 — Stage 3 clustering v2: UMAP reduction + DBCV-based selection

**Date:** 2026-08-21 · **Status:** DO NOT ADOPT wholesale. One component (UMAP as a
targeted residual-decomposition tool) is a clear win and is recommended; the other
(DBCV argmax as the selection rule) **fails on its own terms** and must not ship.

**Trigger:** Stage 3 v1 embeds `steps_taken` with the retriever's local 384-dim
embedding model and runs HDBSCAN directly on those raw vectors, choosing
`min_cluster_size` for mid-tier intents with a hand-rolled "lowest noise, plateau
as tie-break" rule. Two known defects motivated a v2: (a) `reviewer_assignment`'s
residual cluster stayed a ~46% emergency-reviewer mixture even after isolated
re-clustering — a documented high-dimensionality symptom; (b) the original
stability heuristic picked a demonstrably worse `mcs` before being corrected by
hand. v2 therefore adds UMAP before clustering and replaces the plateau rule with
DBCV argmax.

**Parallel test only. `data/mining/stage3_full/` was never written to** — all four
files still carry their 2026-08-08 mtimes after every run in this session, and
`stage3_cluster_v2.py` hard-refuses an `--out` path containing `stage3_full`
(tested: it exits with `refusing to write into stage3_full/`). Note that
`git diff` proves *nothing* here — `.gitignore:101` ignores `data/mining/`
wholesale, so these artefacts are untracked; mtimes are the evidence.

## Setup

- **Pipeline:** `backend/scripts/data_mining/stage3_cluster_v2.py` → all 14 intents →
  `data/mining/stage3_v2_test/clusters_v2.json`. Imports `load_joined` / `embed` /
  `_hdbscan` / `_agglomerative` from `stage3_cluster.py` rather than re-implementing
  them, so inputs, embeddings and the 3,655-ticket join are bit-identical to v1 and
  the only variables are the space and the selection rule.
- **UMAP:** `n_components=10`, `n_neighbors` swept `{5,15,30}`, `min_dist=0.0`
  (correct when the output feeds a clusterer, not a plot), `metric=cosine`,
  `random_state=42`. Local CPU only — no API call, no credential, no spend.
- **mcs grid:** absolute `{5,8,10,12,15,20,25,30}` ∪ proportional
  `round(n·{0.03,0.05,0.08,0.12})`, clipped to `[3, n//3]`. Unioning in absolute
  values removes v1's documented blind spot (`0.03·597 = 18` meant `mcs=15` was
  never triable on a large intent, which is why v1 needed a hard-coded `LARGE_MCS`).
  With a real metric available the `n>=200` tier was dropped and every `n>=30`
  intent is swept.
- **Unchanged on purpose:** `n<30` stays raw-space agglomerative (`LINK_THRESHOLD`
  is a *cosine* distance calibrated on unit embeddings — meaningless against UMAP
  coordinates); noise recovery still runs; merge flagging still computes centroid
  similarity in **raw** space, where `MERGE_SIMILARITY=0.85` was calibrated;
  `min_samples=1`; nothing auto-merges.

### On task item 2 — checked, not assumed

`sklearn.cluster.HDBSCAN` **does not** expose `relative_validity_`. Fitted against
the installed sklearn 1.9.0, the estimator carries only `labels_`,
`probabilities_`, `n_features_in_`. No `dbcv` / `DBCV` / `dbcv-metric`
distribution exists on PyPI either.

A standalone implementation was required regardless of packaging: the whole point
is scoring v1's **human-edited** labelling (merges applied, `desk_reject_appeal`
split three ways, `reviewer_assignment` reverted), which is a label vector no
estimator ever emitted, and `relative_validity_` can only score the fit that
produced it. Hence `backend/scripts/data_mining/dbcv.py` (faithful Moulavi et al.
2014). `hdbscan` (McInnes) **was** installed and its `relative_validity_` is
recorded beside every chosen trial as an independent cross-check — never as the
selector. The two agree closely (0.771/0.773, 0.779/0.735, 0.913/0.919, and
0.545/0.545 exactly on `desk_reject_appeal`).

Implementation notes that matter: the `(1/dist)**d` core-distance term overflows
at `d=384`, so it is computed in log space via `logsumexp` (exact, not an
approximation); distances use the Gram identity so one 1367-ticket intent costs
O(n²) instead of a 5.7 GB `n×n×d` array. Two numbers are reported — `dbcv`
(|O| = all points, the faithful definition, inherently coverage-penalised, used
for selection) and `dbcv_clustered` (clustered points only, diagnosis).

## Results — comparison table

v1 = production, **human-reviewed**. DBCV scored in the neutral raw 384-dim space
for both, since cross-space DBCV comparison is meaningless (the core-distance term
is dimensionality-dependent).

| intent | n | v1 cl | v1 noise | v1 cov | v1 DBCV | v2 cl | v2 noise | v2 cov | v2 DBCV | ARI | AMI |
|---|---|---|---|---|---|---|---|---|---|---|---|
| review_submission_help | 1367 | 14 | 260 | 81% | **+0.016** | 5 | 0 | 100% | −0.452 | 0.196 | 0.410 |
| reviewer_assignment | 597 | 13 | 160 | 73% | −0.021 | 2 | 5 | 99% | −0.334 | 0.033 | 0.196 |
| review_decision_appeal | 449 | 9 | 122 | 73% | **+0.041** | 9 | 9 | 98% | −0.152 | 0.462 | 0.600 |
| reviewer_workload_role | 427 | 6 | 14 | 97% | −0.002 | 3 | 0 | 100% | −0.102 | 0.505 | 0.619 |
| submission_requirements | 149 | 11 | 28 | 81% | +0.049 | 14 | 4 | 97% | **+0.095** | 0.429 | 0.564 |
| desk_reject_appeal | 146 | 11 | 28 | 81% | **+0.023** | 2 | 22 | 85% | −0.044 | 0.151 | 0.358 |
| committee_invitation | 145 | 9 | 33 | 77% | **+0.004** | 10 | 10 | 93% | −0.179 | 0.181 | 0.401 |
| cms_support | 91 | 7 | 7 | 92% | **+0.092** | 4 | 0 | 100% | −0.052 | 0.304 | 0.454 |
| submission_format_policy | 79 | 5 | 30 | 62% | **+0.134** | 3 | 0 | 100% | −0.084 | 0.336 | 0.540 |
| submission_upload_help | 62 | 5 | 1 | 98% | **+0.029** | 2 | 0 | 100% | −0.021 | 0.371 | 0.556 |
| anonymity_violation | 53 | 2 | 10 | 81% | **+0.083** | 3 | 0 | 100% | −0.127 | 0.355 | 0.420 |
| author_list_change | 50 | 2 | 16 | 68% | **+0.154** | 2 | 0 | 100% | +0.083 | 0.438 | 0.582 |
| author_profile_compliance | 29 | 13 | 0 | 100% | +0.051 | 13 | 0 | 100% | +0.051 | 1.000 | 1.000 |
| paper_bidding | 11 | 4 | 0 | 100% | — | 4 | 0 | 100% | — | 1.000 | 1.000 |

**v1 wins the neutral-space comparison 10–2** (v1 positive on 10 of 12 scorable
intents, v2 negative on 11 of 12). The two `n<30` intents are identical by
construction (raw agglomerative in both), which is a useful control: ARI = 1.000
confirms the harness aligns label vectors correctly.

v2 does raise coverage sharply (noise → 0 almost everywhere). That is UMAP
assigning nearly every point rather than v2 discovering more structure — and it
is why cluster counts *fell* on the large intents at the same time.

### The same table in v2's own UMAP space — and why it is circular

| intent | n | v1 DBCV (umap) | v2 DBCV (umap) | delta | v2 relative_validity |
|---|---|---|---|---|---|
| review_submission_help | 1367 | −0.671 | +0.771 | **+1.441** | +0.773 |
| reviewer_assignment | 597 | −0.413 | +0.546 | +0.959 | +0.478 |
| review_decision_appeal | 449 | −0.295 | +0.553 | +0.848 | +0.478 |
| reviewer_workload_role | 427 | −0.288 | +0.779 | +1.067 | +0.735 |
| submission_requirements | 149 | −0.017 | +0.683 | +0.700 | +0.552 |
| desk_reject_appeal | 146 | −0.165 | +0.545 | +0.710 | +0.545 |
| committee_invitation | 145 | −0.082 | +0.588 | +0.671 | +0.532 |
| cms_support | 91 | −0.391 | +0.668 | +1.059 | +0.665 |
| submission_format_policy | 79 | +0.195 | +0.809 | +0.614 | +0.797 |
| submission_upload_help | 62 | +0.171 | +0.913 | +0.743 | +0.919 |
| anonymity_violation | 53 | +0.612 | +0.613 | +0.000 | +0.651 |
| author_list_change | 50 | +0.629 | +0.933 | +0.304 | +0.938 |

v2 "wins" by +0.6 to +1.4 here — **and this table is near-tautological.** UMAP with
`min_dist=0` manufactures compact, well-separated blobs, so any density index
computed on its own output is inflated; and v2's labels were *chosen* by maximising
DBCV in this very space, while v1's never saw it. It is reported only to show that
the apparent win does not survive leaving UMAP's coordinate system (see the
neutral table above). **This is the trap to avoid if anyone revisits this work.**

## Why DBCV argmax fails: the margins are far below the metric's noise floor

Refitting UMAP under five random seeds at a **fixed** `(n_neighbors, mcs)`:

| intent | mcs | DBCV per seed | spread | clusters per seed |
|---|---|---|---|---|
| review_submission_help | 25 | +0.522 +0.423 +0.657 +0.464 +0.482 | 0.234 | 24, 27, 17, 28, 28 |
| reviewer_assignment | 72 | +0.546 +0.335 +0.315 +0.348 +0.385 | **0.231** | 2, 2, 2, 2, 2 |
| desk_reject_appeal | 30 | +0.415 — +0.074 +0.437 +0.562 | **0.488** | 2, **0**, 2, 2, 2 |

Against the margins the argmax actually decided on:

| intent | winner | DBCV | best ≥8-cluster alternative | DBCV | margin | seed floor | verdict |
|---|---|---|---|---|---|---|---|
| review_submission_help | 5 cl | 0.7706 | 9 cl | 0.7155 | 0.0551 | 0.234 | INDISTINGUISHABLE |
| reviewer_assignment | 2 cl | 0.5463 | 21 cl | 0.5418 | **0.0045** | 0.231 | INDISTINGUISHABLE |
| desk_reject_appeal | 2 cl | 0.5450 | 13 cl | 0.5367 | **0.0083** | 0.488 | INDISTINGUISHABLE |

The margins are **40–50× smaller than the seed-only spread**. `reviewer_assignment`'s
winning trial scores +0.546 on seed 42 but +0.315 on seed 123 — its 2-cluster
result is a lucky draw, not a finding, and on most seeds the 21-cluster solution
would have won instead. `desk_reject_appeal` at mcs=30 produced **zero** clusters
on one seed.

This is the **same pathology as v1's original plateau rule** — an arbitrary
tie-break presented as a criterion — which is precisely what the rewrite was
supposed to eliminate. A better-credentialed metric did not make the selection
better-founded, because nobody had checked whether its differences were resolvable.

`n_components` is in the same position: best DBCV moved only 0.05–0.13 across
{5,10,15,25} (`--probe-ncomp`), also below the floor. 10 stands as the task's
starting value, **not** as a tuned optimum.

## Ablation: the two changes push in opposite directions

Same grid, one variable per cell (clusters / noise / DBCV-in-that-space):

| intent | n | A raw + v1 rule | B umap + v1 rule | C raw + DBCV | D umap + DBCV (=v2) |
|---|---|---|---|---|---|
| review_submission_help | 1367 | 24 / 344 / +0.142 | 3 / 0 / +0.489 | 3 / 473 / +0.144 | 5 / 0 / +0.771 |
| reviewer_assignment | 597 | 4 / 321 / +0.021 | 2 / 5 / +0.546 | **17** / 323 / +0.048 | 2 / 5 / +0.546 |
| review_decision_appeal | 449 | 2 / 166 / +0.127 | 3 / 0 / +0.320 | 2 / 166 / +0.127 | 9 / 9 / +0.553 |
| reviewer_workload_role | 427 | 3 / 97 / +0.063 | 3 / 0 / +0.522 | 7 / 117 / +0.066 | 3 / 0 / +0.779 |
| submission_requirements | 149 | 2 / 55 / +0.014 | 8 / 2 / +0.400 | 9 / 65 / +0.150 | 14 / 4 / +0.683 |
| desk_reject_appeal | 146 | 3 / 32 / +0.060 | 2 / 0 / +0.415 | 3 / 32 / +0.060 | 2 / 22 / +0.545 |
| committee_invitation | 145 | 3 / 68 / +0.032 | 2 / 0 / +0.271 | 6 / 76 / +0.082 | 10 / 10 / +0.588 |
| cms_support | 91 | 2 / 17 / +0.022 | 3 / 0 / +0.397 | 6 / 29 / +0.107 | 4 / 0 / +0.668 |
| submission_format_policy | 79 | 5 / 30 / +0.134 | 3 / 0 / +0.809 | 7 / 31 / +0.135 | 3 / 0 / +0.809 |
| submission_upload_help | 62 | 2 / 9 / +0.040 | 2 / 0 / +0.913 | 6 / 12 / +0.111 | 2 / 0 / +0.913 |
| anonymity_violation | 53 | 2 / 10 / +0.083 | 3 / 0 / +0.613 | 5 / 30 / +0.134 | 3 / 0 / +0.613 |
| author_list_change | 50 | 2 / 16 / +0.154 | 2 / 0 / +0.933 | 3 / 17 / +0.164 | 2 / 0 / +0.933 |

- **A→B (space only):** UMAP drives noise to ~0 and *coarsens* the large intents
  (24 → 3 clusters on `review_submission_help`).
- **A→C (rule only):** in raw space DBCV argmax goes the **other** way and prefers
  *finer* partitions (`reviewer_assignment` 4 → 17, `submission_requirements` 2 → 9,
  `cms_support` 2 → 6).

The two changes therefore have opposing effects and were confounded by shipping
them together. Anyone iterating on this must vary one at a time.

## Validation against the four settled human decisions

### [1] `review_submission_help` outage merge (2 clusters → n=806 by hand) — **v2 DISAGREES**

v2 does not reproduce the merge; it goes the opposite way and **fragments the 806
into 4 clusters**: 373 (89% pure), 243 (98%), 123 (97%), 67 (98%). High purity
means the fragments are genuinely outage tickets, so this is a 4-way subdivision
of one human-confirmed procedure, not a mixture. The human call was that these
are one September OpenReview outage workflow; v2 would put that merge decision
back on the table four times over.

### [2] `review_submission_help` troubleshooting cluster kept separate (premerge c7, n=30) — **v2 DISAGREES, and this is the worst result**

27 of 30 (90%) land in v2's c1 — **the same cluster that dominates the outage
set**. v2 merges precisely what the human deliberately refused to merge despite
a 0.888/0.851 similarity flag, on the documented grounds that it is individual
access troubleshooting with no deadline extension. Purity in that host cluster is
6%, so the distinction is not preserved anywhere.

*(Identification verified, not guessed: premerge `c0`(440) + `c1`(366) = final
`c0`(806) exactly, and premerge `c7`(30) ≡ final `c4` as identical ticket sets.)*

### [3] `reviewer_assignment` emergency-invite vs. the ~46% mixed residual — **v2 PARTIALLY IMPROVES; the mixture does NOT simply persist**

Whole-intent v2 gives only 2 clusters, but they are meaningfully de-mixed: the
180-ticket v1 residual (59% emergency by keyword) splits 114→c0 and 66→c1, where
**c1 is 88% emergency and c0 is 28%**. Against v1's 59% blob that is real
separation, though it comes at the cost of a 195-ticket cluster replacing v1's
100%-pure 73-ticket emergency cluster.

**Tested directly, in isolation** (`stage3_v2_residual_probe.py`, mirroring
`stage3_split_c0.py`'s isolation logic with the space as the only variable):

| space | mcs | resulting purity profile (size @ emergency%) |
|---|---|---|
| raw 384-dim | 8–15 | **136 @ 61%**, 16 @ 81% |
| raw 384-dim | 3 | 40 @ 85%, 10 @ 20%, 10 @ 100%, 9 @ 33%, … (73 to noise) |
| UMAP 10-dim | 20 | **67 @ 10%**, **65 @ 92%**, 20 @ 80%, 20 @ 85% |
| UMAP 10-dim | 8 | 27 @ 96%, 21 @ 5%, 20 @ 80%, 17 @ 6%, 17 @ 76%, 17 @ 100%, … |

Raw space reproduces the documented failure exactly — a 136-ticket blob at 61%
that no `mcs` breaks apart. UMAP splits the same 180 tickets cleanly. Largest
cleanly-single-procedure cluster: **raw 40 emergency / 6 non-emergency; UMAP 65 /
67.** Crucially the purity metric is **independent of both DBCV and UMAP**, so
unlike the headline tables this result is not circular. **UMAP genuinely fixes the
specific high-dimensionality defect it was brought in for.**

### [4] `desk_reject_appeal` three-way split (upheld / reversed / triage) — **v2 WRONGLY MERGES ALL THREE**

All three groups land dominantly in v2's c0: upheld 93%, reversed 100%, triage
100%. v2 produced only 2 clusters for the entire 146-ticket intent, so the
three-way distinction is erased.

The task anticipated this check would confirm nothing *broke* (UMAP adds no
outcome-awareness, so it was not expected to *improve*). It is worse than
neutral: v2 actively collapses opposite-outcome procedures — appeal upheld vs.
desk rejection reversed vs. SPC triage — that a human separated by reading
`steps_taken`. Note the cause is the **selection rule, not UMAP**: on this intent
a 13-cluster UMAP solution existed at DBCV 0.5367 versus the winner's 0.5450.

## Decision

**Do not adopt v2 as the production Stage 3 method.** It disagrees with 3 of the 4
settled human decisions, loses the neutral-space DBCV comparison 10–2, and its
selection rule is not reproducible across random seeds. Adopting it would require
re-doing all of the manual merge/split review — and would spend that effort on a
partition that is measurably worse in the space the semantics actually live in.

**Adopt this one piece:** UMAP as a *targeted decomposition tool* for stubborn
residual clusters, invoked deliberately on a named cluster, the way
`stage3_split_c0.py` was. It is the only claim here with independent, non-circular
support, and it resolves defect (a) outright.

**If the selection rule is revisited,** the lesson is not "DBCV is a bad metric"
but that **no selection rule should be built on differences smaller than its own
seed noise**. Any replacement needs (i) a measured noise floor first, (ii)
averaging over seeds rather than a single fit, and (iii) a tie-break toward finer
granularity within the noise band, since workflow discovery wants structure and
the metric provably cannot distinguish 2 clusters from 13 here. Do not evaluate in
UMAP space.

## Manual inspection of the one adopted finding (2026-08-21, follow-up)

The residual decomposition was re-run and read by hand before any decision, to
the same standard as every prior Stage 3 manual review.
`backend/scripts/data_mining/stage3_v2_residual_inspect.py` reproduces the split
and dumps real `steps_taken` per group →
`data/mining/stage3_v2_test/residual_inspection.json`. **Nothing was applied to
`data/mining/stage3_full/clusters.json`.**

Profile reproduced exactly: 67 @ 10%, 65 @ 92%, 20 @ 85%, 20 @ 80% emergency
keyword, + 8 to noise.

| group | n | emerg% | cos to v1 c1 | procedure read from the tickets | verdict |
|---|---|---|---|---|---|
| 1 | 65 | 92% | **0.960** | *Reviewed the request → invited the named person → confirmed publicly.* Executes a specific invite. | **FOLD into c1** |
| 2 | 67 | 10% | 0.727 | *Forwarded to the reassignments team / removed assignments* — workload-driven, request **granted**. | keep separate |
| 0 | 20 | 80% | 0.806 | *Explains who may invite and through what channel* (only ACs/chair team; via private Official Comment; screening required). Guidance, not execution. | keep separate |
| 3 | 20 | 85% | 0.795 | *The reviewer's own side* — confirm status, remove assignment, stop notifications. Requester is the reviewer, not an SPC. | keep separate |

**Group 1 → c1 is supported by the text, not just the centroid.** Group 1's
16730 "Invited Amish Sethi to serve as requested for paper 29433 → Confirmed
publicly to the SPC" is step-for-step c1's 17761 "Invited Sayed to serve as the
requested emergency PC member". This is the opposite situation to the misleading
0.906 similarity `stage3_split_c0.py` rejected: there the number was driven by
emergency-keyword fraction while the procedures differed, here the procedures
match and the number merely agrees.

**The keyword proxy undercounts, so fold on PROCEDURE not on the flag.** Group 1's
16402 has no "emergency" token yet runs the identical procedure ("Invited
Chenyang Zhao to serve as a reviewer for paper 23160" after the assigned reviewer
became unavailable). Its 92% is a floor.

**Group 2 must NOT fold into c2 despite the shared topic — the outcomes are
opposite.** c2 (n=48) predominantly *declines* reassignment and requires
completion ("Declined to reassign the paper and required the requester to complete
the review", "Informed the requester that they were still required to review paper
20888", usually under reciprocal-reviewer obligation), as does c6 (n=15). Group 2
*grants* it and routes to the reassignments team. This is the same
embeddings-are-blind-to-outcome hazard as `desk_reject_appeal`'s upheld/reversed
split, and it is why the low 0.727 similarity is the trustworthy signal here.

**Group 0 vs c4** is likewise a real distinction: c4 (n=22) *declines* late
emergency-reviewer requests on deadline grounds; Group 0 explains the mechanism
and authority irrespective of deadline. One bleed noted — Group 0's 17955
declines on lateness and sits closer to c4.

**Group 3 is the most valuable discovery:** a procedure with no existing cluster
at all (reviewer-initiated status/unassignment handling), and the direction of the
request is inverted relative to groups 1 and 0.

If applied as read, `reviewer_assignment` would go 13 → 15 clusters, c1 would
grow 73 → 138, and coverage would fall 73.2% → 71.9% (−8 tickets to noise). That
small coverage loss is the honest price of the decomposition and matches the
trade-off `stage3_split_c0.py` already accepted for this cluster.

## Threats to validity

- **Emergency purity is a keyword proxy** (`"emergency"` in `steps_taken` /
  `what_was_asked`), matching 288/597 of the intent. It is coarse, but it is the
  same signal the manual review reasoned about and it is applied identically to
  both arms, so the *comparison* holds even where the absolute number is rough.
- **v1 is a moving target by design.** It is scored *after* human editing, which is
  the correct baseline (that is the artefact in production) but means v1 carries
  human judgement v2 had no access to. v2 losing to it is expected to some degree;
  the ablation and seed study are what make the conclusion independent of that.
- **DBCV at d=384 degenerates toward single-linkage** (the log-space core distance
  is dominated by the nearest neighbour). The neutral-space column is therefore
  less discriminative than the 10-dim one — but it is identical for both
  labellings, so it remains a fair comparison.
- **`min_samples=1` throughout**, inherited from v1. UMAP+HDBSCAN practice often
  raises it; not varied here to keep the space and the rule the only moving parts.
- Two intents (`author_profile_compliance` n=29, `paper_bidding` n=11) are
  untouched raw-space agglomerative in both versions, so they carry no signal
  either way — they serve as an alignment control.

## APPLIED — final decision, written to production (2026-08-21)

The decomposition above was **applied to `data/mining/stage3_full/clusters.json`**
by `backend/scripts/data_mining/stage3_applied/stage3_apply_e012_residual_split.py`
(one-shot, snapshot at `clusters_pre_e012.json`). `reviewer_assignment` only —
the other 13 intents were byte-compared against the snapshot and are unchanged.
**No part of the v2 pipeline was adopted.**

**Manual correction applied first.** Ticket **17955** moved from group 0 to the
decline-late-requests cluster. Its steps ("declined both reviewer-addition
requests because it was too late in the submission process", "Advised the
requester to recommend desk rejection") are that cluster's procedure, not group
0's explain-the-authorised-channel procedure; it was the single bleed flagged
during inspection. **group 0 → n=19, decline cluster → n=23.** Recorded as
`assigned_by_inspection` + `correction_note` on the destination.

**Changes:** group 1 (n=65) folded into the emergency-invite cluster (73 → **138**,
`merged_from` + `merge_note`); the n=180 residual removed; three new clusters
added, each carrying `split_from: "c0 (n=180, UMAP decomposition, E012)"` and
`split_group`; 8 unfitted members returned to noise. cluster_ids were renumbered
positionally by size per project convention, so every target was located by
**ticket membership, never by id** — the lesson `stage3_apply_decisions.py`
already recorded.

Final `reviewer_assignment`: **15 clusters** (6 primary + 9 recovered), 429
clustered, **168 noise, 71.9% coverage** (from 13 / 437 / 160 / 73.2%).

| c | n | note |
|---|---|---|
| c0 | **138** | emergency-invite, **merged** (73 + group 1's 65) |
| c1 | **67** | **NEW** group_2 — grant reassignment, route to assignments team |
| c2 | 48 | (unchanged) handle reassignment requests / obligation fulfilled |
| c3 | 29 | (unchanged) confirm assignment status, escalate visibility |
| c4 | **23** | decline late emergency-reviewer requests **+ 17955** |
| c5 | **20** | **NEW** group_3 — reviewer's own status / stop notifications |
| c6 | 19 | (unchanged) communicate delay + expected window |
| c7 | **19** | **NEW** group_0 — who may invite, and through what channel |
| c8–c14 | 15, 13, 11, 8, 8, 6, 5 | (unchanged) |

**Corpus-wide totals updated:** **111 → 113 clusters**, noise **709 → 717**,
coverage **80.6% → 80.4%** (3,655 tickets; 2,946 → 2,938 clustered). The −8 is the
honest price of the decomposition — the same trade `stage3_split_c0.py` accepted
when it returned 81 points to noise rather than keep a grab-bag counted as
"explained".

**`candidate_merges` recomputed for this intent only** (centroids move when
clusters change): the two stale flags are gone, replaced by a single new advisory
flag **c4 ↔ c5 at 0.860** — decline-late-requests vs. the reviewer's-own-status
cluster. **Advisory only, not acted on.** Worth noting it is exactly the pattern
this project has twice found misleading: both clusters are emergency-heavy and
share vocabulary, but one is a chair declining an SPC's request and the other is a
reviewer's own assignment being confirmed or removed — different requester,
different action.

**Two things deliberately NOT changed**, flagged for a future call:
- **c0 keeps its original human-written label** ("Reviewed emergency review needs
  and assigned or deferred emergency reviewers based on deadline status and
  reviewer availability"). It still covers the merged content, but now that 65 of
  138 members are specifically *invite the named person and confirm publicly*, a
  sharper label may be warranted. Not rewritten unilaterally.
- The intent's **`c0_split_note` was superseded, not deleted**: it asserted
  "Nothing folded into the emergency-reviewer cluster: no sub-cluster is
  procedurally equivalent to it". That was correct **in raw 384-dim space** and is
  now false under UMAP, so it is retained verbatim under a `SUPERSEDED by
  e012_note` prefix rather than removed — the raw-space finding is still a true
  fact about that space, and the reason the UMAP result matters.

## Follow-on: E013 (embedding reproducibility)

Applying this method a second time surfaced a finding one layer below it: the same
texts embedded in a **different-sized batch** differ by ~1e-7 (SentenceTransformer
pads per batch), and that was enough to relabel 9 of 91 tickets in a UMAP+HDBSCAN
decomposition. Cores proved stable across 10 runs; boundaries jitter by about +/-3.
Same shape as this experiment's central result — a decision rule operating below its
input's reproducibility floor. Written up separately because it concerns the
embedding layer and applies to every script here that calls `embed()`:
**[E013](E013_embedding_batch_reproducibility.md)**.

## Reproduction

```bash
cd backend
# adds umap-learn (+numba, pynndescent, llvmlite) and hdbscan for the cross-check
pip install umap-learn hdbscan

python scripts/data_mining/stage3_cluster_v2.py                 # all 14 -> stage3_v2_test/clusters_v2.json
python scripts/data_mining/stage3_cluster_v2.py --probe-ncomp   # n_components study
python scripts/data_mining/stage3_compare_v1_v2.py              # comparison tables + 4 validation checks
python scripts/data_mining/stage3_v2_ablation.py                # 4-cell ablation + seed noise floor
python scripts/data_mining/stage3_v2_residual_probe.py          # the residual decomposition win
```

All read-only with respect to `data/mining/stage3_full/`; artefacts land in
`data/mining/stage3_v2_test/` (`clusters_v2.json`, `comparison.json`,
`ablation.json`, `residual_probe.json`). No model calls, no spend.
