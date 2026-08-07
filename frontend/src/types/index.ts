/**
 * Frontend type definitions for the Conference Email System API client layer.
 *
 * These mirror the shapes the backend ACTUALLY returns (verified against the
 * live code, not the placeholder spec):
 *   - Lane / status / intent values come from the pipeline modules + endpoints
 *     (backend/app/pipeline/{classifier,router,drafter,retriever}.py).
 *   - The persisted Email shape is backend/app/api/v1/emails.py::_email_to_dict.
 *   - AnalyticsSummary is backend/app/api/v1/analytics.py::analytics_summary.
 *
 * NOTE: `models/schemas.py` (EmailIntent=FAQ_DEADLINE, RoutingLane=AUTO_REPLY, …)
 * is NOT what the API serializes — the pipeline classes below are. Keep this file
 * in sync with the pipeline modules, not with schemas.py.
 */

// ---------------------------------------------------------------------------
// Enums / string unions
// ---------------------------------------------------------------------------

/** Routing lane values emitted by the router (router.py: LANE_FAQ / LANE_HUMAN_REVIEW). */
export type EmailLane = "faq" | "human_review";

/**
 * Lifecycle status as stored on the `emails.status` column (free-text String).
 *
 * UPPERCASE values are the EmailStatus enum the pipeline writes — in practice the
 * orchestrator only emits DRAFT_GENERATED ("complete") or ROUTED ("draft_failed");
 * the others are defined by the enum but not currently written.
 * lowercase values are written by the chair-action endpoints (approve → "approved",
 * reroute → "rerouted").
 */
export type EmailStatus =
  | "PENDING"
  | "CLASSIFIED"
  | "ROUTED"
  | "DRAFT_GENERATED"
  | "APPROVED"
  | "SENT"
  // The ticket was resolved WITHOUT a reply (chair marked it solved). Distinct
  // from SENT — nothing went to the requester.
  | "SOLVED"
  | "ARCHIVED"
  | "approved"
  | "rerouted";

/**
 * The 14 intent labels the classifier can emit (backend/app/pipeline/taxonomy.py:
 * VALID_INTENTS — the single source of truth; mirrored here for documentation
 * and typo-safety only, not re-derived). 5 families: review_workflow,
 * submission_compliance, appeals_integrity, committee, systems.
 */
export type IntentLabel =
  | "reviewer_assignment"
  | "review_submission_help"
  | "paper_bidding"
  | "author_profile_compliance"
  | "submission_upload_help"
  | "submission_requirements"
  | "submission_format_policy"
  | "author_list_change"
  | "review_decision_appeal"
  | "desk_reject_appeal"
  | "anonymity_violation"
  | "reviewer_workload_role"
  | "committee_invitation"
  | "cms_support";

// ---------------------------------------------------------------------------
// Pipeline result sub-objects (as stored in the emails JSON columns)
// ---------------------------------------------------------------------------

/** classifier.py::ClassificationResult */
export interface ClassificationResult {
  intent: IntentLabel;
  confidence: number;
  reasoning: string;
  secondary_intents: string[];
}

/** router.py::RoutingDecision */
export interface RoutingResult {
  lane: EmailLane;
  reason: string;
  confidence_used: number;
  threshold_applied: number;
  override_reason: string | null;
}

/** drafter.py::DraftResponse (+ Phase 5F chair-edit provenance). */
export interface DraftResult {
  draft_text: string;
  citations: string[];
  model_used: string;
  generation_metadata: Record<string, unknown>;
  /** Chair-facing caveats/suggestions — never part of the sendable reply (7F). */
  notes_for_chair?: string | null;
  /** Hints of the [CHAIR: ...] placeholders the drafter left in draft_text (7F). */
  placeholders?: string[];
  /** Set once a chair edits the draft (Phase 5F): the original AI/template text. */
  original_draft_text?: string;
  is_edited?: boolean;
  edited_by?: string;
  /**
   * Superseded drafts, oldest→newest, preserved when a follow-up re-ran the
   * pipeline (backend orchestrator `_append_draft_history`). Lets the review UI
   * show "Previous drafts". Absent/empty on drafts that were never superseded.
   */
  history?: DraftHistoryEntry[];
}

/** One superseded draft kept in DraftResult.history (backend draft.history[]). */
export interface DraftHistoryEntry {
  draft_text: string | null;
  notes_for_chair?: string | null;
  citations?: string[];
  answer_confidence?: number | null;
  is_edited?: boolean;
  /** ISO 8601 time the draft was superseded. */
  superseded_at?: string | null;
  /** Why it was superseded, e.g. "followup". */
  reason?: string | null;
  triggering_comment_ids?: (number | null)[];
}

/**
 * extractor.py::ExtractionResult — which submission an email is about and who
 * it names, stored in the `emails.extraction` JSON column.
 *
 * `method` says WHICH path produced these values, so the UI can distinguish the
 * model's answer from a weaker regex guess:
 *   "llm_distiller"  — the distiller read the email and reported these
 *   "regex_fallback" — the distiller did not run; matched off subject/body
 *   "none"           — nothing identifying was found to go on
 *
 * NOTE the distinction `Email.extraction === null` carries: null means the row
 * was never examined (it predates the feature), which is NOT the same as a
 * present result whose `submission_number` is null (examined, found nothing).
 * The backend preserves that difference deliberately — do not collapse them.
 */
export interface ExtractionData {
  submission_number: string | null;
  openreview_forum_id: string | null;
  /** Deduplicated, in first-seen order (the sender leads on the regex path). */
  authors: AuthorMention[];
  method: "llm_distiller" | "regex_fallback" | "none";
}

/**
 * extractor.py::AuthorMention — one person an email identifies, inside
 * ExtractionData.authors. Every field is independently optional: a mention with
 * only a name is still a real mention, so all three can be null at once.
 */
export interface AuthorMention {
  name: string | null;
  email: string | null;
  affiliation: string | null;
}

/** One turn in a ticket's conversation — GET /emails/{id}/thread. */
export interface EmailThreadMessage {
  comment_id: number | null;
  /** True = reply visible to the requester; False = internal note. */
  public: boolean;
  /** Zendesk user id of the comment author. */
  author_id?: number | null;
  /** "end-user" | "agent" | "admin" (Zendesk role); null if unresolved. NOTE:
   * unreliable for identifying the requester — chairs often have role "end-user"
   * in this account. Prefer `is_requester`. */
  author_role: string | null;
  /** True iff author_id === the ticket's requester_id (the reliable requester
   * signal); null for non-Zendesk emails (then fall back to author_role). */
  is_requester?: boolean | null;
  plain_body: string | null;
  /** Server-sanitized (backend bleach allowlist) comment HTML for rich
   * rendering; null → render plain_body instead. Safe to inject. */
  html_body: string | null;
  /** ISO 8601 datetime, or null. */
  created_at: string | null;
  via_channel: string | null;
}

/** GET /emails/{id}/thread response envelope. */
export interface EmailThreadResponse {
  messages: EmailThreadMessage[];
}

/**
 * `emails.retrieval_context` — the exact retriever inputs and the grounding set
 * they produced, captured at draft time (orchestrator `_compute`). Serialized by
 * `_email_to_dict` but previously undeclared here; the review UI reads
 * `forced_policy_key` from it to mark which citation the chair added.
 */
export interface RetrievalContext {
  query?: string;
  intent?: string;
  prior_intent?: string;
  /** Rank-ordered grounding set. A chair-forced policy is appended LAST. */
  retrieved_ids?: string[];
  chunk_hash?: string;
  /**
   * The policy the chair forced into this draft, if any (manual invoke). Null /
   * absent for a normal draft. Whether it actually applied is `Email.
   * forced_policy_applied` — this field is only what was requested.
   */
  forced_policy_key?: string | null;
}

/** retriever.py::RetrievedChunk (returned inside the ingest PipelineResult). */
export interface RetrievedChunk {
  policy_id: string;
  title: string;
  content: string;
  /**
   * Live retrieval score. Present on the ingest `PipelineResult` (computed in
   * the same request); ABSENT on `Email.retrieved_chunks`, which the API
   * rehydrates from the persisted `retrieval_context.retrieved_ids` — those
   * were never stored with per-chunk scores. Rank is list order.
   */
  score?: number;
  category: string;
  // [tags-dropped E007] tags: string[];
}

/**
 * Full policy chunk returned by GET /api/v1/policies/{key} (policies.py::
 * PolicyDetail). Read-only citation-detail lookup — the persisted email row does
 * not carry retrieved chunks, so the review UI resolves a cited id to this.
 */
export interface PolicyDetail {
  policy_key: string;
  title: string;
  content: string;
  category: string | null;
  // [tags-dropped E007] tags: string[];
  source: string | null;
  score: number | null;
}

/**
 * orchestrator.py::PipelineResult — the response body of POST /emails/ingest.
 * (Note: the ingest endpoint returns THIS, not an Email row.)
 */
export interface PipelineResult {
  email_id: string;
  classification: ClassificationResult;
  retrieved_chunks: RetrievedChunk[];
  routing: RoutingResult;
  draft: DraftResult;
  processing_time_ms: number;
  status: string;
}

// ---------------------------------------------------------------------------
// Chairs — db/models.py::Chair (Phase 6A multi-chair routing)
// ---------------------------------------------------------------------------

/**
 * A conference chair a human-review email can be assigned to.
 * `areas` is the list of intent/topic strings the chair owns; an empty `areas`
 * marks the catch-all fallback chair (the General Chair).
 */
export interface Chair {
  id: number;
  name: string;
  role_title: string;
  areas: string[];
  active: boolean;
}

/**
 * A `chair_reassigned` audit event, projected for analytics. `original_chair_id`
 * is the chair the email was moved away from (the router's / prior pick),
 * `new_chair_id` where it landed. Either may be null.
 */
export interface ReassignmentEvent {
  email_id: number;
  original_chair_id: number | null;
  new_chair_id: number | null;
  at: string | null;
}

// ---------------------------------------------------------------------------
// Persisted record — emails.py::_email_to_dict
// ---------------------------------------------------------------------------

export interface Email {
  id: number;
  sender: string;
  sender_name: string | null;
  subject: string;
  body: string;
  status: EmailStatus;
  /** ISO 8601 datetime, or null if unset. */
  received_at: string | null;
  /**
   * The chair this human-review email is assigned to (Phase 6A), or null when
   * unassigned (FAQ-lane emails are never assigned; also null before the chair
   * router runs). Resolve the name via the chairs roster.
   */
  assigned_chair_id: number | null;
  /**
   * Which ingestion path created this row (db/models.py EmailSource):
   * "toy_dataset" (seeded demo data) or "zendesk" (synced ticket). Drives the
   * self-hiding source toggle; toy_dataset is temporary demo data.
   */
  source?: string | null;
  /**
   * The Zendesk ticket number this row maps to (db/models.py Email.
   * zendesk_ticket_id) — only meaningful when `source === "zendesk"`; null for
   * other sources. Surfaced so the review UI can show the ticket number.
   */
  zendesk_ticket_id?: number | null;
  /**
   * Deep link to this ticket in the Zendesk agent UI, built server-side
   * (api/v1/emails.py _email_to_dict) as
   * `https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{id}`. Populated only
   * when the row has a `zendesk_ticket_id` AND the backend has ZENDESK_SUBDOMAIN
   * configured; null otherwise.
   */
  zendesk_ticket_url?: string | null;
  /**
   * Zendesk ticket status (new/open/pending/hold/solved/closed) — only
   * meaningful when `source === "zendesk"`; null for other sources.
   */
  zendesk_status?: string | null;
  classification: ClassificationResult | null;
  routing: RoutingResult | null;
  draft: DraftResult | null;
  /**
   * Which submission this email is about and who it names. Required-and-
   * nullable like the three above (not `?`): `_email_to_dict` emits the key on
   * EVERY email response, so it is always present. Null means the row was never
   * examined — see ExtractionData for why that differs from an examined result
   * that found nothing.
   */
  extraction: ExtractionData | null;
  /**
   * Transient re-evaluation state: true while a KB-change sweep is re-drafting
   * this ticket. Drives the "re-drafting…" badge; cleared when the new draft
   * lands (pushed live over the /emails/stream SSE).
   */
  redrafting?: boolean;
  /**
   * Outcome of a chair's manual policy invoke on the CURRENT draft, derived
   * server-side from `retrieval_context` (no stored column):
   *   null  — none was requested (plain redraft, or a pre-manual-invoke draft)
   *   true  — the forced policy is in the grounding set
   *   false — it was requested but skipped (unknown key, or not active)
   * Arrives on the same refetch that clears `redrafting`, so the review UI can
   * confirm success or warn about a silent skip without a second request.
   */
  forced_policy_applied?: boolean | null;
  /**
   * Retrieved policy chunks that grounded this draft. Served by the email-detail
   * endpoints (`GET /emails/{id}`, `/emails/by-ticket/{id}`), which rehydrate
   * them from the persisted `retrieval_context.retrieved_ids`. Absent on queue
   * rows (list responses are not hydrated — that would be an N+1), and null when
   * the email has no retrieval context at all; the UI then falls back to
   * `draft.citations`.
   */
  retrieved_chunks?: RetrievedChunk[] | null;
  /** Retriever inputs + grounding set captured at draft time. */
  retrieval_context?: RetrievalContext | null;
  created_at: string | null;
  updated_at: string | null;
}

/** GET /emails/queue response envelope (emails.py::get_queue). */
export interface EmailQueueResponse {
  emails: Email[];
  total: number;
  page_info: Record<string, unknown>;
}

/**
 * GET /emails/queue/facets — dedicated server-side aggregate for the queue's
 * status bar + self-hiding source toggle (emails.py::get_queue_facets). Counts
 * are grouped over the WHOLE matching set (not a capped page), and honor the
 * active lane / chair / status / search context so they compose with the queue's
 * other filters.
 */
export interface QueueFacets {
  /** {zendesk_status -> count} over source="zendesk" rows (bar counts). */
  by_zendesk_status: Record<string, number>;
  /** {source -> count} over the current context. */
  by_source: Record<string, number>;
  /** Distinct sources present in the WHOLE table — length < 2 hides the toggle. */
  sources: string[];
}

// ---------------------------------------------------------------------------
// Analytics — analytics.py::analytics_summary
// ---------------------------------------------------------------------------

export interface AnalyticsSummary {
  total_emails: number;
  faq_lane_count: number;
  human_review_count: number;
  approved_count: number;
  pending_count: number;
  avg_confidence: number;
  /** Mean confidence over ALL faq-lane emails (server-side aggregate) — for the
   * Auto-Replies "Avg Confidence" stat, page-size independent. */
  faq_avg_confidence: number;
  intent_distribution: Record<string, number>;
  /** Per-chair email volume, keyed by stringified chair id — a server-side
   * aggregate over ALL emails (accurate regardless of page size). */
  chair_distribution: Record<string, number>;
  /** Confidence histogram over ALL emails (server-side aggregate), ordered
   * low → high band. Counts are page-size independent. */
  confidence_distribution: { band: string; count: number }[];
  /** Reassignments grouped by the chair each email was moved AWAY from — a
   * server-side aggregate over ALL chair_reassigned audit rows. Keys are
   * stringified chair ids plus "unassigned" (no chair before the move). */
  reassignment_by_chair: Record<string, number>;
  daily_volume: { date: string; count: number }[];
}

// ---------------------------------------------------------------------------
// Calibration reliability — analytics.py::calibration_report
// ---------------------------------------------------------------------------

/** One decile bucket of the reliability table. */
export interface CalibrationBucket {
  bucket: string;
  n: number;
  mean_confidence: number;
  accuracy: number;
  /** accuracy − mean_confidence (positive = under-confident). */
  gap: number;
}

/** GET /analytics/calibration response. */
export interface CalibrationReport {
  backend: string;
  eval_set_size: number;
  calibration_enabled: boolean;
  calibrated_available: boolean;
  raw: CalibrationBucket[];
  calibrated: CalibrationBucket[] | null;
  metrics: {
    brier_raw: number;
    ece_raw: number;
    brier_calibrated?: number;
    ece_calibrated?: number;
  };
  caveat: string;
}

// ---------------------------------------------------------------------------
// Active-learning candidates — analytics.py::active_learning_candidates
// ---------------------------------------------------------------------------

export interface LowConfidenceFlag {
  reason: "low_confidence";
  confidence_used: number | null;
  threshold: number;
  margin: number;
}

export interface MeaningfulEditFlag {
  reason: "meaningful_edit";
  change_ratio: number;
  min_ratio: number;
}

export interface ActiveLearningCandidate {
  email_id: string;
  subject: string | null;
  reason: "low_confidence" | "meaningful_edit" | "both";
  low_confidence: LowConfidenceFlag | null;
  meaningful_edit: MeaningfulEditFlag | null;
  flagged_at: string | null;
}

export interface ActiveLearningResponse {
  candidates: ActiveLearningCandidate[];
  total: number;
}

// ---------------------------------------------------------------------------
// Audit trail
// ---------------------------------------------------------------------------

/**
 * A normalized audit-trail entry as consumed by the UI.
 *
 * NOTE: the backend has no GET /audit endpoint; the only cross-email audit feed
 * is GET /analytics/recent-activity, which omits the row id and the
 * metadata/details column. getAuditLog() normalizes that feed into this shape
 * (id = feed index, details = {}). The `details` JSON block is therefore wired
 * but stays hidden until the backend exposes per-action metadata.
 */
export interface AuditEntry {
  id: number;
  email_id: number;
  action: string;
  actor: string;
  details: Record<string, unknown>;
  created_at: string;
}

/**
 * One audit_trail row exactly as the single-email endpoints serialize it
 * (backend emails.py::_audit_to_dict). Deliberately distinct from
 * {@link AuditEntry}: that type is the UI-normalized shape of the analytics
 * recent-activity feed (email_id as number, `details`, `created_at`), whereas
 * these endpoints emit `timestamp`/`metadata` and a string `email_id`. Kept
 * accurate to the wire so downstream consumers read real fields.
 */
export interface EmailAuditTrailEntry {
  id: number;
  email_id: string;
  action: string;
  actor: string;
  timestamp: string | null;
  metadata: Record<string, unknown> | null;
}

/**
 * Envelope returned by GET /emails/{email_id} and GET /emails/by-ticket/{id}
 * (both use the same _email_to_dict / _audit_to_dict helpers, so the shape is
 * identical).
 */
export interface EmailDetailResponse {
  email: Email;
  audit_trail: EmailAuditTrailEntry[];
}

// ---------------------------------------------------------------------------
// Request payloads — match the live backend request models in
// backend/app/api/v1/emails.py.
// ---------------------------------------------------------------------------

/** IngestEmailRequest (`from`/`to` are field aliases; `timestamp` defaults to ""). */
export interface IngestRequest {
  from: string;
  to: string;
  subject: string;
  body: string;
  timestamp?: string;
}

/** ApproveRequest */
export interface ApproveRequest {
  approved_by: string;
  final_text?: string;
  /**
   * The Zendesk status the chair wants the ticket to land in after approval
   * (Open / Pending / Solved), or null/omitted for a plain approve with no
   * status change. NOT yet consumed by the backend — see the TODO at the
   * approveEmail() send site (pending the per-chair OAuth send endpoint).
   */
  target_status?: "open" | "pending" | "solved" | null;
}

/** SendRequest — POST /emails/{id}/send body (backend app/api/v1/emails.py). */
export interface SendRequest {
  /** True = public reply to the requester; false/omitted = internal note
   * (default, safe). NOT gated on ALLOW_AUTO_SEND — the backend send gate
   * authorizes any chair-APPROVED draft regardless of that flag, so `true` on
   * an approved draft does reach the requester. The UI no longer lets a chair
   * choose: EmailDetail fixes this per submit status (REPLY_PUBLIC_BY_STATUS —
   * solved → public, pending/open → internal). */
  public?: boolean;
  /** Actor recorded in the audit log (defaults to "chair" backend-side). */
  sent_by?: string;
  /** Zendesk ticket status to set on send; null/omitted keeps the §4 default
   * (public → "solved", internal → unchanged). */
  target_status?: "open" | "pending" | "solved" | null;
}

/** SetStatusRequest — POST /emails/{id}/set-status body: set the Zendesk status
 * WITHOUT sending a reply (backend app/api/v1/emails.py). */
export interface SetStatusRequest {
  /** The Zendesk status to set. Only "solved" has a keyboard shortcut.
   *  Must stay in sync with SetTicketStatusButton's `NoReplyStatus` — the two
   *  unions independently gate which values are reachable, so widening one
   *  alone either fails tsc at the call site or compiles a dead value. */
  status: "new" | "open" | "pending" | "solved";
  /** Actor recorded in the audit log (defaults to "chair" backend-side). */
  set_by?: string;
}

/** The `send` metadata block the backend attaches to a successful /send response. */
export interface SendResult {
  /** "sent" on success (a failure marks the email send_failed and returns an error). */
  state: string;
  /** "internal_note" | "public_reply". */
  mode: string;
  public: boolean;
  /** The Zendesk status set on the ticket, or null when left unchanged. */
  status_set: string | null;
  tags_added: string[];
  /** True when the reply landed but the follow-up tag write hit a 409 (not overwritten). */
  tag_conflict: boolean;
}

/** Response of POST /emails/{id}/send — the email row (status flipped to "sent")
 * plus the send metadata, and an optional warning on a tag conflict. */
export interface SendResponse extends Email {
  send: SendResult;
  warning?: string;
}

/** RerouteRequest */
export interface RerouteRequest {
  rerouted_by: string;
  reason: string;
  new_lane: EmailLane;
}

/** ReassignChairRequest — PATCH /emails/{id}/reassign-chair (Phase 6A). */
export interface ReassignChairRequest {
  reassigned_by: string;
  new_chair_id: number;
  reason?: string;
}

// ---------------------------------------------------------------------------
// Error shape (normalized by the axios response interceptor)
// ---------------------------------------------------------------------------

export interface ApiError {
  detail: string;
  status: number;
}

// ---------------------------------------------------------------------------
// Knowledge Base (policy governance) — backend app/api/v1/policies.py
// ---------------------------------------------------------------------------

export type PolicyVisibility = "public" | "internal";
export type PolicyStatus = "active" | "inactive";

/** One existing policy the model flagged as conflicting with a new one (2e). */
export interface ConflictItem {
  policy_key: string;
  title: string;
  explanation: string;
  /** Exact substrings of the conflicting policy's content, for highlighting. */
  snippets: string[];
}

/** Compact conflict report persisted on a policy / returned by /similar (2e).
 *  `available: false` ⇒ no model was configured, so nothing was checked. */
export interface ConflictReport {
  checked_at: string;
  available: boolean;
  summary: string;
  candidates_checked: string[];
  conflicts: ConflictItem[];
}

/** Mirrors policy_documents (backend/app/db/models.py PolicyDocument). */
export interface PolicyDocument {
  policy_key: string;
  title: string;
  content: string;
  category: string | null;
  // [tags-dropped E007] tags: string[];
  visibility: PolicyVisibility;
  status: PolicyStatus;
  source: string | null;
  updated_at: string | null;
  supersedes: string | null;
  superseded_by: string | null;
  root_key: string | null;
  version: number;
  conflict_report?: ConflictReport | null;
}

/** One policy_audit_logs row (backend PolicyAuditLog). */
export interface PolicyAuditEntry {
  id: number;
  policy_key: string;
  action: string; // policy_created | policy_retired | policy_reactivated | policy_edited | policy_edit_reverted
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
  content: string;
}

export interface PolicyListParams {
  visibility?: PolicyVisibility;
  status?: PolicyStatus;
  search?: string;
  /** Keep only policies with a live conflict (post staleness prune) — 2e. */
  has_conflicts?: boolean;
}

/** POST /api/v1/policies request body. */
export interface CreatePolicyRequest {
  title: string;
  content: string;
  category?: string | null;
  // [tags-dropped E007] tags?: string[];
  actor: string;
  retire_keys?: string[];
  /** The panel's precomputed conflict report for this exact text — reused
   *  server-side so the model isn't called twice (2e). */
  conflict_report?: ConflictReport | null;
}

/** PATCH /api/v1/policies/{key}/edit request body. */
export interface EditPolicyRequest {
  title: string;
  content: string;
  category?: string | null;
  visibility?: PolicyVisibility;
  actor: string;
  expected_updated_at?: string | null;
}

export interface PoliciesResponse { policies: PolicyDocument[]; }
export interface PolicyAuditResponse { entries: PolicyAuditEntry[]; }
export interface SimilarResponse {
  similar: SimilarPolicy[];
  conflict_report?: ConflictReport | null;
}

// --- Continual Experience Learning: suggestions review (Task 6) -------------

/** One PolicySuggestion row (backend app/db/models.py) surfaced for chair
 *  review at GET /policies/suggestions. A chair-gated candidate internal
 *  policy learned from a [CHAIR:]-gap edit, reviewed via the same
 *  add-internal-policy flow, pre-filled. */
export interface PolicySuggestion {
  id: number;
  source_email_id: number;
  /** Null when the source email has no linked Zendesk ticket. */
  source_zendesk_ticket_id: number | null;
  experience_summary: string;
  title: string;
  content: string;
  category: string | null;
  intents: string[];
  generalizable: boolean;
  reason: string | null;
  confidence: number | null;
  conflict_report?: ConflictReport | null;
  seen_count: number;
  status: "pending" | "accepted" | "rejected";
  created_at: string | null;
}

export interface SuggestionsResponse { suggestions: PolicySuggestion[]; }
export interface SuggestionsCountResponse { pending: number; }

/** PATCH /policies/suggestions/{id}/reject request body. */
export interface RejectSuggestionRequest {
  actor: string;
  reason?: string | null;
}

/** PATCH /policies/suggestions/{id}/accept request body. */
export interface AcceptSuggestionRequest {
  actor: string;
  policy_key: string;
}

export interface RejectSuggestionResponse { id: number; status: string; }
export interface AcceptSuggestionResponse {
  id: number;
  status: string;
  resulting_policy_key: string;
}
