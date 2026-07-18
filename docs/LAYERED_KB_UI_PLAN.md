# Layered KB — Frontend (Chair Governance UI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A "Knowledge Base" page letting a chair browse the layered KB, add internal policies with the similarity-assist override flow, retire/reactivate, and review + revert changes from the audit history — backed by three new read/write endpoints.

**Architecture:** Backend first (repository methods + `GET /policies`, `PATCH /policies/{key}/reactivate`, `GET /policies/audit`, TDD with pytest), then the frontend (types → api client → hooks → page views), following the existing Next.js conventions. Spec: `docs/LAYERED_KB_UI_DESIGN.md`.

**Tech Stack:** FastAPI + async SQLAlchemy (backend); Next.js 14 App Router + TypeScript + axios + React Query + Tailwind/CSS-var tokens (frontend).

## Global Constraints

- Spec of record: `docs/LAYERED_KB_UI_DESIGN.md`. Backend spec: `docs/LAYERED_KB_DESIGN.md`.
- Backend: DB access only via repositories; dialect-agnostic (no `func.json_extract`; use SQLAlchemy `.ilike()`/`.in_()`/`==`) — SQLite + Postgres. No AI vendor/model names.
- Revert semantics: retire (active→inactive) and reactivate (inactive→active) are the only transitions; "revert" is UX over them applied to a policy's latest audit entry.
- Actor: frontend hardcodes `ACTOR = "Chair1"` and passes it to every mutation; the backend prefixes `chair:` when auditing.
- Frontend: `"use client"` pages; axios client in `src/lib/api/` (re-export in `index.ts`); React Query hooks in `src/hooks/` (re-export in `index.ts`) with an invalidate helper; types in `src/types/index.ts` with a JSDoc citing the backend source; **CSS-var tokens only, never hex**; bespoke `ui/` primitives; **no modal/toast** — inline collapsible panel (mirror `components/dashboard/IngestPanel.tsx`) + inline `ErrorBanner`/success line.
- No Claude attribution trailers in commit messages.
- Env for backend python/pytest/alembic: `export PATH=/u/jpang1/miniconda3/envs/autoexp/bin:$PATH`; run from `backend/`. Do NOT run the full pytest suite per task (it's ~7 min here) — run the focused test files named in each task.
- Frontend checks run from `frontend/`: `npx tsc --noEmit` (the CI gate). There is no frontend unit-test harness.

---

### Task 1: Backend repository methods (`list`, `reactivate`, audit `list`)

**Files:**
- Modify: `backend/app/repositories/policy_repository.py`
- Modify: `backend/app/repositories/policy_audit_repository.py`
- Test: `backend/tests/test_policy_kb_layers.py` (append) and `backend/tests/test_policy_audit.py` (append)

**Interfaces:**
- Produces:
  - `PolicyRepository.list(db, *, visibility=None, status=None, search=None, limit=200, offset=0) -> list[PolicyDocument]` — filters (exact `visibility`, exact `status`, case-insensitive substring `search` over title+content), ordered by id.
  - `PolicyRepository.reactivate(db, policy_key) -> PolicyDocument | None` — sets `status="active"`; returns row or None.
  - `PolicyAuditRepository.list(db, *, limit=200, offset=0) -> list[PolicyAuditLog]` — newest first (id desc).

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_policy_kb_layers.py
async def test_list_filters_and_search(session):
    repo = PolicyRepository()
    session.add_all([
        PolicyDocument(policy_key="policy_1", title="Submission deadline", content="deadline info", visibility="public", status="active"),
        PolicyDocument(policy_key="int_x", title="Internal ruling", content="chair note", visibility="internal", status="active"),
        PolicyDocument(policy_key="policy_2", title="Old rule", content="retired", visibility="public", status="inactive"),
    ])
    await session.commit()

    assert {p.policy_key for p in await repo.list(session)} == {"policy_1", "int_x", "policy_2"}      # no filter → all
    assert {p.policy_key for p in await repo.list(session, status="active")} == {"policy_1", "int_x"}
    assert {p.policy_key for p in await repo.list(session, visibility="internal")} == {"int_x"}
    assert {p.policy_key for p in await repo.list(session, search="DEADLINE")} == {"policy_1"}          # case-insensitive


async def test_reactivate(session):
    repo = PolicyRepository()
    session.add(PolicyDocument(policy_key="int_y", title="t", content="c", visibility="internal", status="inactive"))
    await session.commit()
    row = await repo.reactivate(session, "int_y")
    assert row is not None and row.status == "active"
    assert await repo.reactivate(session, "missing") is None
```

```python
# append to backend/tests/test_policy_audit.py
async def test_policy_audit_list_newest_first(session):
    repo = PolicyAuditRepository()
    await repo.log(session, policy_key="a", action="policy_created", actor="chair:1")
    await repo.log(session, policy_key="a", action="policy_retired", actor="chair:1")
    entries = await repo.list(session)
    assert [e.action for e in entries] == ["policy_retired", "policy_created"]   # newest first
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && export PATH=/u/jpang1/miniconda3/envs/autoexp/bin:$PATH && python -m pytest tests/test_policy_kb_layers.py::test_list_filters_and_search tests/test_policy_kb_layers.py::test_reactivate tests/test_policy_audit.py::test_policy_audit_list_newest_first -q`
Expected: FAIL (`AttributeError` — methods don't exist).

- [ ] **Step 3: Implement the repository methods**

Add to `PolicyRepository` in `backend/app/repositories/policy_repository.py` (imports `select`, `PolicyDocument` already present):

```python
    async def list(
        self,
        db: AsyncSession,
        *,
        visibility: str | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[PolicyDocument]:
        """Filtered browse of the KB (exact visibility/status; case-insensitive
        substring search over title+content), ordered by id."""
        stmt = select(PolicyDocument)
        if visibility is not None:
            stmt = stmt.where(PolicyDocument.visibility == visibility)
        if status is not None:
            stmt = stmt.where(PolicyDocument.status == status)
        if search:
            like = f"%{search}%"
            stmt = stmt.where(
                PolicyDocument.title.ilike(like) | PolicyDocument.content.ilike(like)
            )
        stmt = stmt.order_by(PolicyDocument.id).limit(limit).offset(offset)
        return list((await db.execute(stmt)).scalars().all())

    async def reactivate(self, db: AsyncSession, policy_key: str) -> PolicyDocument | None:
        """Undo a retirement: set status='active'. Returns the row or None."""
        row = await self.get_by_key(db, policy_key)
        if row is None:
            return None
        row.status = "active"
        await db.commit()
        await db.refresh(row)
        return row
```

Add to `PolicyAuditRepository` in `backend/app/repositories/policy_audit_repository.py` (import `select` from sqlalchemy at top: `from sqlalchemy import select`):

```python
    async def list(
        self, db: AsyncSession, *, limit: int = 200, offset: int = 0
    ) -> list[PolicyAuditLog]:
        """Return governance history, newest first."""
        stmt = (
            select(PolicyAuditLog)
            .order_by(PolicyAuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await db.execute(stmt)).scalars().all())
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && python -m pytest tests/test_policy_kb_layers.py tests/test_policy_audit.py -q`
Expected: PASS (all in both files).

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/policy_repository.py backend/app/repositories/policy_audit_repository.py backend/tests/test_policy_kb_layers.py backend/tests/test_policy_audit.py
git commit -m "feat(kb): PolicyRepository.list/reactivate + PolicyAuditRepository.list"
```

---

### Task 2: Backend endpoints (`GET /policies`, `PATCH .../reactivate`, `GET /policies/audit`)

**Files:**
- Modify: `backend/app/api/v1/policies.py`
- Test: `backend/tests/test_policies_endpoint.py` (append)

**Interfaces:**
- Consumes: `PolicyRepository.list/reactivate` (Task 1), `PolicyAuditRepository.list` (Task 1), existing `_policies`/`_audit` singletons, `_rebuild_index`.
- Produces:
  - `GET /api/v1/policies?visibility=&status=&search=&limit=&offset=` → `{"policies": [ {policy_key,title,content,category,tags,visibility,status,source,updated_at}, ... ]}`.
  - `PATCH /api/v1/policies/{policy_key}/reactivate` body `{actor}` → 404 if missing; no-op (no audit/rebuild) if already active; else reactivate + audit `policy_reactivated` (before `{"status":"inactive"}`) + rebuild. Returns `{policy_key, status}`.
  - `GET /api/v1/policies/audit?limit=&offset=` → `{"entries": [ {id,policy_key,action,actor,before,after,timestamp}, ... ]}` newest first.

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_policies_endpoint.py
async def test_list_policies_filters(client):
    c, factory = client
    async with factory() as s:
        from app.db.models import PolicyDocument
        s.add_all([
            PolicyDocument(policy_key="policy_1", title="Deadline", content="x", visibility="public", status="active"),
            PolicyDocument(policy_key="int_a", title="Ruling", content="y", visibility="internal", status="inactive"),
        ])
        await s.commit()
    r = await c.get("/api/v1/policies", params={"visibility": "public"})
    assert r.status_code == 200
    keys = [p["policy_key"] for p in r.json()["policies"]]
    assert keys == ["policy_1"]


async def test_reactivate_missing_and_success_and_noop(client):
    c, factory = client
    async with factory() as s:
        from app.db.models import PolicyDocument
        s.add(PolicyDocument(policy_key="int_b", title="t", content="c", visibility="internal", status="inactive"))
        await s.commit()
    assert (await c.patch("/api/v1/policies/nope/reactivate", json={"actor": "Chair1"})).status_code == 404
    ok = await c.patch("/api/v1/policies/int_b/reactivate", json={"actor": "Chair1"})
    assert ok.status_code == 200 and ok.json()["status"] == "active"
    from sqlalchemy import select
    from app.db.models import PolicyAuditLog
    async with factory() as s:
        acts = [a.action for a in (await s.execute(select(PolicyAuditLog))).scalars().all()]
    assert acts.count("policy_reactivated") == 1
    # second call: already active → no-op, no new audit row
    await c.patch("/api/v1/policies/int_b/reactivate", json={"actor": "Chair1"})
    async with factory() as s:
        acts2 = [a.action for a in (await s.execute(select(PolicyAuditLog))).scalars().all()]
    assert acts2.count("policy_reactivated") == 1


async def test_policy_audit_endpoint(client):
    c, factory = client
    await c.post("/api/v1/policies", json={"title": "New", "content": "z", "actor": "Chair1"})
    r = await c.get("/api/v1/policies/audit")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert any(e["action"] == "policy_created" for e in entries)
    assert "timestamp" in entries[0]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && export PATH=/u/jpang1/miniconda3/envs/autoexp/bin:$PATH && python -m pytest tests/test_policies_endpoint.py -q`
Expected: the three new tests FAIL (routes 404 / KeyError).

- [ ] **Step 3: Add the endpoints**

In `backend/app/api/v1/policies.py`, add a request model near the others and three routes. Add helper serializers.

```python
class ReactivateRequest(BaseModel):
    actor: str


def _policy_dict(p) -> dict:
    return {
        "policy_key": p.policy_key, "title": p.title, "content": p.content,
        "category": p.category, "tags": p.tags or [], "visibility": p.visibility,
        "status": p.status, "source": p.source,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("")
async def list_policies(
    visibility: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = await _policies.list(
        db, visibility=visibility, status=status, search=search, limit=limit, offset=offset
    )
    return {"policies": [_policy_dict(p) for p in rows]}


@router.patch("/{policy_key}/reactivate")
async def reactivate_policy(
    policy_key: str, payload: ReactivateRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    existing = await _policies.get_by_key(db, policy_key)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"policy {policy_key} not found")
    if existing.status == "active":  # no-op, don't audit/rebuild
        return {"policy_key": policy_key, "status": "active"}
    row = await _policies.reactivate(db, policy_key)
    await _audit.log(db, policy_key=policy_key, action="policy_reactivated",
                     actor=f"chair:{payload.actor}", before={"status": "inactive"},
                     after={"status": "active"})
    await _rebuild_index()
    return {"policy_key": policy_key, "status": row.status}


@router.get("/audit")
async def list_policy_audit(
    limit: int = 200, offset: int = 0, db: AsyncSession = Depends(get_db)
) -> dict:
    rows = await _audit.list(db, limit=limit, offset=offset)
    return {"entries": [
        {"id": e.id, "policy_key": e.policy_key, "action": e.action, "actor": e.actor,
         "before": e.before, "after": e.after,
         "timestamp": e.timestamp.isoformat() if e.timestamp else None}
        for e in rows
    ]}
```

NOTE on route ordering: FastAPI matches in declaration order. `GET /audit` must be declared BEFORE any `GET /{something}` path param route to avoid `audit` being captured as a path param. There is currently no `GET /{key}` route, so `GET ""`, `GET /audit`, `PATCH /{policy_key}/reactivate` are unambiguous — but keep `GET /audit` above any future `GET /{key}`.

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && python -m pytest tests/test_policies_endpoint.py -q`
Expected: PASS (all, including the 3 new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/policies.py backend/tests/test_policies_endpoint.py
git commit -m "feat(kb): GET /policies list, PATCH reactivate, GET /policies/audit endpoints"
```

---

### Task 3: Frontend data layer (types + api client + hooks)

**Files:**
- Modify: `frontend/src/types/index.ts` (append KB types)
- Create: `frontend/src/lib/api/knowledgeBase.ts`; Modify: `frontend/src/lib/api/index.ts` (add `export * from "./knowledgeBase";`)
- Create: `frontend/src/hooks/useKnowledgeBase.ts`; Modify: `frontend/src/hooks/index.ts` (add `export * from "./useKnowledgeBase";`)

**Interfaces:**
- Produces types: `PolicyVisibility`, `PolicyStatus`, `PolicyDocument`, `PolicyAuditEntry`, `SimilarPolicy`, `CreatePolicyRequest`, `PoliciesResponse`, `PolicyAuditResponse`, `SimilarResponse`, `PolicyListParams`.
- Produces api fns: `listPolicies`, `createPolicy`, `retirePolicy`, `reactivatePolicy`, `findSimilarPolicies`, `listPolicyAudit`.
- Produces hooks: `usePolicies`, `usePolicyAudit`, `useCreatePolicy`, `useRetirePolicy`, `useReactivatePolicy`, `useFindSimilar`, and `ACTOR` constant.

- [ ] **Step 1: Add the types**

Append to `frontend/src/types/index.ts` (match the file's `interface`/union style + JSDoc-cites-backend convention):

```ts
// --- Knowledge Base (policy governance) — backend app/api/v1/policies.py ---
export type PolicyVisibility = "public" | "internal";
export type PolicyStatus = "active" | "inactive";

/** Mirrors policy_documents (backend/app/db/models.py PolicyDocument). */
export interface PolicyDocument {
  policy_key: string;
  title: string;
  content: string;
  category: string | null;
  tags: string[];
  visibility: PolicyVisibility;
  status: PolicyStatus;
  source: string | null;
  updated_at: string | null;
}

/** One policy_audit_logs row (backend PolicyAuditLog). */
export interface PolicyAuditEntry {
  id: number;
  policy_key: string;
  action: string; // policy_created | policy_retired | policy_reactivated
  actor: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  timestamp: string | null;
}

/** A related policy surfaced by POST /policies/similar. */
export interface SimilarPolicy {
  policy_key: string;
  title: string;
  score: number;
}

export interface PolicyListParams {
  visibility?: PolicyVisibility;
  status?: PolicyStatus;
  search?: string;
}

/** POST /api/v1/policies request body. */
export interface CreatePolicyRequest {
  title: string;
  content: string;
  category?: string | null;
  tags?: string[];
  actor: string;
  retire_keys?: string[];
}

export interface PoliciesResponse { policies: PolicyDocument[]; }
export interface PolicyAuditResponse { entries: PolicyAuditEntry[]; }
export interface SimilarResponse { similar: SimilarPolicy[]; }
```

- [ ] **Step 2: Add the api client** (`frontend/src/lib/api/knowledgeBase.ts`)

Mirror `frontend/src/lib/api/emails.ts` (import `apiClient from "./client"`, exported async fns, destructure `{ data }`).

```ts
import apiClient from "./client";

import type {
  CreatePolicyRequest, PoliciesResponse, PolicyAuditResponse,
  PolicyListParams, SimilarResponse,
} from "@/types";

/** GET /policies — filtered KB browse. */
export async function listPolicies(params?: PolicyListParams): Promise<PoliciesResponse> {
  const { data } = await apiClient.get<PoliciesResponse>("/policies", { params });
  return data;
}

/** POST /policies — create an internal policy (optionally retiring superseded keys). */
export async function createPolicy(body: CreatePolicyRequest): Promise<{ policy_key: string; visibility: string; status: string }> {
  const { data } = await apiClient.post("/policies", body);
  return data;
}

/** PATCH /policies/{key}/retire. */
export async function retirePolicy(key: string, actor: string): Promise<{ policy_key: string; status: string }> {
  const { data } = await apiClient.patch(`/policies/${encodeURIComponent(key)}/retire`, { actor });
  return data;
}

/** PATCH /policies/{key}/reactivate. */
export async function reactivatePolicy(key: string, actor: string): Promise<{ policy_key: string; status: string }> {
  const { data } = await apiClient.patch(`/policies/${encodeURIComponent(key)}/reactivate`, { actor });
  return data;
}

/** POST /policies/similar — related existing policies for the override assist. */
export async function findSimilarPolicies(body: { title: string; content: string }): Promise<SimilarResponse> {
  const { data } = await apiClient.post<SimilarResponse>("/policies/similar", body);
  return data;
}

/** GET /policies/audit — governance history, newest first. */
export async function listPolicyAudit(params?: { limit?: number; offset?: number }): Promise<PolicyAuditResponse> {
  const { data } = await apiClient.get<PolicyAuditResponse>("/policies/audit", { params });
  return data;
}
```

Then add to `frontend/src/lib/api/index.ts`: `export * from "./knowledgeBase";`

- [ ] **Step 3: Add the hooks** (`frontend/src/hooks/useKnowledgeBase.ts`)

Mirror `frontend/src/hooks/useEmailActions.ts` (invalidate helper + `useMutation`) and `useEmailQueue.ts` (flattened `useQuery`).

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createPolicy, findSimilarPolicies, listPolicies, listPolicyAudit,
  reactivatePolicy, retirePolicy,
} from "@/lib/api";
import type { CreatePolicyRequest, PolicyListParams } from "@/types";

/** Placeholder chair identity until the account system lands. */
export const ACTOR = "Chair1";

function useInvalidateKb() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["knowledgeBase"] });
    queryClient.invalidateQueries({ queryKey: ["policyAudit"] });
  };
}

export function usePolicies(params?: PolicyListParams) {
  const query = useQuery({
    queryKey: ["knowledgeBase", params],
    queryFn: () => listPolicies(params),
  });
  return {
    policies: query.data?.policies ?? [],
    isLoading: query.isLoading, isError: query.isError, refetch: query.refetch,
  };
}

export function usePolicyAudit() {
  const query = useQuery({ queryKey: ["policyAudit"], queryFn: () => listPolicyAudit() });
  return {
    entries: query.data?.entries ?? [],
    isLoading: query.isLoading, isError: query.isError, refetch: query.refetch,
  };
}

export function useCreatePolicy() {
  const invalidate = useInvalidateKb();
  return useMutation({
    mutationFn: (data: CreatePolicyRequest) => createPolicy(data),
    onSuccess: invalidate,
  });
}

export function useRetirePolicy() {
  const invalidate = useInvalidateKb();
  return useMutation({
    mutationFn: (key: string) => retirePolicy(key, ACTOR),
    onSuccess: invalidate,
  });
}

export function useReactivatePolicy() {
  const invalidate = useInvalidateKb();
  return useMutation({
    mutationFn: (key: string) => reactivatePolicy(key, ACTOR),
    onSuccess: invalidate,
  });
}

export function useFindSimilar() {
  return useMutation({
    mutationFn: (body: { title: string; content: string }) => findSimilarPolicies(body),
  });
}
```

Then add to `frontend/src/hooks/index.ts`: `export * from "./useKnowledgeBase";`

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/lib/api/knowledgeBase.ts frontend/src/lib/api/index.ts frontend/src/hooks/useKnowledgeBase.ts frontend/src/hooks/index.ts
git commit -m "feat(kb-ui): types, api client, and React Query hooks for the KB page"
```

---

### Task 4: KB page shell + nav + Policies view (list, filters, retire/reactivate)

**Files:**
- Create: `frontend/src/app/knowledge-base/layout.tsx`, `frontend/src/app/knowledge-base/page.tsx`
- Create: `frontend/src/components/kb/PolicyFilters.tsx`, `frontend/src/components/kb/PolicyList.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx` (add NAV_ITEMS entry + icon import)

**Interfaces:**
- Consumes: `usePolicies`, `useRetirePolicy`, `useReactivatePolicy` (Task 3); `Badge`, `EmptyState`, `ErrorBanner`, `LoadingSpinner`, `Button` from `@/components/ui`.
- Produces: the `/knowledge-base` route with a working Policies view (History view + Add panel come in Tasks 5-6 — leave a placeholder for the segmented toggle's History option and the `[+ Add internal policy]` button that Tasks 5/6 wire up).

- [ ] **Step 1: Add the nav entry**

In `frontend/src/components/layout/Sidebar.tsx`: import a `Library` icon from `lucide-react` (add to the existing lucide import) and add to `NAV_ITEMS`:
```ts
  { label: "Knowledge Base", href: "/knowledge-base", icon: Library },
```
(place it after "Email Queue").

- [ ] **Step 2: Create the layout**

`frontend/src/app/knowledge-base/layout.tsx` (mirror `src/app/audit/layout.tsx`):
```tsx
import type { Metadata, ReactNode } from "next";
export const metadata: Metadata = { title: "Knowledge Base" };
export default function KnowledgeBaseLayout({ children }: { children: ReactNode }) {
  return children;
}
```
(If `audit/layout.tsx` imports `ReactNode` from `"react"` not `"next"`, match that file's exact imports.)

- [ ] **Step 3: Create `PolicyFilters.tsx`**

Mirror `frontend/src/components/email/EmailFilters.tsx` (250ms-debounced search; segmented toggle groups). Props:
```tsx
interface PolicyFiltersProps {
  search: string; onSearchChange: (v: string) => void;
  visibility: "all" | "public" | "internal"; onVisibilityChange: (v: "all" | "public" | "internal") => void;
  status: "active" | "inactive" | "all"; onStatusChange: (v: "active" | "inactive" | "all") => void;
}
```
Render a search `<input>` (styled via CSS-var tokens like EmailFilters), a Visibility toggle group (All / Public / Internal), and a Status toggle group (Active / Inactive / All). Reuse the exact input/toggle styling approach from EmailFilters (read it and match).

- [ ] **Step 4: Create `PolicyList.tsx`**

Props: `{ policies: PolicyDocument[]; onRetire: (key: string) => void; onReactivate: (key: string) => void; pendingKey: string | null; }`. Render each policy as a bordered row (mirror the row styling in the audit or queue list):
- Line 1: `policy_key` (muted mono) + `<Badge variant={p.visibility === "internal" ? "warning" : "neutral"}>{p.visibility}</Badge>` + `<Badge variant={p.status === "active" ? "success" : "neutral"}>{p.status}</Badge>`; right-aligned action button.
- Line 2: `title` (text-primary). Line 3: `content` truncated (`line-clamp`/max length) in text-secondary.
- Action: if `p.status === "active"` → `<Button variant="secondary" onClick={() => onRetire(p.policy_key)}>Retire</Button>`; else → `<Button onClick={() => onReactivate(p.policy_key)}>Reactivate</Button>`. Disable while `pendingKey === p.policy_key`.
- Inactive rows: reduce opacity (e.g. `style={{ opacity: 0.6 }}`).

Use the shared `Button` from `@/components/ui` (the cva one). Never hardcode hex — use token vars.

- [ ] **Step 5: Create the page**

`frontend/src/app/knowledge-base/page.tsx` (`"use client"`). State: `view: "policies" | "history"` (segmented toggle at top — History renders a placeholder `<div>Coming in the History task</div>` for now, wired in Task 6), search/visibility/status filter state (debounce handled inside PolicyFilters or here), an `addOpen` boolean for the `[+ Add internal policy]` button (renders a placeholder panel for now, wired in Task 5). Build the params object (`useMemo`) mapping "all" → `undefined`, and call `usePolicies(params)`. Wire `useRetirePolicy`/`useReactivatePolicy`, track `pendingKey` from the mutations' `variables`/`isPending`. Render: header + segmented view toggle + (Policies view:) `[+ Add internal policy]` button, `PolicyFilters`, then `LoadingSpinner` / `ErrorBanner` (with `refetch`) / `EmptyState` / `PolicyList`. Layout: single-column `mx-auto w-full max-w-4xl px-8 py-10` (mirror audit page).

- [ ] **Step 6: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/knowledge-base frontend/src/components/kb/PolicyFilters.tsx frontend/src/components/kb/PolicyList.tsx frontend/src/components/layout/Sidebar.tsx
git commit -m "feat(kb-ui): Knowledge Base page shell, nav entry, and Policies view (list + retire/reactivate)"
```

---

### Task 5: Add-internal panel (similar-assist + retire_keys + create)

**Files:**
- Create: `frontend/src/components/kb/AddPolicyPanel.tsx`
- Modify: `frontend/src/app/knowledge-base/page.tsx` (mount the panel behind the `[+ Add internal policy]` toggle)

**Interfaces:**
- Consumes: `useCreatePolicy`, `useFindSimilar`, `ACTOR` (Task 3); `Button`, `ErrorBanner` from `@/components/ui`.
- Produces: `AddPolicyPanel` — collapsible in-place form (mirror `components/dashboard/IngestPanel.tsx`'s open/close + inline result pattern). Props: `{ onClose: () => void; onCreated: () => void; }`.

- [ ] **Step 1: Build the panel**

Mirror `IngestPanel.tsx` (bordered `rounded-xl` panel, `X` close button, `Field({label,children})` helper, shared field style const, `<form onSubmit>` with `e.preventDefault()`).
Fields (controlled `useState`): `title`, `content` (textarea), `category` (optional), `tagsText` (optional; split on commas → `tags`).
- **"Check for related policies"** button (type="button"): calls `useFindSimilar().mutate({ title, content })`; on success render the returned `similar` list, each row = title + `policy_key` (muted) + score + a checkbox "supersede (retire this)". Track checked keys in a `Set<string>` state → `retireKeys`. Disable the button when title+content are empty; show `LoadingSpinner` while `isPending`.
- **Create** (submit): `useCreatePolicy().mutate({ title, content, category: category || null, tags, actor: ACTOR, retire_keys: [...retireKeys] })`. On success: call `onCreated()` (parent refetches) and `onClose()`. On error: inline `ErrorBanner` with the `ApiError.detail`.
- Never hardcode hex; use token vars / existing field styles.

- [ ] **Step 2: Mount it in the page**

In `page.tsx`, when `addOpen` is true render `<AddPolicyPanel onClose={() => setAddOpen(false)} onCreated={() => refetch()} />` in place of the placeholder. The `[+ Add internal policy]` button toggles `addOpen`.

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/kb/AddPolicyPanel.tsx frontend/src/app/knowledge-base/page.tsx
git commit -m "feat(kb-ui): add-internal-policy panel with similarity-assist override flow"
```

---

### Task 6: History view + revert

**Files:**
- Create: `frontend/src/components/kb/PolicyHistory.tsx`
- Modify: `frontend/src/app/knowledge-base/page.tsx` (render `PolicyHistory` when `view === "history"`)

**Interfaces:**
- Consumes: `usePolicyAudit`, `usePolicies` (to know current status per key), `useRetirePolicy`, `useReactivatePolicy` (Task 3); `Badge`, `EmptyState`, `ErrorBanner`, `LoadingSpinner`, `Button`.
- Produces: `PolicyHistory` — newest-first audit log with a Revert action on each policy's latest entry.

- [ ] **Step 1: Build the history component**

`PolicyHistory.tsx`: call `usePolicyAudit()` and `usePolicies()` (no filter → all, to read current status per key; build a `Map<policy_key, status>`). Render each `PolicyAuditEntry` newest-first:
- timestamp (localized), `<Badge>` for `action` (`policy_created`→success, `policy_retired`→danger, `policy_reactivated`→review/warning), `policy_key` (mono), `actor`, and a compact `before?.status → after?.status` summary when present.
- **Revert**: compute the set of "latest entry id per policy_key" = the first occurrence of each `policy_key` when iterating the newest-first list. Only those entries get a Revert button. Clicking sets a local `confirmingId`; a second inline "Confirm revert" click fires: look up current status from the map — if `active` → `useRetirePolicy().mutate(key)`; if `inactive` → `useReactivatePolicy().mutate(key)`. On success both queries invalidate (handled by the hooks) so the list updates and a new entry appears.
- `LoadingSpinner` / `ErrorBanner` / `EmptyState` as usual.

- [ ] **Step 2: Wire into the page**

In `page.tsx`, when `view === "history"` render `<PolicyHistory />` instead of the placeholder.

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/kb/PolicyHistory.tsx frontend/src/app/knowledge-base/page.tsx
git commit -m "feat(kb-ui): policy audit History view with revert"
```

---

### Task 7: End-to-end verification (run + drive the page)

- [ ] **Step 1: Typecheck + backend focused tests**

Run: `cd frontend && npx tsc --noEmit` (clean) and `cd backend && export PATH=/u/jpang1/miniconda3/envs/autoexp/bin:$PATH && python -m pytest tests/test_policies_endpoint.py tests/test_policy_kb_layers.py tests/test_policy_audit.py -q` (all pass).

- [ ] **Step 2: Launch the app against a scratch DB and drive the page**

Use the `run` skill / the established launch pattern. Backend on a throwaway DB so the dev DB is untouched:
```bash
cd backend && export PATH=/u/jpang1/miniconda3/envs/autoexp/bin:$PATH HF_HUB_OFFLINE=1
TMPDB=/tmp/confmail_ui_verify.db
export DATABASE_URL="sqlite+aiosqlite:///${TMPDB}" RETRIEVAL_BACKEND=bm25 QUERY_STRATEGY=prefix MODEL_PROVIDER=fallback
alembic upgrade head && python scripts/seed_real_policies.py
nohup python -m uvicorn main:app --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 5 > /tmp/kb_ui_backend.log 2>&1 &
cd ../frontend && nohup npm run dev > /tmp/kb_ui_frontend.log 2>&1 &
```
Then drive http://localhost:3000/knowledge-base (chromium-cli or the project's browser-driving pattern):
- Policies view lists the 93 seeded public policies; filters work (Internal → empty; search "deadline" narrows).
- Add internal policy "Deadline extended" → "Check for related" surfaces deadline policies → check one to supersede → Create → new internal row appears; the superseded one flips to inactive.
- Retire an active row / Reactivate an inactive row.
- History view lists created/retired/reactivated entries; Revert on the latest entry for a policy flips it back and adds a new entry.
Capture a screenshot of the Policies view and the History view; **look at them** (blank = failure).

- [ ] **Step 3: Teardown + commit any fixes**

Kill both servers (user-scoped, filter by comm=python/node), `rm $TMPDB`. If driving surfaced fixes, commit them:
```bash
git add -A && git commit -m "fix(kb-ui): <what the e2e drive surfaced>"
```

---

## Self-Review

- **Spec coverage:** §3.1 repo→T1; §3.2 audit repo→T1; §3.3 endpoints→T2; §4.1 types→T3; §4.2 client→T3; §4.3 hooks→T3; §4.4 page/nav/Policies→T4, Add panel→T5, History+revert→T6; §5 verification→T7. Reactivate transition→T1/T2; revert UX→T4 (rows) + T6 (history).
- **Type consistency:** `PolicyDocument`/`PolicyAuditEntry`/`SimilarPolicy`/`CreatePolicyRequest` defined T3, consumed T4-T6; hook names (`usePolicies`,`usePolicyAudit`,`useCreatePolicy`,`useRetirePolicy`,`useReactivatePolicy`,`useFindSimilar`) consistent across tasks; `ACTOR` from T3 used in T5/T6 via the hooks.
- **Placeholders:** backend steps carry full code; frontend steps carry KB-specific code + explicit "mirror <existing file>" for boilerplate styling (legitimate: matching existing components, not another task).
- **No FE unit harness:** frontend tasks gate on `tsc --noEmit`; behavioral verification is the T7 drive-the-app.
- **Out of scope (unchanged):** content editing in UI, real auth, non-latest-entry revert, rich pagination.
