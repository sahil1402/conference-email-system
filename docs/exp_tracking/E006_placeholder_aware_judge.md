# E006 — Placeholder-aware LLM-judge on the current system

**Date:** 2026-07-19 · **Trigger:** the drafter emits `[CHAIR: …]` placeholders for
chair-only / non-KB decisions (the Phase-7 placeholder contract), but the E004-era
judge rubric scored a deferral as *incompleteness* ("a correct [CHAIR] deferral is
partial credit, not full"). That systematically under-rated human-review drafts and
gave no signal on whether the deferrals were actually *useful*. This run reworks the
rubric to judge the draft as "a draft a chair will finish."

## Setup

- **Harness:** `backend/scripts/judge_testset.py` — regenerates all 20 drafts through
  the **current** pipeline on a throwaway SQLite DB, then scores each vs the chair's
  actual reply. Same model drafts and judges (`gpt-5.5`, OpenAI-compatible endpoint).
- **System under test:** `main` — `QUERY_STRATEGY=distill`, `RETRIEVAL_BACKEND=fusion`,
  **leaf-title embedding (E005)** and the **over-answering scope fix** both live.
- **Data (gitignored):** `data/eval_real/testset_20.jsonl` (20 real AAAI tickets, each
  with the chair's actual reply). Report/drafts → `data/eval_real/testset_20_*` (gitignored).

## Rubric change (this experiment)

- **Placeholders are EXPECTED, not incompleteness.** The judge is told the draft is a
  chair-facing draft to be *completed*; an appropriate `[CHAIR: …]` deferral counts as
  the part being *addressed* (full completeness credit), and helpfulness is judged
  *assuming the chair fills the placeholders*.
- **New dimension `placeholder_quality`** (1–5): quality of the draft's *deferral
  judgment* — defers exactly the right things (chair-only / non-KB) with specific,
  actionable hints, neither over-deferring (vague/unnecessary) nor under-deferring
  (guessing what it should have placeheld). 5 if no deferral was needed and none used.
- The judge now also receives the draft's **`notes_for_chair`** so it can assess
  whether each deferral is actionable.

## Results (n=20, current system)

| dimension | mean |
|---|---|
| factual_correctness | 3.80 |
| completeness | 3.40 |
| helpfulness | 3.15 |
| **placeholder_quality** | **3.25** |
| tone | 4.45 |
| overall | 3.30 |

11/20 drafts carry ≥1 `[CHAIR]` placeholder.

## Findings

1. **The rubric now credits good deferrals.** Drafts that defer well score high:
   `15865` (1 ph) overall **5** / pq 5; `16711` (1 ph) overall **5**; `20660` (2 ph)
   overall 4. Under the old rubric a placeholder tanked completeness/helpfulness.
2. **`placeholder_quality` surfaces real deferral defects** the other dims miss:
   - `16163` → **pq 1**: deferred "check the submission status" to the chair, but the
     chair's reply is that support *cannot* check papers (privacy) — a *wrong* deferral.
   - `16650` → **pq 2**: *under*-answered — redirected/deferred info it should have
     answered (student-volunteer program).
3. **The dominant loss is KB coverage, not drafting.** Several low-overall tickets are
   the chair giving information absent from the 93-chunk corpus (the ~⅓ answerability
   gap from **E001**): `12818` ("a checklist inside supplementary material is
   acceptable"), `15749` (chair acknowledges the guideline itself is unclear), `16650`
   (student-volunteer info). No prompt/retrieval change fixes these — it is the
   **KB-enrichment** lever.

## Threats to validity

- **Not a clean before/after vs the old ~2.4 completeness/helpfulness numbers** — that
  earlier report used the *old* rubric *and* a pre-E005/pre-over-answering-fix system,
  so two variables changed. Treat E006 as the **current-system baseline under the
  correct rubric**, not a delta measurement.
- Judge = same model family as the drafter; single run (E004 noted ±0.1 overall is
  within re-draft noise). n=20.

## Recommendation

- **KB enrichment is the top lever** (confirms E001's follow-up #1): the highest-value
  next step is closing corpus gaps (supplementary-material rules, volunteer/scholar
  program, guideline clarifications) rather than more retrieval/prompt tuning.
- `placeholder_quality` 3.25 shows the drafter's *deferral judgment* has room —
  over-deferral (`16163`, `16650`) is the recurring failure; worth a targeted look.

## Reproduction

```bash
cd backend
export PATH=/u/jpang1/miniconda3/envs/autoexp/bin:$PATH
# Run with the app STOPPED — the judge's ML stack + a live uvicorn/Next server together
# exhaust the login-node thread budget (pyo3 ThreadPoolBuildError / EAGAIN). Thread caps:
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
       RAYON_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false HF_HUB_OFFLINE=1
python scripts/judge_testset.py     # writes data/eval_real/testset_20_judge_report.md (gitignored)
```
