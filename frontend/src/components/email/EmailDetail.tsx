"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown, FileText, Send, CornerUpRight, Zap, GitCompare } from "lucide-react";

import {
  Badge,
  ConfidenceBar,
  DiffLegend,
  DiffView,
  EmptyState,
  LoadingSpinner,
} from "@/components/ui";
import { hasMeaningfulDiff } from "@/lib/diff";
import {
  formatDateTime,
  formatIntentLabel,
  laneBadgeVariant,
  laneLabel,
  statusBadgeVariant,
  statusLabel,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Email, RetrievedChunk } from "@/types";

interface EmailDetailProps {
  email: Email;
  onApprove: (finalText?: string) => void;
  onReroute: (reason: string) => void;
  isApproving: boolean;
  isRerouting: boolean;
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
  isApproving,
  isRerouting,
}: EmailDetailProps) {
  const lane = email.routing?.lane ?? null;
  const classification = email.classification;
  const draft = email.draft;

  const [editedDraft, setEditedDraft] = useState(draft?.draft_text ?? "");
  const [rerouteOpen, setRerouteOpen] = useState(false);
  const [rerouteReason, setRerouteReason] = useState("");
  const [showDiff, setShowDiff] = useState(false);

  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const rerouteInputRef = useRef<HTMLInputElement | null>(null);

  // The original AI/template draft (before any chair edit) to diff against.
  const originalDraft = draft?.original_draft_text ?? draft?.draft_text ?? "";
  const draftChanged = hasMeaningfulDiff(originalDraft, editedDraft);
  const canAct = lane === "human_review";

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
      if (key === "a" && canAct && !isApproving) {
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
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [canAct, isApproving, editedDraft, onApprove]);

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

        {/* CLASSIFICATION */}
        <Collapsible title="Classification" defaultOpen>
          {classification ? (
            <div className="space-y-3 pt-1">
              <div className="flex items-center justify-between text-sm">
                <span style={{ color: "var(--text-secondary)" }}>Intent</span>
                <span
                  className="font-medium"
                  style={{ color: "var(--text-primary)" }}
                >
                  {formatIntentLabel(classification.intent)}
                </span>
              </div>
              <ConfidenceBar value={classification.confidence} showLabel />
              {classification.reasoning && (
                <p
                  className="text-xs leading-relaxed"
                  style={{ color: "var(--text-muted)" }}
                >
                  {classification.reasoning}
                </p>
              )}
            </div>
          ) : (
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>
              Not classified.
            </p>
          )}
        </Collapsible>

        {/* POLICY CITATIONS */}
        <Collapsible title="Policy Citations" defaultOpen>
          <PolicyCitations
            chunks={email.retrieved_chunks ?? null}
            citationIds={draft?.citations ?? []}
          />
        </Collapsible>

        {/* AI DRAFT */}
        <Collapsible title="AI Draft" icon={<FileText className="h-4 w-4" />} defaultOpen>
          {draft ? (
            <div className="space-y-2 pt-1">
              <textarea
                ref={textareaRef}
                value={editedDraft}
                onChange={(e) => setEditedDraft(e.target.value)}
                rows={8}
                spellCheck
                className="w-full resize-y rounded-lg border p-3 text-sm leading-relaxed outline-none transition-colors focus:border-[var(--accent)]"
                style={{
                  backgroundColor: "var(--surface-raised)",
                  borderColor: "var(--border)",
                  color: "var(--text-primary)",
                }}
              />
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
                disabled={isApproving}
                onClick={() => onApprove(editedDraft)}
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
                <Kbd>R</Kbd> reroute
              </div>
            </div>

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
// Policy citations (rich chunks when available, else cited policy ids)
// ---------------------------------------------------------------------------

function PolicyCitations({
  chunks,
  citationIds,
}: {
  chunks: RetrievedChunk[] | null;
  citationIds: string[];
}) {
  if (chunks && chunks.length > 0) {
    return (
      <div className="space-y-3 pt-1">
        {chunks.slice(0, 3).map((chunk) => (
          <CitationCard key={chunk.policy_id} chunk={chunk} />
        ))}
      </div>
    );
  }

  if (citationIds.length > 0) {
    return (
      <div className="space-y-2 pt-1">
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Policies cited in the draft:
        </p>
        <div className="flex flex-wrap gap-2">
          {citationIds.map((id) => (
            <Badge key={id} variant="neutral" size="sm">
              {id}
            </Badge>
          ))}
        </div>
      </div>
    );
  }

  return (
    <p className="pt-1 text-sm" style={{ color: "var(--text-muted)" }}>
      No policy citations for this email.
    </p>
  );
}

function CitationCard({ chunk }: { chunk: RetrievedChunk }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      className="rounded-lg border p-3"
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
        className={cn("text-xs leading-relaxed", !expanded && "line-clamp-3")}
        style={{ color: "var(--text-secondary)" }}
      >
        {chunk.content}
      </p>
      {chunk.content.length > 160 && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-1.5 text-xs font-medium transition-opacity hover:opacity-80"
          style={{ color: "var(--accent)" }}
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}
