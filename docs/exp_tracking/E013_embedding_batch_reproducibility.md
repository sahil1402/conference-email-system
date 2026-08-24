# E013 — Embedding reproducibility: batch composition perturbs vectors, and density clustering amplifies it

**Date:** 2026-08-21 · **Status:** Finding + working rule. No production code changed.

**Why this is its own entry rather than a section of [E012](E012_stage3_umap_dbcv.md):**
E012 answers one question — *does UMAP + DBCV-argmax replace the Stage 3 clustering
method?* This finding is about the **embedding layer**, one level below, and it
applies to anything in this repo that calls `stage3_cluster.embed()` — every mining
stage, both refinement scans, any future re-run of a published cluster number.
Filed inside a clustering-method experiment it would be unfindable by the person it
is actually written for: someone asking *"why did my numbers change when I didn't
change anything?"* E012's method section links here.

## What happened

While writing `stage3_pending_inspect.py` to dump the sub-groups of
`reviewer_workload_role` c4, a drift assertion fired. The same cluster, same file,
same seeded parameters, produced:

```
expected (refinement scan) : SG0 25 / SG2 20 / SG1 19 / SG3 15 + 12 noise
observed (fresh process)   : SG0 25 / SG2 18 / SG1 16 / SG3 15 + 17 noise
```

**Nine of 91 tickets moved.** Nothing had been edited — `clusters.json` was
byte-identical, UMAP was seeded `random_state=42`, HDBSCAN is deterministic.

## Cause

The two runs embedded the *same 91 texts* in **different-sized batches**:

- the refinement scan embedded all **427** rows of the intent, then sliced out c4's 91
- the inspection script embedded only the **91**

SentenceTransformer pads each batch to the longest sequence in it, so the padded
attention arithmetic differs with batch **composition**. Measured directly:

| | |
|---|---|
| max abs difference per component | **1.006e-07** |
| mean abs difference | 5.07e-09 |
| rows differing at all | 26 of 91 |
| max cosine deviation from 1.0 | 5.9e-08 |

So a **1e-7** perturbation — the 7th decimal place — relabelled 9 tickets. Not a
bug in either script: both are correct, they simply are not the *same computation*.

## Why so small a perturbation matters here

HDBSCAN assigns a point to a cluster or to noise by a **density threshold**. A point
sitting near that threshold is decided by a margin far smaller than 1e-7 in the
reduced space, so an arbitrarily small input change flips it. UMAP compounds this:
it is a stochastic optimiser whose neighbour graph can reorder on ties.

This is the same shape as E012's central finding one layer up — that DBCV-argmax was
choosing between configurations on margins 40–50× below its own seed noise. **Both
are cases of a decision rule operating below its input's reproducibility floor.**

## How much is actually unstable — measured, not assumed

Ten runs (2 vector variants × 5 UMAP seeds `{42, 7, 123, 2024, 31337}`), scored with a
**co-assignment / consensus matrix** rather than by comparing labels — group ids are
not comparable across runs, but *"did these two tickets stay together"* is:

| sub-group | n | mean co-assignment | mean noise rate | reading |
|---|---|---|---|---|
| SG0 | 25 | 1.000 | 0.020 | core stable |
| SG2 | 20 | 1.000 | 0.010 | core stable |
| SG1 | 19 | **0.879** | 0.063 | core stable, edge jitters |
| SG3 | 15 | 1.000 | 0.007 | core stable |

**Cores are solid; boundaries jitter by roughly ±3 tickets.** Members that are in a
group stay together essentially always — what moves is whether a marginal ticket is
*in the group at all* or in noise. That is what makes the decomposition safe to act
on while its exact sizes stay provisional, and it is why
`reviewer_workload_role` SG1 carries a `stability_note` in `clusters.json`.

*Caveat on the measurement:* the second vector variant is Gaussian jitter at the
measured 1e-7 magnitude, used so the check runs offline and reproducibly. The real
batch perturbation is **structured**, not Gaussian, so this may understate it — the
9-ticket move is the empirical number, the table is the shape.

## Scope — what this does and does not affect

**Does NOT affect production retrieval.** `faiss_retriever` embeds the corpus to build
its index and embeds a query at request time; a 1e-7 shift moves cosine scores by
~1e-7, which cannot reorder a ranking unless two chunks are tied to seven decimals.
Nothing here calls for a change to the retriever, and no such change was made.

**Does affect** any density-based clustering, any published cluster count, and any
claim of the form "re-running this reproduces the artefact".

## Working rule

1. **To reproduce a published clustering number, reproduce the whole pipeline** —
   including *what else was in the embedding batch*. Slicing a saved
   whole-intent embedding matrix is reproducible; re-embedding a subset is not.
   `stage3_pending_inspect.py` carries this as an inline comment at the call site.
2. **Assert expected partitions instead of trusting them.** The drift here was caught
   only because the script asserted the scan's group sizes. One-shot scripts in
   `scripts/data_mining/stage3_applied/` already assert exact partitions before
   writing; that convention is what turned a silent 9-ticket discrepancy into a
   loud failure.
3. **Report cluster sizes from a decomposition as provisional at the boundary**
   (±3 here), and cite co-assignment rather than a single run when the exact count
   carries weight.
4. **Do not build a selection rule on margins near this floor** — the E012 lesson,
   restated: measure the reproducibility floor first, then check the margin clears it.

## Reproduction

```bash
cd backend
# the drift, and the batch-composition measurement behind it
HF_HUB_OFFLINE=1 python scripts/data_mining/stage3_pending_inspect.py
```
Its ITEM 4 asserts the scan's partition and ITEM 4b prints the stability table.
Artefacts: `data/mining/stage3_v2_test/pending_inspection.json`
(`item4_drift_check`, `item4b_stability`).
