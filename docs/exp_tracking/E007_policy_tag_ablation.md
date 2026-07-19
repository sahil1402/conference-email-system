# E007 — Policy-tag ablation: do chunk `tags` earn their place in BM25?

**Date:** 2026-07-19 · **Trigger:** the classification/KB audit
(`docs/local/CLASSIFICATION_REWORK.md` §B4) found the 93-chunk corpus's `tags`
are **auto-generated** (per-doc YAML frontmatter + `heading_tags` = lowercased
content-words of each section heading, `chunk_policies.py:65-68`), consumed **only
by BM25** (`retriever.py:82`, appended to `title + content`), ignored by FAISS
(E005 embeds leaf-title + content), and **largely redundant** — `heading_tags`
duplicate words already in the chunk title. Hypothesis: dropping tags leaves
retrieval flat. This run tests it before we delete them.

## Setup

- **Harness:** `backend/scripts/e007_tag_ablation.py` (retrieval-only, no model
  calls at run time). Reuses `bench_real.score_ranking` verbatim, so metrics are
  directly comparable to E001/E003/E005.
- **Data (gitignored):** the 37 real-gold answerable tickets
  (`data/eval_real/labels.jsonl` ∩ `sample.jsonl`) + the E003 distilled query
  cache (`distilled_queries.jsonl`). Gold ids all resolve into the live 93-chunk
  corpus (`policy_101`–`193`).
- **Query config:** E005's production parity — distilled queries joined into one
  string, no intent token; fusion = RRF(k=60) over BM25 + dense.
- **Dense side held fixed at production** (E005 leaf-title embed, `"{leaf}
  {content}"`). Tags never enter the dense representation, so the dense ranking is
  **identical** across arms; fusion moves only through its BM25 half.
- **Arms (BM25 document string only):**
  - `tags_on` — BM25 over `"{title} {content} {tags}"` (current production).
  - `tags_off` — BM25 over `"{title} {content}"` (tags stripped; a temp KB JSON
    with `tags: []` seeds a second retriever).
- **Coverage of the change:** all **93/93** chunks carry ≥1 tag, so every chunk's
  BM25 document string differs between arms.
- **Sanity check:** `tags_on | fusion` reproduces E005's `leaf | fusion` row
  **exactly** (hit@1 .703, hit@3 .892, hit@5 .919, recall@3 .640, ndcg@3 .638,
  mrr .800, mean gold rank 2.1) → the harness matches; the deltas are trustworthy.

## Results (n=37)

| arm | backend | hit@1 | hit@3 | hit@5 | recall@3 | recall@5 | ndcg@3 | MRR | mean gold rank |
|---|---|---|---|---|---|---|---|---|---|
| tags_on | bm25 | .405 | .676 | .838 | .523 | .658 | .477 | .589 | 3.1 |
| **tags_off** | bm25 | .405 | **.703** | .838 | .536 | .649 | .488 | .594 | 3.1 |
| tags_on | **fusion** | .703 | .892 | .919 | .640 | .694 | .638 | .800 | 2.1 |
| **tags_off** | **fusion** | **.730** | .892 | .919 | .640 | .712 | .645 | .814 | 2.0 |

**Δ tags_off − tags_on:**
- **bm25:** hit@1 +.000, hit@3 **+.027**, hit@5 +.000, recall@3 +.013, ndcg@3
  +.011, MRR +.005.
- **fusion (production):** hit@1 **+.027**, hit@3 +.000, hit@5 +.000, recall@3
  +.000, recall@5 +.018, ndcg@3 +.007, MRR +.014.

## Findings

1. **Dropping tags does not reduce retrieval on any metric, at either backend.**
   Every delta is **≥ 0** — tags-off is flat or a hair better everywhere. The two
   non-trivial moves (bm25 hit@3 +.027, fusion hit@1 +.027) are each a single
   ticket at n=37 and sit inside E001's ±.05 hit@k noise band, so they are **not**
   evidence that removing tags *improves* retrieval — only that it does not hurt.
2. **Tags carry no measurable BM25 signal.** Consistent with the redundancy
   hypothesis: `heading_tags` repeat words already in the chunk title (title =
   `"{doc} — {heading}"`, which BM25 already indexes), and frontmatter tags
   overlap the content. They add term-frequency weight on already-present terms,
   not new match vocabulary.
3. **The dense/FAISS half is unaffected by construction** — tags were never in the
   embedded string (E005), so this is purely a BM25-corpus question, and BM25 is
   only half of the production fusion signal.

## Decision (adopted) — implemented 2026-07-19

**Dropped tags entirely.** The ablation cleared the bar for removal — retrieval is
flat, so the auto-generated 216-tag long tail was pure carrying cost. Implemented:
- **Column dropped** via Alembic migration `e7a9c1f2b3d4` (down-migration re-adds a
  nullable JSON column; data not restored).
- **Application-side tag path commented out, not deleted** (marker `[tags-dropped
  E007]`), so a future **controlled facet vocabulary** (`CLASSIFICATION_REWORK.md`
  B4b) can restore it by uncommenting + an up-migration: BM25 doc string
  (`retriever.py`), FAISS/Fusion hydration, `chunk_policies.py` generation,
  `policy_repository` importer fields + `create_internal`, the `/policies` API
  request/response, the `PolicyDocument` model column, and the frontend
  (`AddPolicyPanel` input, `PolicyDetailModal` badges, `types/index.ts`).
- `RetrievedChunk.tags` (retrieval DTO) and Fusion's tag-preference merge were left
  intact — they now operate on empty lists (inert), avoiding gratuitous churn.
- **Tests:** suite green (270 passed, 7 skipped — the null-tag-coercion endpoint
  test is skipped, tag assertions in retriever/endpoint/kb-layers/postgres-migration
  tests updated). Frontend `tsc --noEmit` clean.

## Threats to validity

- n=37; single-threshold metrics (hit@3) are ±.05 noise (E001). The verdict rests
  on the *consistency* of the sign (every delta ≥ 0) and the aggregate metrics
  (MRR, mean-rank), not on any single-threshold move.
- Real tickets are AAAI-26-cycle against AAAI-27 policy text (cycle mismatch, as
  in E001/E005) — depresses absolute numbers, not the relative comparison.
- Only the BM25 half is exercised; the dense ranking is identical across arms by
  construction, so this bounds the *retrieval* value of tags only — not any future
  governance/facet use of a curated tag vocabulary.

## Reproduction

```bash
cd backend
export PATH=/u/jpang1/miniconda3/envs/autoexp/bin:$PATH
# Run with the app STOPPED (login-node thread budget). Thread caps:
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
       RAYON_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false HF_HUB_OFFLINE=1
python scripts/e007_tag_ablation.py   # report -> backend/reports/e007_tag_ablation.json
```
