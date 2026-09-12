"use client";

import { useState } from "react";
import { ChevronDown, ExternalLink } from "lucide-react";

import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { EmailAuditTrailEntry } from "@/types";

// ---------------------------------------------------------------------------
// Per-action metadata summaries
//
// ⚠️ AN ALLOW-LIST, NEVER A GENERIC DUMP, and the distinction is the whole
// design. `metadata` is an internal record: it carries exception class names
// (`OpenReviewNoteNotFoundError`), internal field names, group ids, raw
// `str(exc)` text. Rendering it generically would put all of that in front of a
// chair, and would silently start displaying whatever a future audit call
// happens to attach. So each action is written out by hand, and anything not
// listed renders EXACTLY as it did before this existed — one line, no extra.
//
// Longest sentence in this file is still a sentence: this is a retrospective
// log, not an actionable error banner. The equivalent prose on the OpenReview
// detail page is deliberately longer because it tells a chair what to do next;
// here it only says what happened.
// ---------------------------------------------------------------------------

/** Read a non-empty string off untyped metadata, or null. */
function str(meta: Record<string, unknown>, key: string): string | null {
  const value = meta[key];
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/**
 * Cap a message that came from an exception before it reaches the row.
 *
 * These strings are produced by `str(exc)` upstream and have no length
 * contract; an unbounded one would stretch the collapsed activity list rather
 * than being merely ugly.
 */
function clip(text: string, max = 160): string {
  return text.length > max ? `${text.slice(0, max).trimEnd()}…` : text;
}

/**
 * Deep link to one comment on OpenReview, or null.
 *
 * Same construction as the OpenReview detail page: BOTH ids are required. A
 * forum-only link opens the discussion at the top and leaves the reader hunting
 * for which comment the entry refers to, which is exactly the ambiguity the note
 * id removes — so the link is withheld rather than degraded.
 */
function commentUrl(forumId: string | null, noteId: string | null): string | null {
  if (!forumId || !noteId) return null;
  return `https://openreview.net/forum?id=${forumId}&noteId=${noteId}`;
}

/** What an entry adds beneath its action line: prose, an optional link, or nothing. */
type Summary = { text: string; href?: string; linkLabel?: string } | null;

function summarize(entry: EmailAuditTrailEntry): Summary {
  const meta = entry.metadata;
  if (!meta || typeof meta !== "object") return null;

  switch (entry.action) {
    case "openreview_comment_posted": {
      // `note_id` is the comment that was POSTED; `parent_note_id` is the one it
      // answers. The link points at what this entry is about — the new comment.
      // Both it and `edit_id` are documented best-effort upstream and can be
      // null on a genuinely successful post, so the link is optional while the
      // sentence is not.
      const visibility = str(meta, "visibility");
      const submission = meta["submission_number"];
      const where =
        typeof submission === "number" || typeof submission === "string"
          ? ` on submission ${submission}`
          : "";
      const audience =
        visibility === "internal"
          ? "Visible to the program chairs and the paper's authors"
          : visibility === "public"
            ? "Visible to the same people as the comment it answers"
            : "Posted";
      return {
        text: `${audience}${where}.`,
        href: commentUrl(str(meta, "forum_id"), str(meta, "note_id")) ?? undefined,
        linkLabel: "View comment",
      };
    }

    case "openreview_candidate_dismissed": {
      // The reason is the point of this action — it is the feedback signal the
      // detection would be tuned against, and the only part a person wrote.
      const reason = str(meta, "reason");
      return reason ? { text: `Reason: ${clip(reason)}` } : null;
    }

    case "openreview_post_blocked": {
      // The gate's own `reason` is written for a person, so it is shown as-is
      // rather than paraphrased into something vaguer.
      const reason = str(meta, "reason");
      return reason ? { text: clip(reason) } : null;
    }

    case "openreview_post_failed": {
      // ⚠️ `error_type` is an exception CLASS NAME and never reaches the screen.
      // It is used only to choose a sentence; an unrecognised one falls through
      // to a generic line rather than being printed.
      const errorType = str(meta, "error_type");
      const nothingPosted = "Nothing was posted.";
      switch (errorType) {
        case "OpenReviewNoteNotFoundError":
          return { text: `The comment being replied to no longer exists. ${nothingPosted}` };
        case "OpenReviewPermissionError":
          return { text: `OpenReview refused the post. ${nothingPosted}` };
        case "OpenReviewThreadMismatchError":
          return {
            text:
              "The comment belongs to a different submission than this email " +
              `names. ${nothingPosted}`,
          };
        default:
          return { text: `Posting to OpenReview failed. ${nothingPosted}` };
      }
    }

    case "zendesk_auto_solved": {
      const status = str(meta, "status_set");
      return { text: status ? `Ticket set to ${status}.` : "Ticket resolved." };
    }

    case "zendesk_auto_solve_failed": {
      // The reply IS live at this point; only the ticket is outstanding. Said
      // explicitly, because the opposite reading is the dangerous one.
      const error = str(meta, "error");
      const cause = error ? ` ${clip(error)}` : "";
      return {
        text:
          "The reply was posted, but the ticket could not be closed and is " +
          `still open.${cause}`,
      };
    }

    case "zendesk_auto_solve_skipped": {
      const reason = str(meta, "reason");
      return reason
        ? { text: `No ticket action was needed — ${clip(reason)}.` }
        : null;
    }

    // Everything else — `approved`, `zendesk_sent`, `rerouted`,
    // `chair_assigned`, `openreview_post_authorized` (whose reason is
    // boilerplate the following entry already states better), and every action
    // this feature did not introduce — renders exactly as it always has.
    default:
      return null;
  }
}

/**
 * Collapsible activity list for the ticket route, rendering the audit trail
 * returned by GET /emails/by-ticket/{id}.
 *
 * Collapsed by DEFAULT: the header (with the entry count) always shows, and the
 * list expands on click — it's reference detail, not something the chair needs
 * open while triaging, and collapsed it keeps the draft/actions above the fold.
 *
 * Deliberately a LOCAL adapter (not a shared timeline): it is typed directly to
 * {@link EmailAuditTrailEntry} — the actual wire shape of this endpoint's
 * `audit_trail` (`timestamp` / `metadata`, string `email_id`) — so the data is
 * NOT coerced to the differently-shaped {@link AuditEntry} used by the analytics
 * feed.
 */
export function TicketAuditTrail({
  entries,
}: {
  entries: EmailAuditTrailEntry[];
}) {
  const [expanded, setExpanded] = useState(false);

  // Latest first. Sort by timestamp descending (ISO strings compare
  // lexicographically); entries without a timestamp fall to the end. Copy so we
  // never mutate the prop array.
  const ordered = [...entries].sort((a, b) =>
    (b.timestamp ?? "").localeCompare(a.timestamp ?? "")
  );

  return (
    <div
      className="shrink-0 px-6 py-3"
      style={{ borderTop: "1px solid var(--border)" }}
      data-testid="ticket-audit-trail"
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-controls="ticket-audit-trail-list"
        className="flex w-full items-center gap-1.5 text-xs font-semibold uppercase tracking-wide transition-colors hover:text-[var(--text-secondary)]"
        style={{ color: "var(--text-muted)" }}
      >
        <ChevronDown
          className={cn("h-3.5 w-3.5 transition-transform", !expanded && "-rotate-90")}
          aria-hidden
        />
        Activity ({entries.length})
      </button>

      {expanded && (
        <div id="ticket-audit-trail-list" className="mt-2 max-h-40 overflow-y-auto">
          {entries.length === 0 ? (
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              No recorded activity.
            </p>
          ) : (
            <ul className="space-y-1">
              {ordered.map((entry) => {
                const summary = summarize(entry);
                return (
                  <li key={entry.id} className="text-xs">
                    {/* ⚠️ THIS ROW IS UNCHANGED, deliberately. An entry with no
                        summary must render byte-identically to how it did
                        before metadata was surfaced at all — the whole Inbox
                        shares this component, and most of its actions are ones
                        this feature never introduced. */}
                    <div
                      className="flex items-baseline gap-2"
                      style={{ color: "var(--text-secondary)" }}
                    >
                      <span
                        className="font-medium"
                        style={{ color: "var(--text-primary)" }}
                      >
                        {entry.action}
                      </span>
                      <span style={{ color: "var(--text-muted)" }}>
                        · {entry.actor}
                      </span>
                      {/* Reads `timestamp` (EmailAuditTrailEntry), NOT `created_at`
                          (AuditEntry) — proof the shape is not coerced. */}
                      {entry.timestamp && (
                        <span
                          className="ml-auto tabular-nums"
                          style={{ color: "var(--text-muted)" }}
                        >
                          {formatDateTime(entry.timestamp)}
                        </span>
                      )}
                    </div>

                    {summary && (
                      <div
                        className="mt-0.5 flex flex-wrap items-baseline gap-x-2 pl-3"
                        style={{ color: "var(--text-muted)" }}
                      >
                        <span>{summary.text}</span>
                        {summary.href && (
                          <a
                            href={summary.href}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 underline-offset-4 hover:underline"
                            style={{ color: "var(--accent)" }}
                          >
                            <ExternalLink className="h-3 w-3" aria-hidden />
                            {summary.linkLabel}
                          </a>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
