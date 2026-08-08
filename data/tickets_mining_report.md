# Historical Ticket Mining — Marc Workflow Analysis

## Purpose
Mine historical AAAI-27 tickets to learn how questions were actually resolved,
grouped by intent, to make the workflow-suggestion feature data-driven (currently
hand-authored) and to explore a future retrieval step in the pipeline that
surfaces a matched historical workflow for new tickets — improving both workflow
suggestions and auto-draft quality.

## Status
- Step 1 — Locate & define corpus: **COMPLETE**
- Step 2 — Stage 1 extraction (small test batch): **COMPLETE** (25 threads)
- Step 3 — Stage 1 extraction (full corpus): **COMPLETE** (4,094 processed = 3,796 extracted + 298 merge-closures skipped; 0 errors)
- Step 4 — Stage 2 intent tagging: **COMPLETE** (3,796 tagged, 0 errors) — see **Stage 2 — Intent Tagging**
- Step 5 — Stage 3 workflow clustering within each intent: **COMPLETE** (3,655 clustered into 111 clusters, 81% coverage) — see **Stage 3 — Workflow Clustering**

## Data Source
- Zendesk export via OAuth client `confmail` (aaai.zendesk.com), pulled 2026-07-16 UTC
- 21,219 total tickets, 15,878 users, 45,659 comment events
- Location: `data/tickets/` — **gitignored, contains real user PII, never committed or shared as raw files**

## Marc's Corpus Definition
- File: `marc_threads.jsonl`
- Selection rule (per export manifest): tickets where Marc (user `39818737387419`, `pujol@aaai.org`) authored **≥1 public reply**
- 4,094 threads — 19.3% of the 21,219 total tickets
- **Decision:** use all 4,094 threads as-is. No filtering by ticket ownership (`assignee_id`) or by whether other agents also replied. "Marc replied" is the criterion that matters for this analysis — not "Marc owned the ticket."

## Verified Data Quality
- Threads are **full, complete conversations** — cross-checked comment-by-comment against `comment_events.jsonl`: 0 truncated, 0 extra, across all 4,094 threads
- 10,577 total comments, average 2.58 per thread, range 1–10
- Both sides of the conversation present: 5,180 requester comments, 5,161 Marc comments, 236 from other agents
- Internal (non-public) notes included: 394 of 10,577 comments, of which 334 are Marc's
- For context only (not used to filter): Marc is the assigned owner on 4,024/4,094 threads; 136 threads (3.3%) also have a public reply from another agent
- Zendesk's built-in demo ticket (id `1`, 2021-07-13, "Sample ticket: Meet the ticket") **confirmed absent** from `marc_threads.jsonl` — verified by subject/tag search across all 21,219 tickets, not just an ID lookup. Marc's corpus has a hard floor at ticket `12449` / 2024-08-09, spanning **2024-08-09 to 2026-07-16 (~2 years)**, not the export's full 5-year range

## Cost & Credential Isolation
- Stage 1 extraction is billed to a **dedicated AAAI OpenAI API key** held in `backend/.env.mining` (gitignored), read **only** by `backend/scripts/data_mining/stage1_extract.py`. It is deliberately separate from the key in `backend/.env` (`LOCAL_MODEL_API_KEY`) that the live ConfMail pipeline uses, so mining spend for the 4,094-thread run does not bill to the same credential as production traffic — and an offline batch cannot exhaust a rate limit the live app depends on. The script has no fallback: if the mining key is missing or unfilled it exits immediately rather than quietly using the app's key.
- Only the *credential* is isolated. The endpoint and model (`LOCAL_MODEL_BASE_URL`, `LOCAL_MODEL_NAME`) still come from `backend/.env`.

## Stage 1 — Extraction

### Method
- **One LLM call per ticket**, given the full thread in chronological order with each message labelled by author, whether that author is the chair, and whether it was public or an internal note (so internal reasoning can inform the extraction without ever being described as something the requester was told).
- Extracts **5 fields**: `what_was_asked`, `steps_taken` (ordered), `resolution`, `policy_or_reference_used` (nullable), and `outcome_type` (one of `resolved_directly` / `needed_follow_up` / `escalated` / `no_clear_resolution`). The goal is HOW the ticket was worked — the working process — not the reply text.
- **Merge-closure threads skip the LLM entirely.** A thread whose every chair comment is an *outbound* merge notice ("This request was closed and merged into request #N") was absorbed into another ticket and carries no workflow content; it is detected by pattern match and written as a tagged record (`category: "merge_closure"` + `merge_target_id`) with no extracted fields.
- Uses the isolated mining credential — see **Cost & Credential Isolation**.
- Script: `backend/scripts/data_mining/stage1_extract.py`. Output: `data/mining/stage1_full/results.json` (gitignored — PII-derived).

### Full-run results
**4,094 threads processed · 298 merge-closures skipped (7.3%) · 3,796 LLM calls · 0 errors · 19.7 min wall clock.**

| `outcome_type` | Count | % of extracted |
|---|---:|---:|
| resolved_directly | 2,736 | 72.1% |
| needed_follow_up | 520 | 13.7% |
| escalated | 384 | 10.1% |
| no_clear_resolution | 156 | 4.1% |

All 3,796 extractions carry all 5 fields; no empty `what_was_asked`, `resolution`, or `steps_taken`. `steps_taken` averages 2.91 entries (range 1–9); `policy_or_reference_used` is null on 623 (16.4%).

### Fixes applied during testing
- **Merge-closure detector corrected to outbound-only.** Zendesk emits two merge notices sharing the substring "closed and merged into" but meaning *opposite* things. The naive match would also have caught *inbound* notices ("Request #N … was closed and merged into **this** request"), which mark the **survivor** thread that absorbed another ticket's work — wrongly discarding **29 threads**, including the merge target of a ticket the detector correctly skipped. Only threads whose every chair comment is an outbound notice are skipped.
- **`policy_or_reference_used` tightened** to exclude internal support-desk cross-references (Zendesk request/ticket numbers) while explicitly **retaining paper/submission numbers**, which are legitimate references and were being dropped by an over-general first attempt.

### Reproducibility caveat
- **Extraction is not fully reproducible, even at `temperature=0` with a fixed seed** — the hosted model does not honor determinism settings. Verified directly: the same ticket sent the same prompt back-to-back returned different JSON on **3 of 3** attempts, including a change in the number of `steps_taken`.
- Practical consequence: aggregate Stage 2/3 pattern counts should be stable, but **no single Stage 1 record should be treated as ground truth**, and re-running the corpus will not reproduce identical output. Occasional `outcome_type` flips between adjacent labels (e.g. `resolved_directly` ↔ `escalated`) sit inside the model's normal variance band and are not evidence of a code change. The same applies to Stage 2 labels.

## Stage 2 — Intent Tagging

### Method
- Each Stage 1 extraction is tagged with **exactly one** intent from the live **14-intent taxonomy**, imported directly from `backend/app/pipeline/taxonomy.py` (`VALID_INTENTS` / `INTENT_DEFS` / `INTENT_FAMILIES` / `FALLBACK_INTENT`). **No duplicate intent list exists in the mining code** — if the taxonomy changes, the tagger follows automatically.
- Input to the tagging call is deliberately narrow: **`what_was_asked` + `steps_taken` only**. `resolution`, `policy_or_reference_used`, and `outcome_type` are withheld, so the tag reflects what the requester wanted and how it was worked — not how it happened to end.
- Output per ticket: `intent`, `is_fallback`, and a one-sentence `reasoning` that must name why the chosen intent beat the nearest alternative (kept for human sanity-checking).
- **`is_fallback` flag** distinguishes the two very different reasons a ticket lands on `cms_support`: a *genuine* systems/account/access ticket (`false` — that is what the intent is for), versus a *true taxonomy gap* where nothing fit and the fallback was a last resort (`true`). The invariant is enforced in code, not trusted from the model: any intent other than `cms_support` is forced to `is_fallback: false`, and an off-taxonomy answer is coerced to the fallback with the flag set.
- Script: `backend/scripts/data_mining/stage2_tag_intent.py`. Same isolated mining credential and API seam as Stage 1 (see **Cost & Credential Isolation**).

### Full-run results
**3,796 tickets tagged · 0 errors · 0 off-taxonomy coercions · 11.3 min wall clock.** All 14 intents were used — none empty. The 298 `merge_closure` records are excluded (no content to tag), so 3,796 = every real Stage 1 extraction.

| Intent | Count | % | Family |
|---|---:|---:|---|
| review_submission_help | 1,367 | 36.0% | review_workflow |
| reviewer_assignment | 597 | 15.7% | review_workflow |
| review_decision_appeal | 449 | 11.8% | appeals_integrity |
| reviewer_workload_role | 427 | 11.2% | committee |
| cms_support | 232 | 6.1% | systems |
| submission_requirements | 149 | 3.9% | submission_compliance |
| desk_reject_appeal | 146 | 3.8% | appeals_integrity |
| committee_invitation | 145 | 3.8% | committee |
| submission_format_policy | 79 | 2.1% | submission_compliance |
| submission_upload_help | 62 | 1.6% | submission_compliance |
| anonymity_violation | 53 | 1.4% | appeals_integrity |
| author_list_change | 50 | 1.3% | submission_compliance |
| author_profile_compliance | 29 | 0.8% | submission_compliance |
| paper_bidding | 11 | 0.3% | review_workflow |

| Family | Count | % |
|---|---:|---:|
| review_workflow | 1,975 | 52.0% |
| appeals_integrity | 648 | 17.1% |
| committee | 572 | 15.1% |
| submission_compliance | 369 | 9.7% |
| systems | 232 | 6.1% |

Output: `data/mining/stage2_full/intent_tags.json` (gitignored — PII-derived).

### Taxonomy gap finding
- **141 tickets (3.7%) are flagged `is_fallback: true`** — genuinely unclassifiable under the current 14 intents. All 141 sit under `cms_support`, and the invariant held (zero fallback flags leaked onto other intents).
- This splits `cms_support` into **91 genuine systems tickets vs. 141 true gap-tickets**. Without the flag, 61% of that bucket would have been silently miscounted as systems traffic.
- The 141 are **not noise — they cluster into recurring topics** the taxonomy does not cover:
  - **presentation/poster scheduling and session swaps** (the largest cluster)
  - **visa / invitation letters**
  - **registration and copyright-form issues**
  - **dual-submission reports across venues** (e.g. a paper simultaneously under review elsewhere)
- This is concrete, quantified evidence for a future **taxonomy-extension discussion**. **Not acted on now** — the 14-intent taxonomy is unchanged, and nothing in the live pipeline was modified.

### Limitation — 141 is a FLOOR, not a precise count
`is_fallback` can only catch a gap-ticket that actually **lands in `cms_support`**. The same kind of gap can instead be **silently absorbed into a different existing intent**, where nothing flags it. Confirmed case: ticket **20858**, a request to *withdraw* an accepted paper — withdrawal has no taxonomy home, but rather than falling back it was tagged `submission_upload_help` with `is_fallback: false` (stable across 3 resamples). So the true volume of taxonomy-gap tickets is **≥ 141**, and the real figure cannot be measured with this flag alone.

### Distribution skew — a Stage 3 consideration
- The distribution is **heavily concentrated**: `review_submission_help` alone is **36%**, and the `review_workflow` family is **52%** of Marc's tickets.
- The bottom four intents — `submission_upload_help` (62), `author_list_change` (50), `author_profile_compliance` (29), `paper_bidding` (11) — total **~150 tickets combined**. That is unlikely to support **stable statistical workflow-pattern counts**; these are probably better handled qualitatively than statistically in Stage 3.

### Scope caveat — these are MARC's proportions, not the taxonomy's
These percentages describe **Marc's 3,796-ticket workload specifically**, not the full ~18.5k inbound corpus the taxonomy was originally mined from. They must **not** be read as taxonomy-level or inbox-level intent frequencies.

## Stage 3 — Workflow Clustering

### Method
- **Input**: each ticket's `steps_taken` joined into one string, clustered **within a single intent** (never across intents). `is_fallback` tickets are excluded — they are taxonomy gaps parked on an intent, not workflow instances of it.
- **Embeddings**: the existing local CPU SentenceTransformer seam the FAISS retriever uses (`settings.FAISS_MODEL_NAME`, L2-normalized, mirroring `faiss_retriever._encode`). Runs entirely on this machine — **no external call, no credential, no mining spend for embeddings**. Only the short cluster-*labelling* calls use the isolated mining key.
- **Size-tiered clustering** — one global setting does not fit intents spanning 11 to 1,367 tickets:
  - `n >= 200` → HDBSCAN, `min_cluster_size=15`
  - `30 <= n < 200` → HDBSCAN with `mcs` swept proportional to `n`, best candidate selected
  - `n < 30` → average-linkage agglomerative with a cosine distance threshold. HDBSCAN cannot estimate density at small `n` and returns **100% noise even when the tickets are plainly similar** (measured on `paper_bidding`, n=11: mean pairwise cosine 0.60, 42% of pairs above 0.70, yet 0 clusters at every tested parameter). Trade-off: agglomerative assigns every point, so singletons appear as 1-member clusters and should be read as unclustered one-offs (13 corpus-wide).
- **Noise-recovery pass**: HDBSCAN's noise bucket is not all one-offs. Leftover points are re-clustered at half the primary `mcs` (floor 5); anything found is kept and marked `recovered_from_noise`. This is not a marginal cleanup — it surfaced a **91-ticket** procedure in `reviewer_workload_role` and cut `review_submission_help` noise from 403 to 260.
- **Candidate-merge flagging**: cluster centroids above 0.85 cosine are flagged for human review. **Advisory only — never auto-merged** (see the lesson on misleading similarity below).
- Script: `backend/scripts/data_mining/stage3_cluster.py` (one script for both a single-intent test and the full run, via `--intent`). Output: `data/mining/stage3_full/clusters.json` (gitignored — PII-derived).

### Corpus-wide result
**3,655 tickets clustered** (3,796 tagged minus 141 `is_fallback`) into **111 clusters** — 76 primary + 35 recovered — with **709 noise, 81% coverage** and 7 outstanding advisory merge flags.

| Intent | n | Method | Primary | Recovered | Clusters | Noise | Coverage |
|---|---:|---|---:|---:|---:|---:|---:|
| review_submission_help | 1,367 | hdbscan | 5 | 9 | 14 | 260 | 81% |
| reviewer_assignment | 597 | hdbscan | 6 | 7 | 13 | 160 | 73% |
| review_decision_appeal | 449 | hdbscan | 5 | 4 | 9 | 122 | 73% |
| reviewer_workload_role | 427 | hdbscan | 4 | 2 | 6 | 14 | 97% |
| submission_requirements | 149 | hdbscan_swept | 9 | 2 | 11 | 28 | 81% |
| desk_reject_appeal | 146 | hdbscan_swept | 7 | 4 | 11 | 28 | 81% |
| committee_invitation | 145 | hdbscan_swept | 6 | 3 | 9 | 33 | 77% |
| cms_support | 91 | hdbscan_swept | 5 | 2 | 7 | 7 | 92% |
| submission_format_policy | 79 | hdbscan_swept | 5 | 0 | 5 | 30 | 62% |
| submission_upload_help | 62 | hdbscan_swept | 3 | 2 | 5 | 1 | 98% |
| anonymity_violation | 53 | hdbscan_swept | 2 | 0 | 2 | 10 | 81% |
| author_list_change | 50 | hdbscan_swept | 2 | 0 | 2 | 16 | 68% |
| author_profile_compliance | 29 | agglomerative | 13 | 0 | 13 | 0 | 100% |
| paper_bidding | 11 | agglomerative | 4 | 0 | 4 | 0 | 100% |
| **TOTAL** | **3,655** | | **76** | **35** | **111** | **709** | **81%** |

The two 100% figures are an artefact of the agglomerative fallback assigning every point, not evidence those intents clustered better.

### Lesson — a "stability" criterion that ignores coverage picks worse results
The mid-tier sweep originally chose the `mcs` whose **cluster count survived the longest run of swept values** ("longest stable plateau"), on the reasoning that a count appearing at one setting is an artefact while one holding across a plateau is structure. That reasoning is sound but incomplete: **it ignores how much of the intent is left unexplained.**
Measured on `reviewer_assignment` (n=597), it chose **mcs=48 (2 clusters, 369 noise / 62%)** over **mcs=18 (4 clusters, 321 noise / 54%)** purely because `{30,48}` agreed on "2 clusters". The chosen result explained *less* of the intent with *fewer* patterns.
**Corrected rule** (`stage3_cluster.select_trial`): lowest noise wins; anything within 5% of the best noise counts as tied on coverage, and plateau length only breaks ties among those. Re-checking the 8 swept intents under the corrected rule, 4 would pick a different `mcs` — but all four trade granularity for coverage, and their sweep figures are *pre*-recovery, so the apparent gains largely close once the recovery pass runs. They were left unchanged deliberately.

### Lesson — high centroid similarity can be driven by a subset, not the whole cluster
Centroid cosine measures the *average* of a cluster, so a cluster that is partly-overlapping and partly-unrelated reports the same high similarity as one that genuinely matches. This is why merge flags stay advisory. Demonstrated in both directions:
- **`review_submission_help` c0 (440) ↔ c1 (366), cos 0.851** — inspection confirmed one procedure (the September OpenReview outage: confirm outage → refuse email submission → extend deadline without penalty). **Correctly merged** into n=806.
- **`reviewer_assignment` c0 (301) ↔ c1 (73), cos 0.889** — inspection showed c0 was a recovery-pass residual containing *several* procedures, only ~36% of which matched c1's emergency-reviewer invite. The similarity was that fraction pulling the centroid. **Correctly NOT merged** — folding a clean 73-ticket procedure into a 374-ticket grab-bag would have destroyed the one crisp pattern in the intent.
A third case sharpened the rule: `review_submission_help` c7 (n=30) flagged at 0.888/0.851 against both outage clusters, but is individual access troubleshooting with **no deadline extension** — shared vocabulary, different procedure. Kept separate. Note that after merging c0+c1 the flag against c7 *rose* to 0.916 through centroid drift, without any new evidence.

### Known limitation — `steps_taken` embeddings do not separate action type
Procedures that share vocabulary but differ in the **action taken** are not linearly separable in this signal. Concretely: *"invite an emergency reviewer"* and *"reassign / reduce a reviewer's workload"* both read as reviewer + assignment + paper + chairs. Re-clustering `reviewer_assignment`'s 301-ticket residual in isolation could not decompose it — the core stayed a ~46% emergency-reviewer mixture at every reasonable setting, and only extreme fragmentation (`mcs=3` → 14 clusters at 41% noise) began isolating emergency-heavy pockets.
Related: centroid similarity is also blind to **outcome polarity** — after splitting `desk_reject_appeal` c8, its "uphold the rejection" and "reverse the rejection" pieces flagged against each other at 0.870 despite being opposite outcomes.
**Relevance to Phase B**: if a future retrieval step must distinguish *which action a chair took* rather than *what the ticket was about*, embedding similarity over `steps_taken` alone will not provide it. That needs a different signal — an explicit action/verb field, or an LLM pass — and should be designed in rather than assumed.

### Manual decisions applied
Reviewed by hand and applied on top of the automated output; every change records its provenance (`merged_from`, `split_from`, `split_group`, `assigned_by_inspection`) so it is auditable:
- **6 cluster merges** across `review_submission_help` (×2), `review_decision_appeal`, `desk_reject_appeal`, `submission_requirements`, and `committee_invitation` (a three-way).
- **1 further merge**: `review_submission_help` c0+c1 → n=806 (the outage procedure).
- **1 three-way split**: `desk_reject_appeal` c8 (n=30) → **upheld (15) / triage (11) / reversed (4)** — it held opposite outcomes plus a different requester type (SPC/reviewer asking "should this be desk-rejected?"). 12 of the 30 were assigned by reading their steps rather than from the reported example lists; those are marked `assigned_by_inspection`.
- **1 residual re-clustered in isolation**: `reviewer_assignment` c0 (n=301) → 6 sub-clusters, surfacing **5 clean procedures** (conflict-of-interest forwarding, reciprocal-review transfer, extra-reviewer verification, platform-limitation retry, deadline extension) and returning 81 points to noise. Coverage for that intent fell 87% → 73%, which is the honest figure: the prior 87% counted a 301-ticket grab-bag as "explained."
- **1 revert**: `reviewer_assignment`'s re-swept result was discarded in favour of the original `mcs=15` + recovery, after the resweep proved to explain 56% of the intent versus 87%.

### Scope caveat — Marc's workload, not the inbox
As with Stage 2, these are workflow patterns within **Marc's 3,796-ticket workload**, not the full ~18.5k inbound corpus. Cluster sizes and per-intent coverage describe how *he* handled tickets and must not be read as conference-wide or taxonomy-level frequencies.

## Open Items / Caveats
- `state.json` shows 46,075 vs. the manifest's 45,659 comments — explained as pre-dedup counter vs. final deduped file; the file itself is internally consistent, not a data-quality issue
- All raw ticket data contains real user PII — must stay gitignored, never committed, never shared outside this analysis

## Next Step
Phase B: use the mined workflows as a retrieval source — given a new ticket,
surface the matched historical procedure to inform the chair-workflow suggestion
and the auto-draft. Before building it, note the **known limitation** recorded
under Stage 3: `steps_taken` embedding similarity distinguishes *what a ticket is
about* but not *which action the chair took*, so a retrieval step needing that
distinction requires an additional signal.
