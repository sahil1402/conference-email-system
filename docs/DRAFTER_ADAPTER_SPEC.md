# Drafter Adapter Specification

**Status:** current as of Phase 5H · **Audience:** anyone adding a new drafter backend
**Scope:** the reply-drafting seam only. You do not need to read the three existing
backend implementations line by line to add a fourth — this document is the contract.

> **Naming convention:** this document describes drafter backends by **capability and
> role only** — never by product, vendor, or model name. Please keep it that way when
> extending it.

---

## 1. Purpose

The drafter is the pipeline stage that turns a classified, routed, policy-grounded email
into a reply draft. It is deliberately a **swappable seam**: the rest of the pipeline
(classifier → retriever → router → **drafter** → persistence) depends only on the
drafter's input and output *contract*, never on how a given backend produces text. One
configuration flag selects the active backend at runtime.

This matters because the "right" way to generate a reply varies by deployment:

- A venue may **restrict or forbid AI-generated content**, requiring a deterministic,
  no-generation backend.
- A deployment may need to run **fully offline / self-hosted** with no external network
  dependency.
- Different quality/latency/cost trade-offs are appropriate in different settings.
- **Future research backends** (e.g. a different generation strategy) should be
  pluggable without touching the orchestrator, the API layer, or the UI.

A new backend that honors the contract below drops in behind the config flag with **no
changes to any other pipeline stage**.

---

## 2. Interface contract

### 2.1 Entry point

The active backend is invoked through a single asynchronous method on the drafter:

```python
async def draft(
    email: dict,
    classification,          # ClassificationResult
    retrieved_chunks: list,  # list[RetrievedChunk]
    routing,                 # RoutingDecision
) -> DraftResponse
```

- The **public** method is always `async`. A backend's internal work may be synchronous
  (no network) or asynchronous (network I/O) — see §6.1.
- Backends are selected by the `MODEL_PROVIDER` setting and implemented as a **dispatch
  branch** inside the drafter (there is no abstract base class or `Protocol`; the
  contract is behavioral, enforced by tests — see §7).

### 2.2 Input types

Do **not** redefine these — consume the existing Pydantic models by name. They live in
the pipeline modules (not in `app/models/schemas.py`, which defines a different, legacy
set not used by the live pipeline):

| Input | Type | Defined in | What a backend reads from it |
|---|---|---|---|
| `email` | `dict` | (plain dict) | `from`/`sender`, `subject`, `body` |
| `classification` | `ClassificationResult` | `app/pipeline/classifier.py` | `intent`, `confidence` (and, when calibration is active, `calibrated_confidence`) |
| `retrieved_chunks` | `list[RetrievedChunk]` | `app/pipeline/retriever.py` | `policy_id`, `title`, `content` — **the only sanctioned source of factual claims** |
| `routing` | `RoutingDecision` | `app/pipeline/router.py` | `lane` (`faq` / `human_review`), `reason` |

### 2.3 Output type

Return a `DraftResponse` (defined in `app/pipeline/drafter.py`):

| Field | Type | Meaning |
|---|---|---|
| `draft_text` | `str` | the reply text (or a safe fallback message) |
| `citations` | `list[str]` | policy ids the draft relied on (e.g. `["policy_004"]`) |
| `model_used` | `str` | an identifier for what produced the text, or `"none"` when nothing was generated |
| `generation_metadata` | `dict` | free-form provenance: at minimum echo `lane`; network backends should include token usage; see §6.3 for the current variance |

---

## 3. Required behavior

A conforming backend **MUST**:

### 3.1 Never raise on failure

`draft()` must never propagate an exception to the orchestrator. Wrap all fallible work
(network calls, parsing, auth) in a catch-all and **degrade to a safe fallback
`DraftResponse`** — a short "draft unavailable" message, `citations=[]`,
`model_used="none"`, and the error captured in `generation_metadata` (`error`,
`error_type`). The orchestrator treats drafting as best-effort and decides downstream
status from the response, so a raised exception is a contract violation.

### 3.2 Never fabricate ungrounded policy claims

Every factual statement in `draft_text` must be grounded in the supplied
`retrieved_chunks`. A backend must **not** invent, assume, or generalize a policy that is
not present in that context. When `retrieved_chunks` is empty, the backend must **not**
answer from general knowledge — it must return a clear "no grounded answer available /
routed to a human" message instead. (A backend that copies retrieved text verbatim
satisfies this by construction; a generative backend must be instructed to, and its tests
must check that it does not fabricate.)

### 3.3 Return within a bounded time (or time out gracefully)

Any backend that performs network I/O must set an explicit timeout and treat a timeout as
a normal failure that falls back per §3.1 — it must not hang the request. **Reference
value:** the self-hosted network backend currently uses a generation timeout of
**60 seconds**. Choose a bound appropriate to your backend; do not leave network calls
unbounded. (A non-network backend has no timeout obligation.)

### 3.4 Report its health for `GET /api/v1/health/model`

Health is currently resolved in a **per-provider branch in `main.py`** (not as a method
on the drafter). A new backend must add its branch there and return a status the health
endpoint understands:

- A **network** backend should perform a quick, short-timeout reachability check
  (the current self-hosted backend probes its endpoint with a **3-second** timeout — note
  this is distinct from the 60-second *generation* timeout) and report reachable vs
  unreachable.
- A **no-dependency** backend is trivially available and reports healthy without probing.

---

## 4. Registration — adding a new backend

Four edits, no changes to the orchestrator, API routes, or UI:

1. **Config flag** — in `app/core/config.py`, add your backend's key string to the
   `MODEL_PROVIDER` typed `Literal`. It currently enumerates the cloud provider (with a
   legacy alias), the self-hosted provider, the template provider, and a deterministic
   stub. Add any backend-specific configuration (base URL, secret, tuning) as new fields
   on `Settings`, following the existing per-backend config pattern, and read them via
   `from app.core.config import settings` (never hardcode endpoints, secrets, or model
   identifiers in source).

2. **Dispatch branch** — in `app/pipeline/drafter.py`, add a branch to
   `ResponseDrafter.draft` for your provider key that returns a `DraftResponse`. A
   network backend typically reuses the shared grounded prompt builder; a backend that
   needs a different call shape (or no call at all) may bypass it, as the template backend
   does. Keep heavy or optional imports **lazy** (inside the branch) so selecting another
   backend never forces your dependency to load.

3. **Env documentation** — in `backend/.env.example`, add a one-line entry for your
   provider key to the `MODEL_PROVIDER` comment block (the block introduced in Phase 5D),
   stating in one line when to use it and what it depends on.

4. **Health branch** — in `main.py`'s `GET /api/v1/health/model` handler, add a branch for
   your provider per §3.4.

Then add a test file per §7.

---

## 5. Reference implementations

Three backends ship today. Described by role only:

| Role | Dependency | Latency | Quality ceiling | Grounding | Notes |
|---|---|---|---|---|---|
| **Cloud-hosted, API-based** | External hosted API + network + credential | Network round-trip | Highest (large hosted model) | Prompt-instructed, then citations parsed from output | Falls back with no network call when no credential is configured |
| **Self-hosted, network-based** | Local/network inference server exposing a chat-completions-style `POST /chat/completions` endpoint | Network round-trip to a local host | Depends on the self-hosted model | Prompt-instructed, then citations parsed from output | Explicit 60 s generation timeout; degrades to fallback on any error |
| **Template-based, zero-dependency** | None (fully offline, no model call) | Effectively instant | Rigid — bounded entirely by retrieval quality; cannot synthesize or reorder | **Zero hallucination by construction** (copies retrieved policy text verbatim) | Returns a "routed to a human" message when nothing is retrieved; safest fallback for AI-restricted venues |

The zero-network property of the template backend is a **trait of that implementation**,
not a requirement of the interface — a new backend may make network calls.

---

## 6. Known inconsistencies (current implementations differ here)

These are real differences between the three shipping backends. They are documented so
you know what to expect versus what is aspirational; a new backend should follow the
stricter/most-consistent choice where noted.

1. **Internal signature is not uniform.** The two network backends' internal helpers take
   an already-assembled prompt plus `routing`; the template backend's own method takes
   `(email, intent, retrieved_chunks)` — it receives only `intent` (not the full
   classification) and does **not** take `routing`. An adapter bridges the template
   backend into the common `draft()` call (it pulls `intent` out of the classification and
   stamps the `lane` into metadata afterward). **The stable contract is the public
   `draft(email, classification, retrieved_chunks, routing)` signature** (§2.1); how you
   fan those arguments into your backend internally is your choice.

2. **Synchronous vs. asynchronous branches.** The network backends are `async`; the
   template branch is synchronous. Both are fine — the public `draft()` is `async`
   regardless. If your backend is synchronous, return its result directly from the branch;
   if asynchronous, `await` it.

3. **`generation_metadata["provider"]` is not always present.** The self-hosted and
   template backends set a `provider` key; the cloud backend's success path currently does
   **not**. Downstream consumers that need it fall back to the configured provider name.
   **Recommendation for new backends:** always set `generation_metadata["provider"]`.

4. **Timeout coverage is partial.** Only the self-hosted backend sets an explicit
   generation timeout (60 s). The cloud backend sets none in our code and relies on its
   client's default. **Recommendation:** set an explicit timeout for any network backend.

5. **`model_used` semantics vary** — network backends report a concrete model identifier
   read from config; the template backend reports the role word `"template"`; the fallback
   path reports `"none"`. Treat this field as a free-form provenance string, not a
   guaranteed model id.

6. **Citations are derived differently** — network backends parse policy ids out of the
   generated text; the template backend sets them directly from the retrieved chunk ids.
   Either is acceptable as long as `citations` reflects the policies the draft actually
   relied on.

7. **No formal interface object.** There is no abstract base class or `Protocol` — the
   contract is behavioral and enforced by tests (§7), and dispatch/health are wired in two
   separate files (`drafter.py` and `main.py`). This is a deliberate MVP choice; a future
   phase may formalize it into a `Protocol`.

---

## 7. Testing expectations

Add a test file for your backend modeled on `backend/tests/test_template_drafter.py`. At
minimum, cover:

1. **Output shape** — `draft()` returns a `DraftResponse` with a non-empty `draft_text`,
   a `citations` list, a `model_used` string, and a `generation_metadata` dict.
2. **Grounding** — the draft's factual content traces to the supplied `retrieved_chunks`;
   with **zero** chunks, the backend returns the safe "no grounded answer" message and
   does **not** fabricate a policy (assert no invented policy ids / no answer from general
   knowledge).
3. **Fallback on failure** — simulate the backend's failure mode (unreachable endpoint,
   bad response, missing credential) and assert `draft()` **does not raise**, returns a
   fallback `DraftResponse` (`model_used == "none"`), and records the error in
   `generation_metadata`.
4. **No unexpected network call** — assert your backend only touches the network when it
   should. For a no-dependency backend, patch the HTTP client to fail if constructed and
   assert `draft()` still succeeds (the template test does exactly this). For a network
   backend, assert the call targets the configured endpoint and honors the timeout.
5. **Health branch** — assert `GET /api/v1/health/model` reports your backend correctly
   (reachable/unreachable for network backends; healthy for no-dependency backends).

Tests must run with **no real external API and no real network** — mock the transport, as
the existing drafter tests do.
