# Retrieval & KB Rework — Ideas / Design Notes

Status: **PROPOSAL (not started)** · Scope: retriever + KB representation + drafter grounding · Branch: none yet

> Condensed from a codebase audit (2026-07-19) of how policy **tags**, the email
> **intent** class, and **chunking** are actually used today. Every claim below
> is traced to `file:line`. Nothing here is built; this is the menu + rationale
> so we can pick targets and turn them into specs/experiments.

---

## 1. The through-line

The system computes and stores three structural signals — the email's **intent**,
and each policy's **category** and **tags** — and then retrieves almost entirely
**blind to all three**. Retrieval is lexical + dense similarity over `title +
content`. Intent is passed to the retriever and ignored; category is embedded by
*nobody*; tags only nudge the BM25 half with auto-generated heading noise.

So the three questions ("do tags matter?", "does intent matter?", "is our
chunking good?") collapse into one design gap: **the KB's structure and the
classifier's output are disconnected from the one place they'd help most —
choosing which chunks the drafter sees.** That gap is the thing worth attacking.

There is a **second, independent problem** (measured in §2b): the dense
representation we embed is barely discriminative — most chunks have a near-twin
in embedding space, so even a well-scoped query struggles to surface the *right*
chunk. Signal is buried. This one needs no taxonomy to fix and is the cheapest win.

---

## 2. Current-state findings (audited)

| Signal | Stored where | Actually load-bearing? | Evidence |
|---|---|---|---|
| **Policy tags** | `policy_documents.tags` (JSON) | **Almost no.** Only BM25 folds them into indexed text; FAISS/fusion never score on them; no drafter/distiller/router reads them. Tags are auto-derived heading words (`use`, `summary`, `statement`), not a curated taxonomy (218 distinct tokens over 93 chunks). | BM25 index text `retriever.py:82`; FAISS embeds title+content only `faiss_retriever.py:100`; drafter grounding block `drafter.py:276-279`; generation `scripts/chunk_policies.py`; display-only `PolicyDetailModal.tsx:157-169` |
| **Email intent** | `emails.classification.intent` (str) | **Yes — but not for retrieval.** Drives chair routing, the lane decision, the drafter prompt, template openings, RL bandit stats, analytics. The retriever **accepts and ignores** it (and in `distill` mode is handed `""` on purpose — E001 found the intent token *hurt* dense retrieval). | Chair routing exact-match `chair_router.py:120-122`; lane `router.py:19-31,103-107`; drafter prompt `drafter.py:286-287`; retriever ignores `faiss_retriever.py:134-176`, BM25 token-append `retriever.py:106`, orchestrator `intent=""` `orchestrator.py:198-206` |
| **Policy category** | `policy_documents.category` | **No.** Embedded/indexed by neither retriever; not given to the drafter. | (absence) `faiss_retriever.py:100`, `retriever.py:82`, `drafter.py:276-279` |
| **Confidence** | `emails.classification.confidence` | **Yes** — FAQ lane gate + RL floor + active-learning near-miss flag. | `router.py:104`, `rl_router.py:147-169`, `active_learning.py:57-72` |

**Chunking (audited).** Reasonable but structure-driven:
- Source = 6 AAAI-27 markdown docs (`data/policy_corpus_real/*.md`) → offline
  heading-based split (`##` section, `###` subsection, preamble→Overview,
  section-intro→intro), then paragraph-packed to ≤220 words for the
  sentence-embedding model's ~256-wordpiece window (`scripts/chunk_policies.py`).
- Result = 93 chunks (`policy_101`–`193`, `data/knowledge_base/policies.json`),
  **one chunk = one DB row**, no runtime chunking.
- Sizes: median 99 words / 648 chars; max 216 words / 1449 chars; **min 7 words**
  (`policy_168` is a bare URL). 24 of 93 are `(part n/m)` splits; 6 are `(intro)`.
- The drafter receives **raw `content` verbatim** per chunk (`[policy_id] title\n
  content`, `drafter.py:276-279`) — no metadata, no summarization. The "distiller"
  (`distiller.py`) only rewrites the *inbound email* into search queries; it does
  **not** summarize retrieved chunks.

---

## 2b. Empirical check — is the relevance signal buried? (2026-07-19)

Two throwaway scripts (lexical + dense embedding of all 93 chunks; no LLM).
**Answer: yes, the dense representation is barely discriminative**, from two
distinct causes.

**Confusability** — if chunks are near-twins in embedding space, a query can't
tell them apart, so the distinctive rule is effectively buried:

| Metric | `title + content` (current FAISS) | `content` only |
|---|---|---|
| mean pairwise cosine | **0.442** | 0.309 |
| chunks with a near-twin (NN cosine > 0.8) | **47 / 93** | 11 / 93 |
| chunks with NN cosine > 0.7 | **82 / 93** | 41 / 93 |
| median nearest-neighbor cosine | 0.805 | 0.684 |

**82 of 93 chunks have another chunk at >0.7 cosine.** Two causes:

- **Cause 1 — the embedded title path homogenizes siblings (cheapest lever).** The
  contextual title repeats a 7-word doc prefix (`AAAI Code of Professional Ethics
  and Conduct — …`) across every sibling, and FAISS embeds `title content`
  (`faiss_retriever.py:100`). Dropping the title from the *vector* alone cuts
  near-twins from 47→11 (>0.8) and 82→41 (>0.7). The title is only ~12% of the
  tokens but a large share of the homogenization because it is identical across
  siblings. It stays valuable for BM25 matching + citation display — it just hurts
  the dense vector.
- **Cause 2 — genuinely duplicated / boilerplate content.** Most-confusable pairs
  are (a) sibling sections (`policy_141≈142≈143`, all "3. PROFESSIONAL LEADERSHIP"
  parts, cos ~0.91) and (b) the *same policy restated across two source docs*
  (`policy_105 ≈ policy_178` multiple-submissions, cos 0.899; `policy_151 ≈
  policy_163` AI-usage, cos 0.893). Intra-doc mean similarity 0.67 (ethics)–0.73
  (publication ethics). Lexically: a reviewer-confidentiality clause is copy-pasted
  across 3 chunks; 8/93 chunks carry URLs; `policy_168` is an 11-word bare link.

**Follow-up probe (what to embed + is the problem intra- or cross-doc):**

| Embedded text | near-twins >0.8 | >0.7 | mean pairwise |
|---|---|---|---|
| full title + content (current) | 47/93 | 82/93 | 0.442 |
| **leaf title + content** (drop doc-prefix) | **19/93** | 60/93 | 0.315 |
| content only | 11/93 | 41/93 | 0.309 |
| leaf title + IDF-distinctive-half content | 14/93 | 51/93 | 0.282 |

- **Leaf title + content is the sweet spot** for the embed representation: −60% of
  near-twins while keeping the distinctive section name (`Multiple Submissions
  Policy`). Content-only is marginally tighter but drops useful leaf signal.
- **Salient extraction helps but is secondary.** Keeping the IDF-distinctive half
  of the content (a non-LLM proxy for LLM salient-extraction; ~62% of words) tightens
  further (19→14), confirming that stripping shared boilerplate spreads chunks —
  but most of the win is already the leaf-title change.
- **The confusability is intra-doc.** 82% of each chunk's nearest neighbor is in
  the *same* source doc; of 72 twin-pairs (>0.8), **66 are intra-doc, only 6
  cross-doc**. A per-document candidate filter would remove ~8% of the confusability
  and leave 92% — see Idea H.

**Caveat (the E001 lesson):** low confusability is *not* automatically better
retrieval — spreading chunks apart only helps if the *right* chunk stays closest
to real queries. This is strong evidence that "what we embed" is a high-leverage
lever, but which change actually raises hit-rate must be measured against the
judge/gold set, not assumed.

Scripts: `analyze_corpus.py` (lexical), `embed_confusability.py`, `probe3.py`
(dense) — kept in the session scratch dir, not committed; rerunnable if the
corpus changes.

---

## 3. Design principle to hold firm

**Separate the retrieval representation from the grounding representation.**

This is a *policy* system: a hallucinated page limit or a softened deadline in a
*cited* policy is a shipping-blocker. Therefore any LLM-generated artifact
(summaries, questions, context blurbs) goes into the **index** (to get found), and
the drafter continues to ground on **verbatim** content (to stay faithful). Never
let a paraphrase become the text that gets cited.

---

## 4. Ideas (prioritized)

### Idea A — Fix the embedded representation  *(cheapest, best-evidenced — see §2b)*
- **Problem:** most chunks are near-twins in the dense space; the distinctive rule
  is buried under repeated title paths + duplicated boilerplate.
- (i) **[SHIPPED 2026-07-19 — E005]** embed the **leaf title + content** (drop the
  repeated doc-prefix from the *embedded* string only). Validated on the 37 real-gold
  tickets: dense hit@1 .514→.649, MRR .665→.756; production fusion hit@1 .649→.703,
  gold rank 2.3→2.1, no regression. Leaf beat content-only, so the leaf is kept.
  `faiss_retriever.py` `_embed_text`/`_leaf_title`; stored `title` unchanged (BM25 +
  citation display keep the full path).
- (ii) **[TODO]** de-duplicate near-identical cross-doc chunks (e.g. `policy_105`/`178`)
  so a top-k slot isn't spent twice on the same rule; (iii) **[TODO]** drop/annex
  URL-only chunks.
- **Fidelity:** index-only; the drafter still grounds on verbatim content.

### Idea B — LLM-augmented multi-vector index  *(biggest retrieval-quality upside)*
- **Problem:** two different gaps — (a) *recall*: policies are formal/third-person,
  emails ask informal/first-person (vocabulary gap); (b) *precision*: chunks are
  near-twins (§2b).
- **Proposal:** offline, generate per chunk **both** (i) 3–5 likely conferee
  questions and (ii) a one-line *discriminative* claim; store each as its own index
  vector pointing back at the **same verbatim chunk** (multi-vector). Questions
  attack recall; the salient claim attacks separability.
- **Two axes, don't conflate them:** generated questions ≠ summaries. Summaries help
  *separability*; questions help *matching*. The §2b proxy shows salient extraction
  does reduce confusability (19→14) but the leaf-title change already got most of it.
- **Backfire risk:** a *brevity*-optimized summary can collapse two distinct sections
  into the same generic statement (↑ confusability) or drop recall-critical common
  words. Objective is "extract the **discriminative core**," never "make it short."
- **Fidelity:** all generated text is index-only; drafter still sees verbatim content.
- **Validate:** E006 vs. baseline on hit@k *and* judge (not confusability alone).
  Regenerate once, offline, versioned; refresh when the KB changes.

### Idea C — Contextual chunk augmentation
- **Problem:** `(part n/m)` and subsection chunks lose their parent context.
- **Proposal:** prepend a one-sentence LLM-generated situating blurb ("This is from
  the AAAI-27 CFP; it states the multiple-submission limit…") to each chunk
  **before embedding**. Finishes what the contextual titles already gesture at.
- **Fidelity:** blurb is index-only.

### Idea D — Structured fact extraction
- **Problem:** emails ask for exact values (deadlines, page limits, "at most 10
  submissions", fees, URLs) — exactly where an LLM drafter hallucinates.
- **Proposal:** extract a small structured facts layer per policy; use it to
  **ground or verify** exact values rather than hoping prose survived.
- **Fidelity:** structured facts are authoritative, extracted verbatim, human-checkable.

### Idea E — Small-to-big retrieval
- **Problem:** retrieval can surface `part 2/3` without the sibling context it depends on.
- **Proposal:** match on small chunks, but feed the drafter the **full parent section**.

### Idea F — Cross-encoder re-ranker
- **Problem:** with top-k now 5 (`MAX_RETRIEVED_CHUNKS`), the precision of those 5
  is what the drafter lives or dies on; RRF fusion alone is coarse.
- **Proposal:** re-rank the fused top-N with a cross-encoder before drafting.

### Idea G — Answerability floor
- **Problem:** weak/irrelevant retrieval still produces a confident draft (ties to
  the known **FAQ over-answering** finding).
- **Proposal:** if the best retrieval score is below a floor, don't draft a
  confident FAQ answer — defer to human review / a "not covered" path.

### Idea H — Document/category retrieval scoping  *(deprioritized — data says small upside)*
- **Idea:** classify the email into one of the **6 source-doc categories** (a more
  honest, data-derived axis than the 11 hand-picked `VALID_INTENTS`) and restrict /
  boost retrieval to that document's chunks.
- **Why the upside is small (§2b measured):** the confusability is **intra-doc** —
  82% of near-neighbors and 66/72 of the >0.8 twin-pairs are *within* the same
  document. A doc-filter removes only ~8% of the confusability (the 6 cross-doc
  duplicates) and leaves the 32-way ethics tangle untouched. Candidate count drops
  93→~22, but dense retrieval already ranks off-topic docs low.
- **Why a hard filter is risky:** a wrong guess makes the correct policy
  *unreachable*. Coarse 6-way classification beats 57.8% (the 11-intent number) but
  even at ~85% you starve ~1 in 7 emails. **If used at all, soft-boost — never a hard
  filter.** Many emails also span docs (there is a whole cross-reference doc).
- **Verdict:** the intra-doc confusability that Ideas A + B attack is the real
  problem; document scoping is at best a small precision add-on afterward.

---

## 5. Cleanup (do regardless of direction)

- **Dead intent enum.** `EmailIntent` (`enums.py:11-21`) uses a *different*
  vocabulary than the live `VALID_INTENTS` and is imported only by `schemas.py`,
  which is imported nowhere in the pipeline. Delete or reconcile — a mismatched
  dead enum is a latent bug.
- **Dead config.** `CONFIDENCE_THRESHOLD = 0.75` (`config.py:38`) is documented as
  the FAQ gate but read by nothing; the real gate is `FAQ_CONFIDENCE_THRESHOLD =
  0.65` (`config.py:93`). Remove or wire up.
- **Tags decision.** Either curate a small closed taxonomy and make it load-bearing
  (Idea H, and only with a data-derived taxonomy), or drop the heading-noise tags so
  nothing false-signals a "taxonomy."
- **Degenerate chunks.** A handful of tiny chunks (e.g. `policy_168`, a 7-word bare
  URL) can win a retrieval slot and give the drafter nothing — fold into their parent
  or attach as metadata.

---

## 6. Suggested sequencing & validation

Nothing here ships on vibes — E001 is the cautionary tale (the "obvious"
intent-in-query idea measurably *hurt*). Each idea is a numbered experiment against
the 20-email set with the existing LLM-judge harness (`backend/scripts/judge_testset.py`),
logged in `docs/exp_tracking/` (next id **E005**), baseline vs. change, same judge.

1. **A** (fix embedded representation) — cheapest, directly evidenced (§2b), no taxonomy dependency → **E005**
2. **B** (question-generation index) — biggest upside, the richer "change what's embedded" move → **E006**
3. **G** + drafter metadata (source URL) — attacks over-answering, lets replies cite the real page
4. Then **C / E / F** as retrieval-precision work, once A/B move the metric
5. **H** (intent/category scoping) — only after a *data-derived* taxonomy exists; low priority

---

## 7. Open questions

- Filter vs. boost for Idea H — a hard category filter risks starving a
  mis-classified email; a boost is safer but weaker. Measure both.
- Idea B/C/D regeneration cost & cadence: these are offline preprocessing passes —
  when do they re-run (every KB edit? nightly? on demand)? The re-eval sweep
  already rebuilds the index on edits; augmentation would need a companion refresh.
- Do internal (chair-authored) policies get the same augmentation as public ones?
