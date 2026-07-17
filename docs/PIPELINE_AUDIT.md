# Pipeline Audit — Current Implementation vs. the v1 Target Flow

**Date:** 2026-07-16 · **Updated:** 2026-07-17 (post Phase 7C — style guide + real-policy chunking done; agreed eval plan added) · **Scope:** backend + frontend at Phase 7A (146/146 tests) · **Companion:** `docs/ZENDESK_API.md`

## The v1 target flow

> Automatically fetch tickets from Zendesk → retrieve relevant policy chunk sections for the email → prompt the external drafting provider with (1) the raw inquiry email, (2) the retrieved policy chunks, (3) a style/instruction guide → generate the draft → hold for human review (no auto-send; every send is human-gated) → reviewer approves-and-sends or modifies-and-sends → record the human's action and the edited email for future development.

## Scorecard

| # | v1 step | Status | Summary |
|---|---------|--------|---------|
| 1 | Auto-fetch tickets from Zendesk | ❌ Missing | Only manual `POST /api/v1/ingest`. Read-scope OAuth + batch pull script exist as groundwork, but there is no poller service and no ticket identity on the `Email` model (no dedup key). |
| 2 | Retrieve relevant policy chunks | 🟡 Partial *(chunks ready)* | Retrieval machinery done and evaluated (BM25 / dense / fusion, swappable, top-k). **Done 7C:** real AAAI-27 corpus chunked → `policies_aaai27.json`, 93 chunks. Remaining: seed into `policy_documents` (FAISS reads the DB), real-ticket ground truth, re-bench (see "Agreed evaluation plan"). |
| 3 | Prompt = raw email + chunks + style guide | 🟡 Partial *(guide ready)* | Prompt already carries the raw email + all retrieved chunks (+ classification & lane). **Done 7C:** style guide distilled from 702 scrubbed real-reply pairs (`data/style_guide/style_guide_v1.md`) + curated **v2** (~460 words; no-action-claims rule; refusals conditional on retrieved context). Remaining: `STYLE_GUIDE_PATH` injection seam, structured `reply_text`/`notes_for_chair` output, v1-vs-v2-vs-none A/B. |
| 4 | Draft via the external provider | 🟡 Partial | A provider-compatible chat-completions HTTP path exists (`_draft_local`) but sends **no Authorization header**, so it cannot reach the hosted keyed API. Needs a small auth-aware provider branch. |
| 5 | Hold for human review; never auto-send | 🟢 Behaviorally true / ⚠️ by accident | Nothing in the codebase sends anything — every email persists as `DRAFT_GENERATED` regardless of lane. But the FAQ lane is *designed* as an auto-reply lane; v1 must make "draft-only" an explicit, flagged policy rather than an accident of the missing send step. |
| 6 | Approve-and-send / modify-and-send | 🟡 Half done | Approve with optional edit (diff preserved) ✅. **Send does not exist** — no Zendesk write-back, no `SENT` status transition, and the OAuth client has only been proven with `read` scope. |
| 7 | Record human actions + edited email | ✅ Done | The strongest area: append-only audit (both full texts on edited approvals), reroute + chair-reassign trails, active-learning flags (near-miss confidence, meaningful edit), RL feedback hooks, per-email pipeline traces. |

---

## Step-by-step detail

### 1. Auto-fetch from Zendesk — missing (biggest build item)

**Have:**
- `POST /api/v1/ingest` runs one `{from, subject, body}` payload through the full pipeline (`backend/app/api/v1/emails.py`).
- Working read-only OAuth (`client_credentials`, 30-min bearer tokens) and a **batch** pull script `backend/scripts/pull_zendesk_tickets.py` — a research tool, not a service.
- `docs/ZENDESK_API.md` documents the recommended polling design (cursor incremental export every 2–5 min).

**Gaps:**
- **No poller service.** Need a scheduled worker: incremental export → new tickets → map to ingest payload → run pipeline. Options: asyncio background task in the app, or a cron-driven script hitting `/ingest`; either must persist its export cursor.
- **No ticket identity → no idempotency.** `Email` has no `external_id` / `zendesk_ticket_id` column (`backend/app/db/models.py`), so a poller cannot dedup re-exported tickets (incremental export re-emits tickets on *any* update) and an approved draft has no address for write-back. **Requires a migration**; this is the prerequisite for everything Zendesk-facing.
- **No thread model.** The pipeline treats an email as one `subject + body`. Zendesk tickets are threads; v1 can ingest the first requester comment only, but the poller must define behavior when a requester replies again on an already-processed ticket (recommend: re-queue for human review, do not re-draft blindly in v1).
- Requester name/email arrive via the ticket's `requester_id` → users side-load; `sender`/`sender_name` fields already fit.

### 2. Policy retrieval — machinery done, corpus wrong

**Have:**
- Three swappable backends behind `RETRIEVAL_BACKEND` (`bm25` default / `faiss` / `fusion`), one `retrieve(query, intent, top_k)` contract, `MAX_RETRIEVED_CHUNKS=3`.
- Measured on the current KB: dense retrieval R@3 = 0.982 vs BM25 0.875 (Phase 5A); fusion doesn't beat dense alone (5C).
- Retrieval query = first 300 chars of body + intent token (`orchestrator.py`).

**Done (Phase 7C, 2026-07-17):** `backend/scripts/chunk_policies.py` chunks the six real AAAI-27 markdown docs → `data/knowledge_base/policies_aaai27.json`, **93 chunks** (median 99 words, max 216, none over the ~220-word dense-embed truncation bound). Rules: `##` primary cut, `###` subsection split (each ethics principle is its own chunk), substantive intros → "(intro)" chunks, preambles → "— Overview" chunks, oversize leaves paragraph-packed into "(part n/m)". Titles are contextual paths ("Doc — Section — Sub") because both retrievers match on title (BM25 indexes title+content+tags; FAISS embeds "title content"). Ids `policy_101+` keep the drafter's citation regex matching, clear of the toy KB's 001–045. Toy KB untouched for regression.

**Remaining gaps:**
- Seed the 93 chunks into `policy_documents` (BM25 reads the JSON; **FAISS reads the DB**), then re-benchmark — all existing retrieval numbers are still from the toy corpus.
- Ground truth: build from **real tickets**, not the synthetic set (see "Agreed evaluation plan" below); labels must be multi-gold (same policy appears in multiple docs, e.g. page limits in CFP + cross-reference).

**Agreed evaluation plan (2026-07-17)** — one labeled real-ticket sample feeding two evals:
1. Sample ~200 answered threads from `data/tickets/marc_threads.jsonl`, stratified by intent, over-sampling author-facing intents (the corpus skews ≈68% reviewer-ops where policy coverage is low).
2. LLM-assisted labeling anchored on the chair's **actual reply** (the reply defines the information need): per ticket, label (a) policy-answerable yes/no — this yields the **coverage** number that bounds the FAQ lane, (b) relevant chunk id(s) (multi-gold) from the union of both backends' top-10 + a title scan, (c) an **intent label** (free in the same call; later used to evaluate/retrain the classifier honestly). Human spot-check ~20.
3. Retrieval ablation on the answerable subset: backends bm25/faiss/fusion × query variants (body only / body + keyword-classifier intent / subject + body). Decides the backend, whether the classifier's intent token helps retrieval, and whether the keyword→candidate-pool pipeline idea (deployment TODO 4) is worth building. The keyword classifier participates as an **ablation subject and production-default config, not a trusted component**.
4. End-to-end draft eval on the same tickets (no guide / guide v1 / guide v2) judged against the chair's real replies — retrieval labels give failure attribution (drafting problem vs retrieval problem). Requires the step-4 auth fix first.
The synthetic 67-email set is retired to a CI smoke test; its labels point at old chunk ids and its policy-derived vocabulary inflates retrieval numbers.

### 3. Prompt assembly — two of three inputs present

**Have:** `_build_user_prompt` (`backend/app/pipeline/drafter.py`) already includes (1) the raw email (sender/subject/body) and (2) every retrieved chunk with id + title + content, plus classification and lane. Grounding rules live in the hardcoded `_SYSTEM_PROMPT`.

**Done (Phase 7C, 2026-07-17):** the guide exists, in two versions under `data/style_guide/` (provenance + change log in `manifest.json`):
- **v1** — distilled via `backend/scripts/distill_style_guide.py` (map-reduce over 702 PII-scrubbed question→reply pairs: 3,790 first public chair replies, merges excluded, near-dup templates collapsed to frequency-weighted representatives, 15 month-stratified batches). ~1,100 words.
- **v2** — manual curation after review (~460 words: 8 behavioral rules, 3 hard constraints). Key corrections over v1: **never claim an action was taken** (the drafter cannot act — v1's "we have updated the assignment" examples invited fabrication); refusals made **conditional on retrieved context** (outcome from retrieval, phrasing from the guide); distillation-artifact rules dropped; no hard word cap — concise/to-the-point, length is the model's call (user direction).
- General (non-per-intent) guide confirmed as the v1 product decision; the corpus evidence supports it (voice uniform across topics: 94.6% "Dear", 91.8% "AAAI Team", 85.9% "Best regards").

**Done (Phase 7D/7E, 2026-07-17):**
- `STYLE_GUIDE_PATH` implemented (config + system-prompt injection, warn-once on unreadable path); **guide v2 adopted** after the blinded A/B (see RESULTS §4).
- **Structured drafter output implemented** (Phase 7E): the model emits `=== REPLY === / === CITATIONS === / === NOTES FOR CHAIR ===`; `DraftResponse` gains `notes_for_chair`, and `draft_text` is deterministically sanitized — scaffold headers ("Draft reply:") stripped and **every internal `policy_NNN` id removed from requester-facing text** (ids are internal indexing; provenance lives only in `citations`, now parsed from the CITATIONS section). Unstructured model output degrades gracefully (whole text = reply, ids recovered then scrubbed). Template drafter's inline `[policy_id]` tags removed likewise. Verified on regenerated real-ticket drafts: clean replies, correct citations.
- Watch item: on the 5-draft regeneration the model chose empty chair notes everywhere — verify notes populate on live traffic (e.g. cycle-mismatch caveats) and strengthen the NOTES instruction if not.

**Remaining gap:** UI surface for `notes_for_chair` in the review queue detail pane (field is persisted in the draft JSON already).

### 4. Drafting via the external provider — protocol ready, auth missing

**Have:** `MODEL_PROVIDER` seam with 4 working branches (cloud-SDK, self-hosted HTTP, template, fallback). The self-hosted branch (`_draft_local`) POSTs `{model, messages, max_tokens}` to `{base}/chat/completions` — exactly the protocol of the chosen external provider — and degrades to a fallback draft on any error.

**Gaps:**
- `_draft_local` sends **no `Authorization: Bearer` header**, so it cannot authenticate to the hosted API. Fix per `docs/DRAFTER_ADAPTER_SPEC.md`: either extend the HTTP branch with an optional key setting, or add a 5th provider branch (config Literal + dispatch + `.env.example` + health-check). Small, contract-preserving change.
- Key handling: currently sitting in `docs/secrets.txt` (gitignored) — move to `backend/.env` as a proper setting; never hardcode. House rule: no model/vendor names in source or docs; the model id belongs in `DRAFT_MODEL`-style config only.
- `GET /api/v1/health/model` needs a branch for the new provider.

### 5. Human gating — true today, but make it a policy, not an accident

**Have:** The orchestrator persists **every** email — both lanes — as `DRAFT_GENERATED` and returns; no code path sends anything anywhere. The router's FAQ gate is conservative (eligible intent + confidence ≥ 0.65 + grounding present; sensitive intents always escalate).

**Gaps / decisions:**
- The FAQ lane is *architecturally* an auto-reply lane (its name, the analytics, the frontend "Auto-Replies" page — currently showing drafts that were never sent). For v1, repurpose it explicitly as a **priority/suggested-reply marker**: all emails, both lanes, land in the human queue; FAQ-lane drafts are simply higher-confidence suggestions. Add an `AUTO_SEND_ENABLED`-style flag, **default false**, so the future auto-send decision is a deliberate config change with an audit trail, not a code path that quietly activates when sending is implemented.
- Verify the review UI exposes approve on FAQ-lane emails too (the keyboard shortcut path was human-review-only in Phase 5F); in v1 a human must be able to action every email.
- On real traffic this queue is nearly all-human anyway: only ~27% of real tickets clear the 0.65 confidence threshold with the keyword classifier, and two-thirds classify as the sensitive `review_assignment` intent.

### 6. Approve / modify / send — approval done, sending absent

**Have:** `PATCH /{id}/approve` with optional `final_text`: whitespace-insensitive edit detection, original draft preserved (`draft.original_draft_text`), both texts in the audit entry, status → `approved`. Reroute and chair-reassign endpoints with full audit. Frontend split-pane queue with edit, diff view, keyboard shortcuts.

**Gaps:**
- **No send step.** `EmailStatus.SENT` exists in the enum but nothing transitions to it. Needed: on approve (or as a separate explicit "send" action — recommended, so approve and send remain distinct decisions), `PUT /api/v2/tickets/{zendesk_id}` with the approved text as a comment. Launch mode per `ZENDESK_API.md`: post as **internal note** (`public: false`) first; flip to public replies only after trust is established. Record the returned comment/audit id on the email.
- **OAuth write scope unproven.** The client has only been exercised with `scope=read`; whether it is granted `write` must be verified before any write-back work (1-line test, but a hard blocker if the grant is read-only).
- Failure handling: Zendesk 429/5xx on write-back must not lose the approval — approve locally, mark send-pending, retry.

### 7. Recording human actions — complete

Append-only `audit_logs` with distinct actions (`approved` incl. both texts when edited, `rerouted`, `chair_reassigned`, `flagged_low_confidence`, `flagged_meaningful_edit`), active-learning candidates endpoint + analytics card, RL feedback hooks on approve/reroute, per-email stage traces (`/{id}/trace`), SSE live queue. **No v1 gap.** Once ticket identity exists (step 1), audit entries gain a join key to the real Zendesk ticket — worth adding to the write-back audit details.

---

## Cross-cutting gaps (not tied to one step)

1. **No authentication on the app itself.** Every endpoint — including approve — is open; `approved_by` is client-supplied free text. Before this system can send email on behalf of the conference, the review UI/API needs at least a shared-secret or basic SSO gate, and `approved_by` should come from the authenticated identity.
2. **Deployment target.** Docker Compose is ready and verified, but the app needs a persistent host (the HPC login environment is unsuitable for a 24/7 service). SQLite → PostgreSQL is a one-line env change (Phase 3C); the in-process SSE broker and module singletons require single-instance/single-worker deployment — fine at this traffic level, but a stated constraint.
3. **Classifier/calibration tuned on synthetic data.** Real-traffic numbers (27% above threshold) confirm the synthetic eval overstates confidence quality. The pulled corpus enables an out-of-sample re-fit of calibration and a real eval set — this should accompany, not follow, deployment.
4. **Secrets hygiene.** `docs/secrets.txt` is gitignored but ad-hoc; consolidate into `backend/.env` (already ignored + already the config seam).
5. **Legacy vocabularies.** `app/models/enums.py::EmailIntent`/`RoutingLane` use an old unused vocabulary — cosmetic, but confusing to new contributors touching the send state machine.

## Classifier reality check (added 2026-07-17)

The keyword classifier's rules were derived from the toy dataset; measured against the real corpus and real traffic it cannot be trusted as-is:

- **"Coverage" of policies is trivially 100% — and that's the problem.** Every one of the 93 real chunks contains at least one intent keyword, but only because the vocabulary is generic: `review_assignment`'s keywords alone hit **66/93 chunks (71%)**, `formatting_requirements` 55, `ethics_concern` 55. Intent→policy association is nearly uninformative, so (a) the intent token appended to the retrieval query adds little discriminating signal, and (b) the keyword→candidate-pool design (deployment TODO 4) would produce huge overlapping pools with almost no filtering value. Static evidence against TODO 4, ahead of the ablation.
- **On 4,094 real tickets:** zero-keyword fallback is rare (1.5% — again, broad vocabulary), but **14.9% of tickets trip the near-tie penalty** (intents collide) and the confidence histogram is smeared across the whole range (28% at ≤0.3; only ~27% clear the 0.65 FAQ threshold). Confidence semantics tuned on synthetic data do not transfer.
- **Taxonomy gaps:** real traffic contains recurring types with no intent — reviewer recruitment/volunteering, emergency-PC operations, role/account questions ("why am I called PC?"), AI-review complaints, camera-ready/publication, registration/attendance. Today these are absorbed by `review_assignment`/`general_inquiry`, which partly explains the 67.8% `review_assignment` share.
- **Its real v1 job is smaller than it looks:** with every send human-gated and the FAQ lane demoted to a suggestion marker, intent classification actually gates **chair routing and queue priority**, not sending. Evaluate it against that bar.

**Path:** don't hand-tune more keywords against real traffic (hand-overfitting). Collect real intent labels via the labeling pass (free — same LLM call), evaluate the keyword classifier honestly (accuracy, confusion, what `review_assignment` absorbs, calibration re-fit out-of-sample), and if inadequate, retrain the existing trainable backend (`CLASSIFIER_BACKEND=trainable`, MiniLM+LogReg — already wired) on scaled-up LLM labels (~1–2k tickets). Keyword stays as the cold-start fallback.

## Test roadmap (priority order, added 2026-07-17)

1. **Labeling pass** (~200 stratified Marc threads; LLM anchored on the chair's real reply; human spot-check ~20): policy-answerable? · relevant chunk ids (multi-gold) · intent label. Unblocks everything below.
2. **Coverage number**: fraction of real traffic answerable from the policy docs — bounds the FAQ lane and sets expectations for retrieval's reach.
3. **Retrieval ablation** (local, fast): bm25/faiss/fusion × (body / body+intent / subject+body) on the answerable subset. Decides backend, whether the intent token stays in the query, and kills or revives TODO 4.
4. **Classifier eval on real labels**: per-intent precision/recall + confusion; taxonomy-gap measurement (fraction fitting no current intent → proposed new intents); chair-routing accuracy (its real v1 job); calibration re-fit + reliability out-of-sample.
5. **Drafter auth fix**, then **end-to-end draft eval** on the same tickets: no-guide vs guide-v1 vs guide-v2 vs the chair's real reply, with retrieval labels for failure attribution.
6. **Engineering regression**: chunker unit tests (count/id format/size bound), KB seeding test, style-guide injection tests once the seam exists; synthetic 67-set retained only as CI smoke.

## RESULTS — real-ticket evaluation (run 2026-07-16/17, Phase 7D)

Roadmap steps 1–5 executed. Artifacts: `data/eval_real/` (sample.jsonl 202 tickets · labels.jsonl · drafts.jsonl 74 · judge_batches/) and `backend/reports/real_eval_20260716_203615.json`. Label quality was independently spot-checked (20 tickets, separate model family): **answerable 19/20, chunk selection 9/9, intent 19/20 agreement**; noted bias — the labeler is slightly *generous* on answerability, so true coverage is likely a little below the number reported.

**1. Coverage: 18.3%** (37/202 policy-answerable; author-facing intents deliberately over-sampled, so raw traffic is likely lower). By intent: authorship 67% · ethics 44% · formatting 41% · general 25% · review_assignment 18% · **technical_issue 0/53 · submission_withdrawal 0/9** (the six policy docs contain no withdrawal procedure — a KB content gap to raise with the chairs). Conclusion: the FAQ lane's reach is bounded at roughly one-fifth of traffic; the human-review lane is the product.

**2. Retrieval ablation** (37 gold tickets, multi-gold; hit@3 / recall@3 / nDCG@3):
| query | bm25 | dense | fusion |
|---|---|---|---|
| body300 | .378/.185/.198 | .568/.356/.344 | .540/.369/.350 |
| body300+kw_intent | .378/.185/.198 | .486/.297/.302 | .540/.383/.351 |
| **subject+body300** | .486/.261/.244 | .595/.387/.348 | **.649/.432/.366** |

Decisions: **(a)** the classifier's intent token HURTS dense retrieval (−.08 hit@3) → remove it from the retrieval query; **(b)** the subject belongs IN the query (orchestrator currently drops it) — biggest single win; **(c)** **fusion wins on real data** (.649 hit@3), reversing the toy-corpus verdict (5C) — real queries are noisier and bm25+dense are complementary; **(d)** TODO 4 (keyword→candidate pools) is dead: static analysis showed intent→policy association is uninformative and the ablation confirms intent conditioning only hurts. Sobering baseline: best real hit@3 = **0.65** vs 0.98 synthetic — ~⅓ of answerable tickets miss the right chunk in top-3. **Orchestrator change recommended: query = subject + body[:300], no intent token, RETRIEVAL_BACKEND=fusion.**

**3. Classifier on real labels**: intent accuracy **57.8%**, chair-routing accuracy **85%** (most confusions stay within one chair's area), taxonomy gap 7.4%. Worst: submission_deadline precision **0.04** (its keywords fire on reviewer-deadline/outage complaints — n.b. sample was stratified by predicted intent, so precision is the fair read); technical_issue recall **0.32** (absorbed by review_assignment ×18 and deadline ×13). Verdict: unusable as an FAQ gate, serviceable for chair routing; retrain the trainable backend on scaled LLM labels (~1–2k) before relying on intent anywhere else.

**4. Draft eval — style guide v2 vs none** (37 tickets × 2 arms, blinded judges from a separate model family, judged against the chair's real replies):
| config | info_acc (0-5) | style (0-5) | grounding violations | action claims | blinded preference |
|---|---|---|---|---|---|
| none | 2.57 | 2.57 | 1 | 1 | 7 |
| **v2** | **2.65** | **2.78** | **0** | **0** | **20** (10 ties) |

**v2 adopted**: preferred 20-7 blinded, better on every metric, and the none-arm produced the only fabricated recipient name and the only false action-claim. → Set `STYLE_GUIDE_PATH=data/style_guide/style_guide_v2.md` as the deployment default.

**Failure modes both arms share** (the real work remaining):
- **Under-answering dominates**: on most tickets the human chair issued a firm ruling resting on knowledge outside the retrieved context (FAQ links, ethics report form, reciprocal-reviewer rules, extension cutoffs, "checklist-in-supplementary is fine"). Drafts correctly refuse to invent it — absolute info accuracy (~2.6/5) is capped by retrieval + corpus coverage, not by drafting. Fixes ranked: enrich the KB (FAQ doc + operational/reciprocal-reviewer policies + withdrawal procedure), then retrieval quality, then any prompt work.
- **Chair-discretion divergence**: in some cases the written policy is stricter than actual chair practice (e.g. LLM-use-in-review reply permitted with ownership; drafts followed the stricter written policy). Grounded drafting cannot reproduce discretion — for these, the draft is a starting point by design.
- **Chair-facing meta-notes leak into reply bodies** in both arms (the "ACTION NEEDED FOR CHAIR" class, invited by the base prompt's human-review-lane instruction). The guide reduced but did not eliminate it → implement the structured `reply_text` / `notes_for_chair` output (§3 remaining gaps) — prompt rules alone won't close this.

**Ops note (2026-07-16)**: `/projects/bdem` hit its file-count quota mid-run (EDQUOT, allocation-wide, ~765k/750k files — not this project's usage); work continued from a home mirror and the canonical working copy is now **/work/hdd/bdem/jpang1/confmail/** (full repo + policies + all eval artifacts). Code deltas vs /projects: drafter bearer-auth + style-guide seam + `max_completion_tokens` retry (tests 151→152 incl. param-swap), eval scripts (`label_real_tickets.py`, `bench_real.py`, `draft_eval.py`), `data/eval_real/`. Sync /projects when quota clears — or make /work/hdd the repo home.

## What already exceeds v1 needs

Confidence calibration (opt-in, big measured routing win), chair routing + reassignment UI, RRF fusion retrieval, template fallback drafter, per-email tracing, live SSE queue, active-learning flagging, eval harness with retrieval-only metrics. None of these block v1; several (calibration, active-learning) become much more valuable the day real traffic flows.

## Recommended build order for the v1 delta

1. **Migration: `zendesk_ticket_id` (+ `external_id` unique index) on `emails`** — prerequisite for fetch, dedup, and write-back.
2. **Poller service** (read-only, safe to run immediately): incremental export → dedup by ticket id → ingest first requester comment → pipeline. Persist the export cursor.
3. **Real policy KB**: ~~chunk the 6 AAAI markdown docs~~ ✅ done (7C, 93 chunks); remaining: seed into `policy_documents`, real-ticket ground truth, retrieval ablation per the test roadmap.
4. **Style guide**: ~~distill from `marc_threads.jsonl`~~ ✅ done (7C, v1 distilled + v2 curated); remaining: `STYLE_GUIDE_PATH` injection + structured `reply_text`/`notes_for_chair` output + A/B in the end-to-end eval.
5. **Drafting provider auth**: bearer-token support on the chat-completions branch; key via `.env`; health-check branch.
6. **Write-back behind a default-off flag**: explicit send action, internal-note mode first, `SENT` status + comment id in audit; verify OAuth write scope first (blocker check).
7. **Hardening**: app auth, PostgreSQL, hosted deployment, then live shadow-run (drafts as internal notes) while measuring edit rates via the existing active-learning signals.

Steps 1–2 and 3–5 are independent tracks; 6 depends on 1; 7 gates real traffic.
