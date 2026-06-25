/**
 * Frontend type definitions mirroring the backend Pydantic schemas
 * (see backend/app/models/enums.py and schemas.py). Keep in sync — these
 * are the API contract for the UI layer.
 */

// ---------------------------------------------------------------------------
// Enums (string unions — match backend enum values exactly)
// ---------------------------------------------------------------------------
export type EmailIntent =
  | "FAQ_DEADLINE"
  | "FAQ_FORMAT"
  | "FAQ_SUBMISSION"
  | "REVIEW_ASSIGNMENT"
  | "VISA_LETTER"
  | "APPEAL"
  | "AMBIGUOUS"
  | "OTHER";

export type RoutingLane = "AUTO_REPLY" | "HUMAN_REVIEW";

export type SensitivityLevel = "LOW" | "MEDIUM" | "HIGH";

export type EmailStatus =
  | "PENDING"
  | "CLASSIFIED"
  | "ROUTED"
  | "DRAFT_GENERATED"
  | "APPROVED"
  | "SENT"
  | "ARCHIVED";

export type UserRole = "CHAIR" | "REVIEWER" | "ADMIN";

// ---------------------------------------------------------------------------
// Inbound payload
// ---------------------------------------------------------------------------
export interface EmailIn {
  sender: string;
  sender_name?: string | null;
  subject: string;
  body: string;
  received_at?: string | null; // ISO 8601 datetime
}

// ---------------------------------------------------------------------------
// Pipeline result sub-objects
// ---------------------------------------------------------------------------
export interface IntentMatch {
  intent: EmailIntent;
  score: number;
}

export interface ClassificationResult {
  intent: EmailIntent;
  confidence: number;
  reasoning: string;
  top_matches: IntentMatch[];
}

export interface RoutingDecision {
  lane: RoutingLane;
  sensitivity: SensitivityLevel;
  reason: string;
  confidence: number;
}

export interface PolicyCitation {
  policy_id: string;
  title: string;
  snippet: string;
  score?: number | null;
}

export interface RetrievalContextItem {
  policy_id: string;
  title: string;
  content: string;
  score: number;
}

export interface DraftResponse {
  draft_body: string;
  policy_citations: PolicyCitation[];
  retrieval_context: RetrievalContextItem[];
}

// ---------------------------------------------------------------------------
// Persisted record
// ---------------------------------------------------------------------------
export interface EmailRecord {
  id: number;
  sender: string;
  sender_name?: string | null;
  subject: string;
  body: string;
  received_at: string; // ISO 8601 datetime
  status: EmailStatus;
  classification?: ClassificationResult | null;
  routing?: RoutingDecision | null;
  draft?: DraftResponse | null;
  created_at: string; // ISO 8601 datetime
  updated_at: string; // ISO 8601 datetime
}

// ---------------------------------------------------------------------------
// Human-review action
// ---------------------------------------------------------------------------
export interface ApprovalAction {
  action: "approve" | "edit" | "reroute";
  edited_body?: string | null;
  reroute_reason?: string | null;
}
