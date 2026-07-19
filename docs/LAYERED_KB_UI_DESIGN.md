# Layered KB — Frontend (Chair Governance UI) Design

Status: **DRAFT for review** · Date: 2026-07-18 · Builds on: `feature/layered-kb` (backend complete) · Spec of record for backend: `docs/LAYERED_KB_DESIGN.md`

## 1. Problem

The layered-KB governance backend is complete (create internal / retire / similar / audit), but there is **no UI** — a chair can only exercise it via curl. This adds a "Knowledge Base" page so a chair can browse the KB, add internal policies (with the similarity-assist override flow), retire/reactivate, and review + revert changes from the audit history.

## 2. Decisions (locked)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Scope | List + add-internal (similar-assist + `retire_keys`) + retire + **reactivate** + **policy-audit history with revert** |
| 2 | Similar-assist trigger | Explicit **"Check for related"** button (not debounced-on-keystroke) |
| 3 | Actor (no auth yet) | Hardcoded placeholder `ACTOR = "Chair1"` (frontend constant; audit records `chair:Chair1`) |

### 2.1 Revert semantics (current-truth)
Each `policy_audit_logs` row is a real state transition. Revert applies the inverse of a policy's **latest** change:
- Revert an addition (`policy_created`, policy active) → **retire** (active→inactive).
- Revert a retirement (`policy_retired`, policy inactive) → **reactivate** (inactive→active).

The only new state transition is **reactivate** (inactive→active). Revert is UX over the existing retire + new reactivate — no separate "revert" action string; each revert produces a truthful `policy_retired`/`policy_reactivated` audit entry. Revert is offered only on the **latest** audit entry per policy (the one that caused the current state); older entries are read-only history.

## 3. Backend additions (this is API-only today)

All in `backend/`, following the existing repository + `app/api/v1/policies.py` patterns. Dialect-agnostic, DB via repositories, no vendor names.

### 3.1 `PolicyRepository`
- `list(db, *, visibility=None, status=None, search=None, limit=200, offset=0) -> list[PolicyDocument]` — filtered browse (visibility/status exact match; `search` = case-insensitive substring on title/content via `ilike`). Ordered by id.
- `reactivate(db, policy_key) -> PolicyDocument | None` — mirror of `retire`; sets `status="active"`; returns row or None.

### 3.2 `PolicyAuditRepository`
- `list(db, *, limit=100, offset=0) -> list[PolicyAuditLog]` — newest-first (id desc) governance history.

### 3.3 Endpoints (`app/api/v1/policies.py`)
- `GET /api/v1/policies` — query params `visibility`, `status`, `search`, `limit` (default 200), `offset` → `{"policies": [...]}` (no `total` — KB is small; pagination beyond `limit` is out of scope). Each item: `policy_key, title, content, category, tags, visibility, status, source, updated_at`.
- `PATCH /api/v1/policies/{key}/reactivate` — body `{actor}` → 404 if missing; no-op (no audit/rebuild) if already active; else reactivate + audit `policy_reactivated` (before `{"status":"inactive"}`) + `rebuild_index()`. Returns `{policy_key, status}`.
- `GET /api/v1/policies/audit` — query `limit` (default 200), `offset` → `{"entries": [...]}`. Each: `id, policy_key, action, actor, before, after, timestamp`.

(`POST /policies`, `PATCH /policies/{key}/retire`, `POST /policies/similar` already exist.)

## 4. Frontend (`frontend/`)

Follows the mapped conventions: `"use client"` pages; axios client in `src/lib/api/` re-exported from `index.ts`; React Query hooks in `src/hooks/` with an invalidate helper; types in `src/types/index.ts`; CSS-var tokens (never hex); bespoke `ui/` primitives (`Badge`, `EmptyState`, `ErrorBanner`, `LoadingSpinner`); **no modal/toast** — inline collapsible panel (like `IngestPanel`) + inline success/error.

### 4.1 Types (`src/types/index.ts`)
`PolicyVisibility = "public" | "internal"`, `PolicyStatus = "active" | "inactive"`, `PolicyDocument`, `PolicyAuditEntry`, request/response shapes (`CreatePolicyRequest`, `SimilarPolicy`, list/audit responses) — each JSDoc-citing the backend source, per convention.

### 4.2 API client (`src/lib/api/knowledgeBase.ts`, re-export in `index.ts`)
`listPolicies(params)`, `createPolicy(data)`, `retirePolicy(key, actor)`, `reactivatePolicy(key, actor)`, `findSimilarPolicies({title, content})`, `listPolicyAudit(params)`.

### 4.3 Hooks (`src/hooks/useKnowledgeBase.ts`, re-export in `index.ts`)
`usePolicies(params)` (useQuery), `usePolicyAudit(params)` (useQuery), and mutation hooks `useCreatePolicy` / `useRetirePolicy` / `useReactivatePolicy` sharing a `useInvalidateKb()` helper (invalidates `["knowledgeBase"]` + `["policyAudit"]`). Similar-check is a mutation (`useFindSimilar`) or a manual `findSimilarPolicies` call triggered by the button.

### 4.4 Page (`src/app/knowledge-base/{page.tsx,layout.tsx}`) + nav
Single-column (mirror `audit/page.tsx`). Nav entry added to `Sidebar.tsx` `NAV_ITEMS` (`/knowledge-base`, `Library` icon). Two views via a small segmented toggle at top: **Policies** and **History**.

`ACTOR = "Chair1"` constant lives in the page/hook module and is passed to every mutation.

**Policies view:**
- Filters row: search input (250ms debounce → server `search` param), Visibility toggle (All/Public/Internal), Status toggle (Active/Inactive/All). Combined into a `useMemo` params object → `usePolicies`.
- `[+ Add internal policy]` opens the inline collapsible add-panel.
- List rows: `policy_key` + `Badge`(visibility) + `Badge`(status), title, truncated content. Row action: **Retire** if active, **Reactivate** if inactive. Public rows are content-read-only (no edit) but retire/reactivate is allowed (that's the override mechanism). Inactive rows dimmed.
- `EmptyState` when no results; `ErrorBanner` on error; `LoadingSpinner` while pending.

**Add-internal panel** (collapsible, in-place):
- Fields: title, content (textarea), category (optional), tags (optional comma-split). Controlled inputs, shared field style.
- **"Check for related policies"** button → `findSimilarPolicies({title, content})` → renders the top matches, each with a "supersede (retire this)" checkbox → checked keys become `retire_keys`.
- **Create** → `createPolicy({title, content, category, tags, actor: ACTOR, retire_keys})` → on success the panel closes and the list + history refetch (success is implicit — the new row appears and any superseded row flips to inactive); errors surface inline via `ErrorBanner`.

**History view:**
- `usePolicyAudit` list, newest first. Each entry: timestamp, `action` badge (created/retired/reactivated), `policy_key`, actor, and a compact before→after (status) summary.
- **Revert** button shown only on the latest entry per policy (compute client-side: first-seen policy_key in the newest-first list): if that policy is currently active → calls `retirePolicy` (undo add/reactivate); if inactive → calls `reactivatePolicy` (undo retire). Confirm inline before firing. After revert, both queries refetch (a new audit entry appears).

## 5. Verification

Frontend has no unit-test harness (CI runs `tsc --noEmit`). So:
- Backend additions: pytest (TDD) — repository `list`/`reactivate`, audit `list`, and the three endpoints (list filters, reactivate 404/no-op/success+audit, audit list).
- Frontend: `npx tsc --noEmit` clean; then **run the app and drive the page** — add an internal policy (with a related-policy retire), see it in the list, retire/reactivate a row, open History, revert the latest change, confirm the list reflects it.

## 6. Out of scope (YAGNI)
Editing public/internal policy *content* in the UI (importer-owned; internal edits deferred) · real auth/accounts (hardcoded `Chair1` stand-in) · pagination controls beyond a simple limit (list caps at 200; add "load more" only if needed) · reverting arbitrary non-latest historical entries (only the latest per policy is revertable).
