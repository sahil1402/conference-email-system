# E009 — Hand-audit of the 37-gold retrieval eval set

**Date:** 2026-07-19 · **Trigger:** the retrieval line (E001/E003/E005/E007/E008) all
rests on the same 37 gold labels; before building on them further, hand-check them for
mislabeling and validity.

## What the set is
The 37 = tickets in `data/eval_real/labels.jsonl` with `policy_answerable=true` AND a
non-empty `relevant_chunk_ids`, intersected with `sample.jsonl` (the ~202-ticket
labeled sample). Built by `scripts/label_real_tickets.py`: **LLM-assisted, one call
per ticket, anchored on the chair's (Marc's) actual reply** (the reply defines the
information need). `relevant_chunk_ids` were chosen from BM25∪dense top-10 candidates
or the full title catalog; each label carries a `rationale`.

## Audit method
Hand-checked all 37: for each, compared the (PII-scrubbed) question ↔ the label's
`rationale` ↔ the gold chunk titles + content. Deep-verified the cases that looked
suspicious by reading the full chunk text.

## Findings
- **Label quality is high — no clear mislabels.** ~32/37 are crisp, well-matched
  multi-gold labels (e.g. the appendix/desk-reject cluster tid 15765/15796/15848/16639,
  the ethics cluster 16339/16523/18387/19173/19781, authorship 20573/20710/20615,
  reviewer-LLM 16565/19458). Anchoring on the real reply is a sound method.
- **A misleading-title case that turned out CORRECT:** tid 16701 (reviewer withdrawal)
  and 17871 (late review) are grounded on `policy_170`, titled *"Author Registration"* —
  which *buries* the clause *"all authors of AAAI-27 submissions will be expected to
  join the conference's reviewer pool unless prevented by extenuating circumstances."*
  It is one of only two chunks stating that obligation, so the label is right; the
  labeler found a clause the title hides. (Initially flagged as a mislabel, then
  verified correct.)
- **~5 generous `policy_answerable=true` calls** (tid 21005 slot-swap, 16163 status
  inquiry, 16720 review-extension, 19173/19493 operational functions): the gold chunk
  is the correct/closest policy section, but the chair's real answer also used
  operational info NOT in the KB. Not label errors — the E006 coverage gap surfacing as
  *partial* answerability.
- **Intent field is noisy (~10/37 off)** — appeals/status tagged `review_assignment`,
  etc. This is the old 11-way taxonomy mismatch (see CLASSIFICATION_REWORK.md), NOT the
  retrieval gold, and does not affect E005/E007/E008.

## Validity limits (the real caveats — not mislabeling)
1. **Selection bias (biggest).** `label_real_tickets.py` deliberately over-sampled
   author-facing intents because "the corpus skews ~68% reviewer-ops where policy
   coverage is low." So the 37 are the **policy-answerable author-submission slice** —
   exactly where the KB is strong. **Retrieval scores (hit@3 .892) are optimistic and
   do NOT represent the reviewing-ops majority of the real inbox**, which has little KB
   to retrieve against. Same submission-scoped-KB-vs-reviewing-ops-inbox gap the
   taxonomy mining found.
2. **Cycle mismatch.** Questions are AAAI-25/26; gold chunks are AAAI-27. The *section*
   is right, the *values* (dates, page limits) differ — fine for retrieval, a caveat for
   answer-correctness.
3. **n=37, single LLM labeler, no second annotator.** The audit verified no *false
   positives* among labeled chunks; it did **not** exhaustively check *false negatives*
   (relevant chunks that should have been labeled but weren't) — the one gap a manual
   pass can't fully close.

## Implications
- **E005/E007/E008 conclusions stand** — the labels are trustworthy, so
  MiniLM + distilled + fusion + leaf-title is genuinely the best retrieval config *on
  this set*.
- But the set measures the friendly slice; **real-inbox retrieval is overstated**, and
  reviewing-ops retrieval is untested (and, given ~0 KB coverage, likely far worse).
- The fix is not relabeling — it is **expanding the eval to reviewing-ops questions once
  the KB (or the intent→coverage map from Task 2b) can support them.**
