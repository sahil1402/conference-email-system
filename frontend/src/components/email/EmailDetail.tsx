"use client";

import {
  useEffect,
  useRef,
  useState,
  type MutableRefObject,
  type ReactNode,
} from "react";
import {
  AlertTriangle,
  AlertOctagon,
  ChevronDown,
  FileText,
  Send,
  CornerUpRight,
  Zap,
  GitCompare,
  Users,
  Check,
} from "lucide-react";

import {
  Badge,
  ChairBadge,
  DiffLegend,
  DiffView,
  EmptyState,
  ErrorBanner,
  LoadingSpinner,
} from "@/components/ui";
import { PolicyDetailModal } from "./PolicyDetailModal";
import { hasMeaningfulDiff } from "@/lib/diff";
import {
  formatDateTime,
  laneBadgeVariant,
  laneLabel,
  statusBadgeVariant,
  statusLabel,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ApiError, Chair, Email, RetrievedChunk } from "@/types";

/**
 * [CHAIR: ...] placeholders the drafter leaves where the policy context could
 * not support the reply (Phase 7F). Mirrors backend drafter.PLACEHOLDER_RE —
 * the approve endpoint 409s while any remain, so the UI blocks approval too.
 */
const PLACEHOLDER_RE = /\[CHAIR:\s*([^\]]*)\]/g;

function findPlaceholders(text: string): string[] {
  return Array.from(text.matchAll(PLACEHOLDER_RE), (m) => m[1].trim());
}

interface EmailDetailProps {
  email: Email;
  onApprove: (finalText?: string) => void;
  onReroute: (reason: string) => void;
  /**
   * Reassign this email to a chair (Phase 6A). Returns a promise so the pane can
   * show inline success / error feedback scoped to this email.
   */
  onReassignChair: (chairId: number, reason: string) => Promise<unknown>;
  isApproving: boolean;
  isRerouting: boolean;
  isReassigning: boolean;
  /** The chair roster for the reassignment picker + name resolution. */
  chairs: Chair[];
}

/**
 * Right pane of the split view. Owns the editable draft + reroute-reason state
 * internally; the parent passes the final values through onApprove / onReroute.
 * The parent should key this on email.id so state resets per selection.
 */
export function EmailDetail({
  email,
  onApprove,
  onReroute,
  onReassignChair,
  isApproving,
  isRerouting,
  isReassigning,
  chairs,
}: EmailDetailProps) {
  const lane = email.routing?.lane ?? null;
  const draft = email.draft;

  // Chair-facing notes (7F): a newline-delimited blob → structured, severity-
  // aware items. Computed here so the section can be omitted entirely when empty.
  const chairNotes = parseChairNotes(draft?.notes_for_chair);

  const [editedDraft, setEditedDraft] = useState(draft?.draft_text ?? "");
  const [rerouteOpen, setRerouteOpen] = useState(false);
  const [rerouteReason, setRerouteReason] = useState("");
  const [showDiff, setShowDiff] = useState(false);

  // Chair reassignment state (Phase 6A).
  const chairsById = new Map(chairs.map((c) => [c.id, c]));
  const [reassignOpen, setReassignOpen] = useState(false);
  const [reassignReason, setReassignReason] = useState("");
  const [reassignError, setReassignError] = useState<string | null>(null);
  // Optimistic: the chair we just reassigned to, shown immediately before the
  // queue refetch lands. Null until a successful reassignment this session.
  const [reassignedTo, setReassignedTo] = useState<number | null>(null);

  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const rerouteInputRef = useRef<HTMLInputElement | null>(null);
  const chairSelectRef = useRef<HTMLSelectElement | null>(null);

  // The original AI/template draft (before any chair edit) to diff against.
  const originalDraft = draft?.original_draft_text ?? draft?.draft_text ?? "";
  const draftChanged = hasMeaningfulDiff(originalDraft, editedDraft);
  const canAct = lane === "human_review";

  // Live placeholder check on the CURRENT edit — approval unblocks the moment
  // the chair resolves the last [CHAIR: ...] token (backend enforces the same).
  const unresolvedPlaceholders = findPlaceholders(editedDraft);
  const canApprove = canAct && unresolvedPlaceholders.length === 0;

  // Current assignment (optimistic value wins until the refetch confirms it).
  const currentChairId = reassignedTo ?? email.assigned_chair_id;
  const currentChairName =
    currentChairId != null ? chairsById.get(currentChairId)?.name ?? null : null;
  const [pickedChairId, setPickedChairId] = useState<number | null>(
    email.assigned_chair_id ?? chairs.find((c) => c.active)?.id ?? null
  );

  async function handleReassign() {
    if (pickedChairId == null) return;
    setReassignError(null);
    try {
      await onReassignChair(pickedChairId, reassignReason.trim());
      setReassignedTo(pickedChairId);
      setReassignReason("");
      setReassignOpen(false);
    } catch (err) {
      setReassignError((err as ApiError)?.detail ?? "Reassignment failed.");
    }
  }

  // Keyboard shortcuts, scoped to the review pane (this component only mounts
  // when an email is open). A = approve, E = edit (focus draft), R = reroute.
  // They never fire while focus is in a text field, so typing is never hijacked.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) {
        return; // don't hijack typing
      }
      const key = e.key.toLowerCase();
      if (key === "a" && canApprove && !isApproving) {
        e.preventDefault();
        onApprove(editedDraft);
      } else if (key === "e") {
        const el = textareaRef.current;
        if (el) {
          e.preventDefault();
          el.focus();
          el.setSelectionRange(el.value.length, el.value.length);
        }
      } else if (key === "r" && canAct) {
        e.preventDefault();
        setRerouteOpen(true);
        requestAnimationFrame(() => rerouteInputRef.current?.focus());
      } else if (key === "c" && canAct) {
        e.preventDefault();
        setReassignOpen(true);
        requestAnimationFrame(() => chairSelectRef.current?.focus());
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [canAct, canApprove, isApproving, editedDraft, onApprove]);

  return (
    <div className="flex h-full flex-col">
      {/* Scrollable content */}
      <div className="flex-1 space-y-5 overflow-y-auto p-6">
        {/* HEADER */}
        <header className="space-y-3">
          <h2
            className="text-xl font-semibold leading-snug"
            style={{ color: "var(--text-primary)" }}
          >
            {email.subject || "(no subject)"}
          </h2>
          <div
            className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm"
            style={{ color: "var(--text-secondary)" }}
          >
            <span style={{ color: "var(--text-primary)" }}>{email.sender}</span>
            <span style={{ color: "var(--text-muted)" }}>·</span>
            <span>{formatDateTime(email.received_at ?? email.created_at)}</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={statusBadgeVariant(email.status)} size="sm">
              {statusLabel(email.status)}
            </Badge>
            {lane && (
              <Badge variant={laneBadgeVariant(lane)} size="sm">
                {laneLabel(lane)}
              </Badge>
            )}
            {lane === "human_review" && (
              <ChairBadge chairId={currentChairId} chairName={currentChairName} />
            )}
          </div>
        </header>

        {/* EMAIL BODY */}
        <div
          className="max-h-64 overflow-y-auto rounded-lg border p-4 text-sm leading-relaxed"
          style={{
            backgroundColor: "var(--surface-raised)",
            borderColor: "var(--border-subtle)",
            color: "var(--text-primary)",
            fontFamily:
              'ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace',
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {email.body}
        </div>

        {/* POLICY CITATIONS */}
        <Collapsible title="Policy Citations" defaultOpen>
          <PolicyCitations
            chunks={email.retrieved_chunks ?? null}
            citationIds={draft?.citations ?? []}
          />
        </Collapsible>

        {/* CHAIR SUGGESTIONS — drafter notes + gaps, never part of the reply.
            Empty → the whole section is omitted (no empty box). */}
        {chairNotes.length > 0 && (
          <Collapsible
            title="Chair Suggestions"
            icon={<AlertTriangle className="h-4 w-4" style={{ color: "var(--warning)" }} />}
            defaultOpen
          >
            <ChairNotesPanel notes={chairNotes} />
          </Collapsible>
        )}

        {/* AI DRAFT */}
        <Collapsible title="AI Draft" icon={<FileText className="h-4 w-4" />} defaultOpen>
          {draft ? (
            <div className="space-y-2 pt-1">
              <HighlightedDraftEditor
                value={editedDraft}
                onChange={setEditedDraft}
                textareaRef={textareaRef}
              />
              {unresolvedPlaceholders.length > 0 && (
                <p
                  className="flex items-center gap-1.5 text-xs"
                  style={{ color: "var(--warning)" }}
                >
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                  {unresolvedPlaceholders.length} unresolved [CHAIR] placeholder
                  {unresolvedPlaceholders.length > 1 ? "s" : ""} — resolve to
                  enable Approve &amp; Send.
                </p>
              )}
              <div className="flex items-center justify-between">
                <button
                  type="button"
                  disabled={!draftChanged}
                  onClick={() => setShowDiff((v) => !v)}
                  className="inline-flex items-center gap-1.5 text-xs font-medium transition-opacity hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-40"
                  style={{ color: "var(--accent)" }}
                  title={
                    draftChanged
                      ? "Compare your edit against the original draft"
                      : "No changes to the original draft yet"
                  }
                >
                  <GitCompare className="h-3.5 w-3.5" />
                  {showDiff ? "Hide changes" : "Show changes"}
                </button>
                <span
                  className="text-xs tabular-nums"
                  style={{ color: "var(--text-muted)" }}
                >
                  {editedDraft.length} characters
                </span>
              </div>

              {showDiff && draftChanged && (
                <div className="space-y-2 pt-1">
                  <div className="flex items-center justify-between">
                    <span
                      className="text-xs font-medium"
                      style={{ color: "var(--text-secondary)" }}
                    >
                      Original vs. your edit
                    </span>
                    <DiffLegend />
                  </div>
                  <DiffView original={originalDraft} edited={editedDraft} />
                </div>
              )}
            </div>
          ) : (
            <EmptyState
              icon={<FileText className="h-5 w-5" />}
              title="No draft generated"
              description="This email has no AI-generated draft. It may have failed drafting or predates the pipeline."
            />
          )}
        </Collapsible>
      </div>

      {/* ACTION BAR (pinned bottom) */}
      <div
        className="shrink-0 p-4"
        style={{
          borderTop: "1px solid var(--border)",
          backgroundColor: "var(--surface)",
        }}
      >
        {lane === "faq" ? (
          <div className="flex flex-wrap items-center gap-3">
            <Badge variant="success" size="md">
              <Zap className="h-3 w-3" /> Auto-replied
            </Badge>
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>
              {formatDateTime(email.updated_at ?? email.received_at)}
            </span>
            <span
              className="w-full text-xs"
              style={{ color: "var(--text-muted)" }}
            >
              This email was handled automatically.
            </span>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                disabled={isApproving || !canApprove}
                onClick={() => onApprove(editedDraft)}
                title={
                  canApprove
                    ? undefined
                    : "Resolve the [CHAIR: …] placeholders in the draft first"
                }
                className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                style={{
                  backgroundColor: "var(--success)",
                  color: "var(--text-primary)",
                }}
              >
                {isApproving ? (
                  <LoadingSpinner size="sm" className="!text-[var(--text-primary)]" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
                Approve &amp; Send
              </button>

              <button
                type="button"
                disabled={isReassigning}
                onClick={() => setReassignOpen((v) => !v)}
                className="inline-flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium transition-colors hover:bg-[var(--surface-raised)] disabled:cursor-not-allowed disabled:opacity-60"
                style={{
                  borderColor: "var(--border)",
                  color: "var(--text-secondary)",
                }}
              >
                <Users className="h-4 w-4" />
                Reassign chair
              </button>

              <button
                type="button"
                disabled={isRerouting}
                onClick={() => setRerouteOpen((v) => !v)}
                className="inline-flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium transition-colors hover:bg-[var(--surface-raised)] disabled:cursor-not-allowed disabled:opacity-60"
                style={{
                  borderColor: "var(--border)",
                  color: "var(--text-secondary)",
                }}
              >
                <CornerUpRight className="h-4 w-4" />
                Reroute to FAQ
              </button>

              {/* Keyboard shortcut hint (inactive while typing in a field). */}
              <div
                className="ml-auto hidden items-center gap-2 text-xs sm:flex"
                style={{ color: "var(--text-muted)" }}
                title="Shortcuts work when an email is open and you're not typing in a field"
              >
                <Kbd>A</Kbd> approve
                <Kbd>E</Kbd> edit
                <Kbd>C</Kbd> reassign
                <Kbd>R</Kbd> reroute
              </div>
            </div>

            {/* Success confirmation after a reassignment (inline, no toast infra). */}
            {reassignedTo != null && !reassignOpen && (
              <div
                className="flex items-center gap-2 text-xs"
                style={{ color: "var(--success)" }}
              >
                <Check className="h-3.5 w-3.5" />
                Assigned to {currentChairName ?? `Chair #${currentChairId}`}.
              </div>
            )}

            {/* Inline chair-reassignment form (no modal — keeps context visible) */}
            {reassignOpen && (
              <div
                className="flex flex-col gap-2 rounded-lg border p-3"
                style={{
                  borderColor: "var(--border)",
                  backgroundColor: "var(--surface-raised)",
                }}
              >
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                  <select
                    ref={chairSelectRef}
                    value={pickedChairId ?? ""}
                    onChange={(e) => setPickedChairId(Number(e.target.value))}
                    aria-label="Assign to chair"
                    className="rounded-md border px-3 py-1.5 text-sm outline-none transition-colors focus:border-[var(--accent)] sm:w-64"
                    style={{
                      backgroundColor: "var(--surface)",
                      borderColor: "var(--border)",
                      color: "var(--text-primary)",
                    }}
                  >
                    {chairs.map((chair) => (
                      <option
                        key={chair.id}
                        value={chair.id}
                        style={{ backgroundColor: "var(--surface)" }}
                      >
                        {chair.name}
                        {chair.active ? "" : " (inactive)"}
                        {chair.id === email.assigned_chair_id ? " · current" : ""}
                      </option>
                    ))}
                  </select>
                  <input
                    type="text"
                    value={reassignReason}
                    onChange={(e) => setReassignReason(e.target.value)}
                    placeholder="Reason (optional)…"
                    className="flex-1 rounded-md border px-3 py-1.5 text-sm outline-none transition-colors focus:border-[var(--accent)]"
                    style={{
                      backgroundColor: "var(--surface)",
                      borderColor: "var(--border)",
                      color: "var(--text-primary)",
                    }}
                  />
                  <button
                    type="button"
                    disabled={
                      isReassigning ||
                      pickedChairId == null ||
                      pickedChairId === email.assigned_chair_id
                    }
                    onClick={handleReassign}
                    className="inline-flex items-center justify-center gap-2 rounded-md px-3 py-1.5 text-sm font-semibold transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                    style={{
                      backgroundColor: "var(--accent)",
                      color: "var(--text-primary)",
                    }}
                  >
                    {isReassigning && <LoadingSpinner size="sm" />}
                    Assign
                  </button>
                </div>
                {reassignError && <ErrorBanner message={reassignError} />}
              </div>
            )}

            {/* Inline reroute form (no modal — keeps context visible) */}
            {rerouteOpen && (
              <div
                className="flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center"
                style={{
                  borderColor: "var(--border)",
                  backgroundColor: "var(--surface-raised)",
                }}
              >
                <input
                  ref={rerouteInputRef}
                  type="text"
                  value={rerouteReason}
                  onChange={(e) => setRerouteReason(e.target.value)}
                  placeholder="Reason for rerouting…"
                  className="flex-1 rounded-md border px-3 py-1.5 text-sm outline-none transition-colors focus:border-[var(--accent)]"
                  style={{
                    backgroundColor: "var(--surface)",
                    borderColor: "var(--border)",
                    color: "var(--text-primary)",
                  }}
                />
                <button
                  type="button"
                  disabled={isRerouting || !rerouteReason.trim()}
                  onClick={() => onReroute(rerouteReason.trim())}
                  className="inline-flex items-center justify-center gap-2 rounded-md px-3 py-1.5 text-sm font-semibold transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                  style={{
                    backgroundColor: "var(--accent)",
                    color: "var(--text-primary)",
                  }}
                >
                  {isRerouting && <LoadingSpinner size="sm" />}
                  Confirm
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Draft editor with [CHAIR: ...] placeholders highlighted in place (Phase 7F)
// ---------------------------------------------------------------------------

/** Identical box/typography on backdrop + textarea so their text overlays 1:1. */
const _EDITOR_CLASSES =
  "block w-full whitespace-pre-wrap break-words rounded-lg border p-3 text-sm leading-relaxed";

/**
 * A textarea whose [CHAIR: ...] tokens glow: a backdrop div paints the text
 * (placeholders wrapped in <mark>), while the textarea on top keeps its text
 * transparent — only the caret, selection, and native editing stay live.
 */
function HighlightedDraftEditor({
  value,
  onChange,
  textareaRef,
}: {
  value: string;
  onChange: (v: string) => void;
  textareaRef: MutableRefObject<HTMLTextAreaElement | null>;
}) {
  const backdropRef = useRef<HTMLDivElement | null>(null);

  const segments: ReactNode[] = [];
  let last = 0;
  // Array.from (not for...of on the iterator): the CI type check targets a
  // lib level where iterators need downlevelIteration, arrays don't.
  for (const m of Array.from(value.matchAll(PLACEHOLDER_RE))) {
    const at = m.index ?? 0;
    if (at > last) segments.push(value.slice(last, at));
    segments.push(
      <mark
        key={at}
        style={{
          backgroundColor: "var(--warning-subtle)",
          color: "var(--warning)",
          fontWeight: 600,
          borderRadius: "3px",
          boxShadow: "inset 0 0 0 1px var(--warning)",
        }}
      >
        {m[0]}
      </mark>
    );
    last = at + m[0].length;
  }
  segments.push(value.slice(last));

  return (
    <div className="relative">
      <div
        ref={backdropRef}
        aria-hidden
        className={cn(
          _EDITOR_CLASSES,
          "pointer-events-none absolute inset-0 overflow-hidden"
        )}
        style={{
          backgroundColor: "var(--surface-raised)",
          borderColor: "transparent",
          color: "var(--text-primary)",
        }}
      >
        {segments}
        {"\n" /* trailing newline keeps backdrop scroll extent == textarea */}
      </div>
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onScroll={(e) => {
          const backdrop = backdropRef.current;
          if (backdrop) backdrop.scrollTop = e.currentTarget.scrollTop;
        }}
        rows={8}
        spellCheck
        className={cn(
          _EDITOR_CLASSES,
          "relative resize-y outline-none transition-colors focus:border-[var(--accent)]"
        )}
        style={{
          backgroundColor: "transparent",
          borderColor: "var(--border)",
          color: "transparent",
          caretColor: "var(--text-primary)",
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Keyboard-shortcut key cap
// ---------------------------------------------------------------------------

function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd
      className="inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded border px-1 font-mono text-[11px]"
      style={{
        backgroundColor: "var(--surface-raised)",
        borderColor: "var(--border)",
        color: "var(--text-secondary)",
      }}
    >
      {children}
    </kbd>
  );
}

// ---------------------------------------------------------------------------
// Collapsible section
// ---------------------------------------------------------------------------

function Collapsible({
  title,
  icon,
  defaultOpen = true,
  children,
}: {
  title: string;
  icon?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section
      className="rounded-lg border"
      style={{ borderColor: "var(--border)", backgroundColor: "var(--surface)" }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-3"
      >
        <span
          className="flex items-center gap-2 text-sm font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          {icon}
          {title}
        </span>
        <ChevronDown
          className={cn(
            "h-4 w-4 transition-transform duration-200",
            open && "rotate-180"
          )}
          style={{ color: "var(--text-muted)" }}
        />
      </button>
      {/* Smooth height + fade via animated grid rows (no magic max-height). */}
      <div
        className="grid transition-all duration-200 ease-out"
        style={{ gridTemplateRows: open ? "1fr" : "0fr", opacity: open ? 1 : 0 }}
      >
        <div className="overflow-hidden">
          <div className="px-4 pb-4">{children}</div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Chair notes — structured, severity-aware view of drafter.notes_for_chair (7F)
// ---------------------------------------------------------------------------

type ChairNoteSeverity = "advisory" | "urgent";

interface ChairNote {
  text: string;
  severity: ChairNoteSeverity;
}

/**
 * The backend appends automated leak-check flags to notes_for_chair with this
 * exact prefix (drafter._apply_reply_contract). It's the one severity signal in
 * the blob today: such a line means possible chair-facing meta language leaking
 * into the requester reply → urgent. A dedicated backend `severity` field would
 * be cleaner than sniffing the prefix, but this keeps the change frontend-only.
 */
const URGENT_NOTE_PREFIX_RE = /^WARNING \(automated check\):\s*/i;

/**
 * Split the newline-delimited notes blob into per-line items (one caveat/gap per
 * line, per the drafter contract), tagging each with a severity. Blank lines
 * (incl. the \n\n gap before an appended warning) are dropped; the urgent prefix
 * is stripped from the displayed text since the styling already conveys it.
 * Empty / whitespace-only / nullish input → [] (caller omits the section).
 */
function parseChairNotes(raw: string | null | undefined): ChairNote[] {
  if (!raw) return [];
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => {
      const urgent = URGENT_NOTE_PREFIX_RE.test(line);
      return {
        text: urgent ? line.replace(URGENT_NOTE_PREFIX_RE, "").trim() : line,
        severity: urgent ? "urgent" : "advisory",
      } satisfies ChairNote;
    });
}

/**
 * Renders parsed chair notes as distinct rows, escalating amber (advisory) →
 * red (urgent). Returns null when there are none so no empty box appears.
 */
function ChairNotesPanel({ notes }: { notes: ChairNote[] }) {
  if (notes.length === 0) return null;
  return (
    <div className="space-y-2 pt-1">
      {notes.map((note, i) => (
        <ChairNoteRow key={i} note={note} />
      ))}
      <p className="pt-0.5 text-xs" style={{ color: "var(--text-muted)" }}>
        Internal — not sent to the requester.
      </p>
    </div>
  );
}

/**
 * One note: a left accent bar + tinted surface carry the severity (restrained,
 * not a full-bleed alert). Urgent rows add a small label so the escalation reads
 * even for a colorblind chair — color is never the only signal.
 */
function ChairNoteRow({ note }: { note: ChairNote }) {
  const urgent = note.severity === "urgent";
  const accent = urgent ? "var(--danger)" : "var(--warning)";
  const tint = urgent ? "var(--danger-subtle)" : "var(--warning-subtle)";
  const Icon = urgent ? AlertOctagon : AlertTriangle;
  return (
    <div
      className="flex items-start gap-2.5 rounded-md border-l-[3px] p-2.5 pl-3 text-sm leading-relaxed"
      style={{
        backgroundColor: tint,
        borderColor: accent,
        color: "var(--text-primary)",
      }}
    >
      <Icon className="mt-0.5 h-4 w-4 shrink-0" style={{ color: accent }} />
      <div className="min-w-0 space-y-0.5">
        {urgent && (
          <span
            className="block text-[11px] font-semibold uppercase tracking-wide"
            style={{ color: accent }}
          >
            Automated leak check
          </span>
        )}
        <p style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {note.text}
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Policy citations (rich chunks when available, else cited policy ids)
// ---------------------------------------------------------------------------

function PolicyCitations({
  chunks,
  citationIds,
}: {
  chunks: RetrievedChunk[] | null;
  citationIds: string[];
}) {
  // The cited policy key whose full detail is open in the modal (null = closed).
  const [openPolicyKey, setOpenPolicyKey] = useState<string | null>(null);

  let body: ReactNode;
  if (chunks && chunks.length > 0) {
    body = (
      <div className="space-y-3 pt-1">
        {chunks.slice(0, 3).map((chunk) => (
          <CitationCard
            key={chunk.policy_id}
            chunk={chunk}
            onOpen={() => setOpenPolicyKey(chunk.policy_id)}
          />
        ))}
      </div>
    );
  } else if (citationIds.length > 0) {
    body = (
      <div className="space-y-2 pt-1">
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Policies cited in the draft (click for full text):
        </p>
        <div className="flex flex-wrap gap-2">
          {citationIds.map((id) => (
            <button
              key={id}
              type="button"
              onClick={() => setOpenPolicyKey(id)}
              aria-label={`View policy ${id}`}
              className="rounded-full outline-none transition-transform hover:scale-[1.03] focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              <Badge variant="neutral" size="sm">
                {id}
              </Badge>
            </button>
          ))}
        </div>
      </div>
    );
  } else {
    body = (
      <p className="pt-1 text-sm" style={{ color: "var(--text-muted)" }}>
        No policy citations for this email.
      </p>
    );
  }

  return (
    <>
      {body}
      <PolicyDetailModal
        policyKey={openPolicyKey}
        onClose={() => setOpenPolicyKey(null)}
      />
    </>
  );
}

/**
 * A retrieved-chunk card. The whole card is a button: clicking (or Enter/Space)
 * opens the full-detail modal (source, id, tags, full text). Content is clamped
 * to a 3-line preview here; the modal carries the complete text, so no separate
 * inline expander is needed (and nesting a button inside a button is invalid).
 */
function CitationCard({
  chunk,
  onOpen,
}: {
  chunk: RetrievedChunk;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`View policy ${chunk.title || chunk.policy_id}`}
      className="block w-full rounded-lg border p-3 text-left outline-none transition-colors hover:border-[var(--accent)] focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
      style={{
        borderColor: "var(--border-subtle)",
        backgroundColor: "var(--surface-raised)",
      }}
    >
      <div className="mb-1 flex items-start justify-between gap-2">
        <span
          className="text-sm font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          {chunk.title || chunk.policy_id}
        </span>
        {chunk.category && (
          <Badge variant="neutral" size="sm">
            {chunk.category}
          </Badge>
        )}
      </div>
      <p
        className="text-xs leading-relaxed line-clamp-3"
        style={{ color: "var(--text-secondary)" }}
      >
        {chunk.content}
      </p>
    </button>
  );
}
