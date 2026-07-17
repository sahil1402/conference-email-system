# Zendesk REST API — Engineering Reference

Condensed reference for integrating the conference email pipeline with the AAAI-27 Zendesk
instance (`workflowchairs@aaai.zendesk.com` → subdomain `aaai`). Verified against the official
developer docs (July 2026). Items that could not be confirmed on a docs page are marked
**(unverified)**.

---

## 1. Overview, base URL, auth

Docs: [API introduction](https://developer.zendesk.com/api-reference/ticketing/introduction/) ·
[Security & auth](https://developer.zendesk.com/api-reference/introduction/security-and-auth/)

- Base URL: `https://{subdomain}.zendesk.com/api/v2/` (for us: `https://aaai.zendesk.com/api/v2/`).
- All requests/responses are JSON (`Content-Type: application/json`). `.json` suffix on paths is
  optional.

### API token auth (recommended for a server-side integration)

Basic auth with username `{email}/token` and the API token as password:

```
curl https://aaai.zendesk.com/api/v2/tickets.json \
  -u 'chair@example.org/token:{api_token}'
```

Equivalently, header form: `Authorization: Basic base64("{email}/token:{api_token}")`.

- Admin creates tokens in **Admin Center → Apps and integrations → APIs → Zendesk API**.
- Tokens impersonate the account of the email used — API writes will appear as that user.
  Use a dedicated agent account so drafts are attributable to the bot, not a chair.
- Max 256 active tokens per account; delete unused ones.

### OAuth (alternative)

`Authorization: Bearer {access_token}`. Needed only if we ever ship a multi-tenant integration;
for a single account, an API token is simpler.

### httpx pattern

```python
client = httpx.Client(
    base_url="https://aaai.zendesk.com/api/v2",
    auth=(f"{EMAIL}/token", API_TOKEN),
    timeout=30.0,
)
```

Ticket creation (`POST`) supports an `Idempotency-Key: {unique_key}` header (keys expire after
2 hours; response header `x-idempotency-lookup: hit|miss`). We mostly update, not create.

---

## 2. Reading tickets — polling strategy

Docs: [Tickets](https://developer.zendesk.com/api-reference/ticketing/tickets/tickets/) ·
[Incremental Exports](https://developer.zendesk.com/api-reference/ticketing/ticket-management/incremental_exports/) ·
[Search](https://developer.zendesk.com/api-reference/ticketing/ticket-management/search/) ·
[Pagination](https://developer.zendesk.com/api-reference/introduction/pagination/)

| Option | Endpoint | Good for | Limits / caveats |
|---|---|---|---|
| List Tickets | `GET /api/v2/tickets` | Small accounts, ad-hoc listing | No "changed since" filter beyond `start_time`; sort by `updated_at`/`id`/`status`; 100/page |
| Search | `GET /api/v2/search?query=` | Ad-hoc filtered queries | **Hard cap 1,000 results** (422 past page 10); results can lag indexing |
| Search Export | `GET /api/v2/search/export?query=&filter[type]=ticket` | One-off large filtered pulls | Cursor pagination, no 1,000 cap; 100 req/min; `filter[type]` required |
| **Incremental cursor export** | `GET /api/v2/incremental/tickets/cursor?start_time=` | **Ongoing polling for new/updated tickets** | 10 req/min; no data for the most recent minute; returns *updated* as well as new tickets |

**Recommendation: incremental cursor-based export for the polling loop.** It is the purpose-built
"give me everything created/updated since my last position" API, survives restarts via a stored
cursor, and never misses tickets between polls.

### Incremental cursor export loop

First call uses `start_time` (Unix epoch, must be ≥ 1 minute in the past); every later call uses
the stored cursor:

```
GET /api/v2/incremental/tickets/cursor.json?start_time=1752624000
GET /api/v2/incremental/tickets/cursor.json?cursor={after_cursor}
```

Response shape:

```json
{
  "tickets": [ { "id": 1234, "status": "new", "updated_at": "...", ... } ],
  "after_cursor": "MTU4MDc2OTgwMS4wfHw0Njd8",
  "end_of_stream": true
}
```

- Page until `end_of_stream: true`, persist `after_cursor` in our DB, sleep, repeat.
- `per_page` up to 1,000 (default 1,000).
- Comparison is against `generated_timestamp` (any change, incl. system), not `updated_at`.
- Same ticket can appear again on every update → **upsert by `ticket.id`** (dedup key), compare
  `updated_at` to skip no-op reprocessing.
- Deleted tickets still appear with `status: "deleted"` — filter them out.
- `exclude_deleted=true` is supported.
- Side-loading via `include=` works here (e.g. `include=users`), **except `last_audits`**, which
  is explicitly unsupported on incremental endpoints. Ticket **comments cannot be side-loaded**
  on any tickets endpoint — see §3.
- Best-practices guide: [Using the Incremental Exports API](https://developer.zendesk.com/documentation/api-basics/working-with-data/using-the-incremental-export-api/).

### Pagination (general)

- **Cursor pagination (use this):** `page[size]` (≤100 on most endpoints), `page[after]`;
  response has `meta.has_more`, `meta.after_cursor`, `links.next`. No depth limit.
- **Offset pagination (avoid):** `page`/`per_page`; hard-limited to 100 pages / 10,000 records
  (400 error beyond) since Aug 2023.
- Incremental export uses its own `after_cursor`/`end_of_stream` variant, as shown above.

### Ticket object — fields we care about

```json
{
  "id": 1234,
  "external_id": null,
  "subject": "Question about supplementary material deadline",
  "description": "(read-only: body of the first comment)",
  "status": "new",
  "requester_id": 20978392,
  "assignee_id": null,
  "tags": ["author_inquiry"],
  "custom_fields": [{"id": 360001, "value": "..."}],
  "via": {"channel": "email"},
  "created_at": "2026-07-15T09:00:00Z",
  "updated_at": "2026-07-15T09:00:00Z"
}
```

`external_id` is a free-form string for linking to local records (not enforced unique;
filterable via `GET /api/v2/tickets?external_id=` and search).

### Status lifecycle (matters for our approve/send flow)

`new` → never touched by an agent · `open` → assigned/being worked · `pending` → **waiting on
the requester** (use after we ask the author a clarifying question) · `hold` → internal wait ·
`solved` → resolved (requester reply reopens it to `open`) · `closed` → archived and
**immutable — no comments, no tag changes, no updates at all**. Solved tickets are moved to
closed by an automation (default setup ~4 days after solved; system-enforced no later than
28 days **(exact day counts unverified — account-configurable)**). Practical rules:

- Post internal note → leave status unchanged (or set `open` so it shows in agent queues).
- Auto-send FAQ reply → set `status: "solved"` in the same update; a follow-up from the author
  reopens it.
- Never try to write to `closed` tickets; if we must annotate one, it's a new follow-up ticket.
- Accounts with custom statuses expose `custom_status_id`; plain `status` is then the category.

---

## 3. Ticket comments (threads)

Docs: [Ticket Comments](https://developer.zendesk.com/api-reference/ticketing/tickets/ticket_comments/)

```
GET /api/v2/tickets/{ticket_id}/comments.json?include=users&sort=created_at&page[size]=100
```

Comment object:

```json
{
  "id": 9873843,
  "type": "Comment",
  "public": true,
  "author_id": 20978392,
  "body": "plain/markdown text",
  "html_body": "<p>rendered HTML</p>",
  "plain_body": "sanitized plain text (read-only)",
  "via": {"channel": "email"},
  "created_at": "2026-07-15T09:00:00Z",
  "attachments": []
}
```

- `public: true` = visible to requester (a real reply); `public: false` = internal note.
  This flag is how we separate "what the chair actually sent" from internal chatter in the
  historical pull.
- `plain_body` is the safest field to feed the classifier/drafter; `html_body` preserves
  formatting.
- Default sort is `created_at` ascending — first comment = the original inquiry.
- `?include=users` side-loads authors so we can tell agent vs end-user comments (join
  `author_id` → sideloaded `users[].role`).
- Comments are **not** side-loadable on ticket list/show endpoints (only `comment_count`), so
  fetching threads is one request per ticket. Bulk alternative: incremental ticket *events*
  export with `include=comment_events` (`GET /api/v2/incremental/ticket_events?start_time=...`)
  embeds comments in `child_events` — useful for large backfills, overkill for ~100 tickets.
- There is no comment-create endpoint; comments are added via ticket update (§4). Max 5,000
  comments per ticket. `PUT /api/v2/tickets/{ticket_id}/comments/{comment_id}/make_private`
  can demote an accidentally-public comment (one-way).

---

## 4. Writing back: notes, replies, status, tags

Docs: [Update Ticket](https://developer.zendesk.com/api-reference/ticketing/tickets/tickets/#update-ticket) ·
[Tags](https://developer.zendesk.com/api-reference/ticketing/ticket-management/tags/) ·
[Creating & updating tickets guide](https://developer.zendesk.com/documentation/ticketing/managing-tickets/creating-and-updating-tickets/)

### Internal note (launch mode)

```
PUT /api/v2/tickets/{id}.json
```
```json
{
  "ticket": {
    "comment": {
      "html_body": "<p><b>AI draft</b> — review before sending:</p><p>Dear author, ...</p>",
      "public": false
    }
  }
}
```

### Public reply (auto-send mode, FAQ lane)

```json
{
  "ticket": {
    "comment": { "html_body": "<p>Dear author, ...</p>", "public": true },
    "status": "solved"
  }
}
```

- `comment.author_id` may be set to attribute the comment to a specific agent; otherwise it's
  the authenticated user. Body: `body` (plain/markdown) or `html_body` (preferred in Agent
  Workspace).
- A public comment on a ticket whose requester came in via email triggers the normal outbound
  email notification (subject to the account's triggers) **(trigger-dependent — verify in the
  AAAI instance that the default "Notify requester of comment update" trigger is active)**.

### Tags — the race problem

`ticket.tags` in an update is **set semantics: it overwrites the whole array**. Two writers
(our bot + a chair in the UI) can clobber each other. Prefer the dedicated tag endpoints:

```
PUT    /api/v2/tickets/{ticket_id}/tags.json    # ADD tags (merge, no overwrite)
POST   /api/v2/tickets/{ticket_id}/tags.json    # SET tags (replace)
DELETE /api/v2/tickets/{ticket_id}/tags.json    # REMOVE listed tags
```
```json
{ "tags": ["ai_drafted"], "updated_stamp": "2026-07-15T09:00:00Z", "safe_update": "true" }
```

With `safe_update` + `updated_stamp` (the ticket's current `updated_at`), the write fails with
**409 Conflict** if the ticket changed since we read it — re-fetch and retry. Tag endpoints do
not work on `closed` tickets. When adding tags inside `update_many`, use `additional_tags`
(adds) / `remove_tags` instead of `tags`.

Suggested state tags: `ai_drafted`, `ai_auto_replied`, `ai_skipped` (+ optional
`ai_lane_faq` / `ai_lane_human_review`). Tags are lowercase, no spaces (use underscores).

### Batch updates + Job Statuses

Docs: [Job Statuses](https://developer.zendesk.com/api-reference/ticketing/ticket-management/job_statuses/)

`PUT /api/v2/tickets/update_many.json?ids=1,2,3` (shared change) or with a `tickets` array
(per-ticket changes). Returns `202` with a `job_status`; poll
`GET /api/v2/job_statuses/{id}.json` until `status` ∈ `completed|failed|killed`
(`queued`/`working` meanwhile; `progress`/`total`/`results` fields). Job data expires after
1 day; max ~30 queued/running jobs. Only needed if we ever tag/close in bulk.

### Detecting human activity (audits)

Docs: [Ticket Audits](https://developer.zendesk.com/api-reference/ticketing/tickets/ticket_audits/)

`GET /api/v2/tickets/{ticket_id}/audits.json` — read-only history; each audit = one update with
an `events` array (`Comment`, `Change`, `Create`, `Notification`) plus `author_id`/`via`.
Useful to detect "a chair already replied after our draft" (Comment event with agent author
and `public: true` newer than our note) before auto-acting. For polling-scale change detection
the incremental export's `updated_at` bump + a comments re-fetch is usually enough.

---

## 5. Historical pull recipe (~100 tickets for style guide + eval set)

One-off script; stays well inside rate limits.

1. **Enumerate solved/closed tickets** (Search Export avoids the 1,000-result cap and gives
   cursor pagination):

   ```
   GET /api/v2/search/export.json?query=type:ticket status>=solved created>2025-09-01&filter[type]=ticket&page[size]=100
   ```

   Or plain search (fine under 1,000 results):
   `GET /api/v2/search.json?query=type:ticket status:solved&sort_by=created_at&sort_order=desc`.

2. **For each ticket id, pull the full thread** (1 request/ticket):

   ```
   GET /api/v2/tickets/{id}/comments.json?include=users&page[size]=100
   ```

3. **Reconstruct Q/A pairs:** first comment (or first `public` end-user comment) = question;
   subsequent `public: true` comments whose author has `role` `agent`/`admin` = what the chair
   actually sent. `public: false` notes reveal internal reasoning — keep separately, never in
   the style corpus destined for outbound text.

4. Persist raw JSON (ticket + comments + sideloaded users) so the corpus can be re-derived.

~101–110 requests total; with a 100 ms inter-request sleep this finishes in <1 min and cannot
hit any documented limit.

```python
for t in ticket_ids:
    r = client.get(f"/tickets/{t}/comments.json", params={"include": "users"})
    r.raise_for_status()
    save(t, r.json())          # keys: comments, users
    time.sleep(0.1)
```

---

## 6. Users (identifying the requester)

Docs: [Users](https://developer.zendesk.com/api-reference/ticketing/users/users/)

- `ticket.requester_id` → the author. Resolve via:
  - Side-load on any tickets/incremental/comments call: `?include=users` (matches
    `requester_id`, `assignee_id`, comment `author_id`).
  - `GET /api/v2/users/{id}.json` or bulk `GET /api/v2/users/show_many.json?ids=1,2,3`.
- Key fields: `id`, `name`, `email`, `role` (`end-user` | `agent` | `admin`),
  `organization_id`. Use `name`/`email` to address the reply ("Dear Dr. …").
- Role is how we distinguish chair replies from author messages in threads (§5).

---

## 7. Rate limits & error handling

Docs: [Rate limits](https://developer.zendesk.com/api-reference/introduction/rate-limits/)

Account-wide requests/minute by plan: Team 200 · Growth 400 · Professional 400 ·
Enterprise 700 · High Volume add-on 2,500. (Check which plan AAAI's instance has.)

Per-endpoint limits that affect us:

| Endpoint | Limit |
|---|---|
| Incremental exports | **10 req/min** |
| Update Ticket | 30 updates / 10 min / user / ticket; 100 req/min account-wide (300 w/ High Volume) |
| Search Export | 100 req/min |
| Search | dedicated budget, surfaced via `Zendesk-RateLimit-search-index` header |

Handling:

- On **429**, read the **`Retry-After`** header (seconds) and sleep at least that long; add
  exponential backoff + jitter for repeats.
- Monitor `X-Rate-Limit` / `X-Rate-Limit-Remaining` response headers and the newer
  `Zendesk-RateLimit-*` per-endpoint headers; back off proactively below ~20% remaining.
- On **409** (safe_update conflict): re-read ticket, re-apply, retry.
- On **422** from search page >10: switch to Search Export.
- Poll incremental export well under its 10 req/min ceiling; for our volume a 2–5 min interval
  is plenty. In httpx: on 429, `time.sleep(int(resp.headers.get("Retry-After", "60")))`, retry.

---

## 8. Search API specifics

Docs: [Search](https://developer.zendesk.com/api-reference/ticketing/ticket-management/search/) ·
[Query syntax guide](https://developer.zendesk.com/documentation/ticketing/using-the-zendesk-api/searching-with-the-zendesk-api/)

- `GET /api/v2/search.json?query={urlencoded}` · count: `/search/count` · export:
  `/search/export` (cursor, `filter[type]=ticket` required).
- Syntax (URL-encode the whole query): `type:ticket`, `status:open`, ranges
  `status<solved`, dates `created>2026-01-01` / `updated>=...` (YYYY-MM-DD), `tags:ai_drafted`,
  negation `-tags:ai_drafted`, exact phrase `"camera ready"`, `requester:someone@example.com`
  **(requester keyword: unverified against current docs page — confirm before relying on it)**.
- Standard search caps at **1,000 results / 100 per page** (422 beyond); Export has no cap but
  recommends `page[size]=100` when archived tickets are in scope.
- Search sideloads use `include=tickets(users)` syntax.
- Handy operational queries:
  - Drafted but not yet answered: `type:ticket tags:ai_drafted status<solved`
  - Backfill candidates: `type:ticket status>=solved created>2025-09-01`

---

## 9. Webhooks & triggers (later phase)

Docs: [Webhooks API](https://developer.zendesk.com/api-reference/webhooks/webhooks-api/webhooks/) ·
[Verifying webhooks](https://developer.zendesk.com/documentation/webhooks/verifying/) ·
[Triggers](https://developer.zendesk.com/api-reference/ticketing/business-rules/triggers/)

When we have a public callback URL: create a webhook
(`POST /api/v2/webhooks` with `endpoint`, `http_method: "POST"`, `request_format: "json"`,
`subscriptions: ["conditional_ticket_events"]`), then create a trigger
(`POST /api/v2/triggers`) whose condition is "ticket is created" and whose action is
`notification_webhook` with a JSON body of placeholders (e.g. `{{ticket.id}}`). Verify each
delivery: signature = `base64(HMAC-SHA256(timestamp + raw_body, signing_secret))`, compared
against headers `X-Zendesk-Webhook-Signature` + `X-Zendesk-Webhook-Signature-Timestamp`;
fetch the secret via `GET /api/v2/webhooks/{id}/signing_secret`. Keep the incremental-export
poller as reconciliation even after webhooks ship (webhook deliveries can be missed).

---

## 10. Idempotency & dedup

- **Zendesk `ticket.id` is the canonical dedup key.** Store it (plus `updated_at` seen) in our
  DB; upsert on every poll. Incremental export re-delivers a ticket on every update — this is
  a feature (we see status/comment changes), not a bug.
- Only enqueue drafting work when the ticket is new to us **or** has a new end-user comment
  since our last processed comment id (track `last_processed_comment_id` per ticket).
- Guard writes with our state tags (§4): before posting a draft, skip if `ai_drafted` already
  present (covers crash-between-write-and-DB-commit).
- `external_id` on the ticket can mirror our internal email/case id if we ever create tickets
  from our side; for the read path it is unnecessary.
- Use `safe_update`/`updated_stamp` on tag writes; use `Idempotency-Key` on any ticket
  creation.

---

## 11. Recommended integration plan (mapped to our 5 needs)

| # | Need | Approach |
|---|---|---|
| 1 | Pull incoming tickets | Poller (every 2–5 min): `GET /incremental/tickets/cursor` with stored cursor; `include=users`; upsert by `ticket.id`; fetch `GET /tickets/{id}/comments` for tickets needing processing; feed `plain_body` of the newest end-user comment into the existing ingest pipeline |
| 2 | Write back drafts | Launch: `PUT /tickets/{id}` with `comment.public: false` (internal note). Auto-send FAQ lane: `comment.public: true` + `status: "solved"` in the same call |
| 3 | Historical corpus | §5 recipe: Search Export for solved/closed ids → per-ticket `GET /tickets/{id}/comments?include=users` → Q/A pairs from `public` flag + author `role` |
| 4 | State tracking | `PUT /tickets/{id}/tags` (add) with `safe_update`; tags `ai_drafted` / `ai_auto_replied`; mirror state in our DB as source of truth |
| 5 | Status semantics | Draft-only → don't touch status (or `open`); clarifying question to author → `pending`; auto-answered → `solved`; treat `closed` as read-only |

Suggested build order: (a) token + read-only poller into a shadow table → (b) historical pull
script → (c) internal-note writeback + tags → (d) approve/send flow driving `public`
comments/status → (e) webhooks as a latency optimization.

---

## 12. Doc link index

- Security & auth: <https://developer.zendesk.com/api-reference/introduction/security-and-auth/>
- Rate limits: <https://developer.zendesk.com/api-reference/introduction/rate-limits/>
- Tickets: <https://developer.zendesk.com/api-reference/ticketing/tickets/tickets/>
- Ticket Comments: <https://developer.zendesk.com/api-reference/ticketing/tickets/ticket_comments/>
- Ticket Audits: <https://developer.zendesk.com/api-reference/ticketing/tickets/ticket_audits/>
- Incremental Exports: <https://developer.zendesk.com/api-reference/ticketing/ticket-management/incremental_exports/>
- Incremental export guide: <https://developer.zendesk.com/documentation/api-basics/working-with-data/using-the-incremental-export-api/>
- Search: <https://developer.zendesk.com/api-reference/ticketing/ticket-management/search/>
- Search syntax: <https://developer.zendesk.com/documentation/ticketing/using-the-zendesk-api/searching-with-the-zendesk-api/>
- Tags: <https://developer.zendesk.com/api-reference/ticketing/ticket-management/tags/>
- Adding tags without overwriting: <https://developer.zendesk.com/documentation/ticketing/managing-tickets/adding-tags-to-tickets-without-overwriting-existing-tags/>
- Users: <https://developer.zendesk.com/api-reference/ticketing/users/users/>
- Job Statuses: <https://developer.zendesk.com/api-reference/ticketing/ticket-management/job_statuses/>
- Side-loading: <https://developer.zendesk.com/documentation/api-basics/working-with-data/side_loading/>
- Webhooks: <https://developer.zendesk.com/api-reference/webhooks/webhooks-api/webhooks/>
- Webhook verification: <https://developer.zendesk.com/documentation/webhooks/verifying/>
- Triggers: <https://developer.zendesk.com/api-reference/ticketing/business-rules/triggers/>
