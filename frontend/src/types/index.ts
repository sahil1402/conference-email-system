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

/** The 11 intent labels the classifier can emit (classifier.py: VALID_INTENTS). */
export type IntentLabel =
  | "submission_deadline"
  | "formatting_requirements"
  | "general_inquiry"
  | "review_assignment"
  | "authorship_dispute"
  | "submission_withdrawal"
  | "ethics_concern"
  | "technical_issue"
  // Phase 6A conference-operations intents.
  | "sponsorship"
  | "publicity"
  | "media_inquiry";

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
  /** Set once a chair edits the draft (Phase 5F): the original AI/template text. */
  original_draft_text?: string;
  is_edited?: boolean;
  edited_by?: string;
}

/** retriever.py::RetrievedChunk (returned inside the ingest PipelineResult). */
export interface RetrievedChunk {
  policy_id: string;
  title: string;
  content: string;
  score: number;
  category: string;
  tags: string[];
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
  classification: ClassificationResult | null;
  routing: RoutingResult | null;
  draft: DraftResult | null;
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
