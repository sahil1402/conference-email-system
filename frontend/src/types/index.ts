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

/** One turn in a ticket's conversation — GET /emails/{id}/thread. */
export interface EmailThreadMessage {
  comment_id: number | null;
  /** True = reply visible to the requester; False = internal note. */
  public: boolean;
  /** "end-user" | "agent" | "admin" (Zendesk role); null if unresolved. */
  author_role: string | null;
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

/** retriever.py::RetrievedChunk (returned inside the ingest PipelineResult). */
export interface RetrievedChunk {
  policy_id: string;
  title: string;
  content: string;
  score: number;
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
   * Zendesk ticket status (new/open/pending/hold/solved/closed) — only
   * meaningful when `source === "zendesk"`; null for other sources.
   */
  zendesk_status?: string | null;
  classification: ClassificationResult | null;
  routing: RoutingResult | null;
  draft: DraftResult | null;
  /**
   * Transient re-evaluation state: true while a KB-change sweep is re-drafting
   * this ticket. Drives the "re-drafting…" badge; cleared when the new draft
   * lands (pushed live over the /emails/stream SSE).
   */
  redrafting?: boolean;
  /**
   * Retrieved policy chunks. NOT currently persisted on the email row by the
   * backend (_email_to_dict omits it) — only the ingest PipelineResult carries
   * them. Declared optional so the review UI can render rich citations if/when
   * the backend starts persisting them; today the UI falls back to
   * `draft.citations` (the cited policy ids).
   */
  retrieved_chunks?: RetrievedChunk[] | null;
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
  /** True = public reply to the requester (needs ALLOW_AUTO_SEND); false/omitted
   * = internal note (default, safe). */
  public?: boolean;
  /** Actor recorded in the audit log (defaults to "chair" backend-side). */
  sent_by?: string;
  /** Zendesk ticket status to set on send; null/omitted keeps the §4 default
   * (public → "solved", internal → unchanged). */
  target_status?: "open" | "pending" | "solved" | null;
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
}

/** POST /api/v1/policies request body. */
export interface CreatePolicyRequest {
  title: string;
  content: string;
  category?: string | null;
  // [tags-dropped E007] tags?: string[];
  actor: string;
  retire_keys?: string[];
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
export interface SimilarResponse { similar: SimilarPolicy[]; }
