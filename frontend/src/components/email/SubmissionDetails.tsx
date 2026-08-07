import { ExternalLink } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ExtractionData } from "@/types";

/** Public forum page for a submission, keyed by its OpenReview forum id. */
const OPENREVIEW_FORUM_URL = "https://openreview.net/forum";

interface SubmissionDetailsProps {
  /**
   * The email's `extraction` (Email.extraction). Null when the row was never
   * examined — see ExtractionData for why that differs from an examined result
   * that found nothing. Both render nothing here.
   */
  extraction: ExtractionData | null;
  className?: string;
}

/** A value counts as present only if it is a non-blank string. */
function isPresent(value: string | null | undefined): boolean {
  return typeof value === "string" && value.trim().length > 0;
}

/**
 * Which submission an email is about, and (later) who it names.
 *
 * Owns its own "render nothing when there is nothing to show" logic, so callers
 * can pass the raw nullable `extraction` without a surrounding conditional —
 * same contract as ZendeskLinkButton.
 *
 * Structure: the inline Intent-row idiom (surface-tinted `rounded-lg`, uppercase
 * muted label + value), NOT a Collapsible card. Collapsibles here exist to
 * bound the vertical cost of an unbounded list — Policy Citations renders N
 * policy cards, Previous drafts renders N drafts. This panel holds at most three
 * short values, so a disclosure control would hide already-cheap content behind
 * a click and add a box around almost nothing.
 *
 * (2a: submission_number. 2b: the OpenReview link. Authors are 2c — but the
 * emptiness check below already considers all three, so that subtask does not
 * have to revisit this component's render/don't-render decision.)
 */
export function SubmissionDetails({
  extraction,
  className,
}: SubmissionDetailsProps) {
  if (extraction == null) return null;

  // Deliberately covers all three fields even though only the first has UI in
  // this subtask: the panel's job is "is there anything worth showing", which is
  // a property of the whole extraction, not of whichever field is wired up yet.
  const hasSubmissionNumber = isPresent(extraction.submission_number);
  const hasForumId = isPresent(extraction.openreview_forum_id);
  const hasAuthors = extraction.authors.some(
    (author) =>
      isPresent(author.name) ||
      isPresent(author.email) ||
      isPresent(author.affiliation)
  );

  if (!hasSubmissionNumber && !hasForumId && !hasAuthors) return null;

  // Trimmed because it goes into a URL: the backend passes identifiers through
  // unvalidated, so stray whitespace would otherwise become %20 in the href.
  // encodeURIComponent is a no-op for a well-formed id ([A-Za-z0-9]{10}) and
  // keeps a malformed one from breaking out of the query parameter.
  const forumId = extraction.openreview_forum_id?.trim() ?? "";
  const forumUrl = `${OPENREVIEW_FORUM_URL}?id=${encodeURIComponent(forumId)}`;

  return (
    <div
      role="group"
      aria-label="Submission details"
      className={cn("rounded-lg px-3 py-2", className)}
      style={{ backgroundColor: "var(--surface)" }}
    >
      <h3
        className="text-xs font-semibold uppercase tracking-wide"
        style={{ color: "var(--text-muted)" }}
      >
        Submission Details
      </h3>

      {/* Two-column grid rather than a flex row per field: the label column
          auto-sizes to the widest label, so values stay aligned as rows are
          added (authors in 2c) without hand-tuned widths. */}
      <div className="mt-1.5 grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-1">
        {hasSubmissionNumber && (
          <>
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>
              Number
            </span>
            {/* tabular-nums so ids line up when this sits near the ticket badge. */}
            <span
              className="text-sm font-semibold tabular-nums"
              style={{ color: "var(--text-primary)" }}
            >
              {extraction.submission_number}
            </span>
          </>
        )}

        {hasForumId && (
          <>
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>
              OpenReview
            </span>
            <a
              href={forumUrl}
              target="_blank"
              rel="noopener noreferrer"
              /* "(opens in new tab)" suffix matches ZendeskLinkButton, the
                 external-link convention this component already follows. The id
                 is named because on its own it is an opaque string that tells a
                 screen-reader user nothing; it stays a prefix of the visible
                 text, so the accessible name still contains the label. */
              aria-label={`Open submission ${forumId} in OpenReview (opens in new tab)`}
              className={cn(
                "inline-flex w-fit items-center gap-1 text-sm font-semibold",
                "text-[var(--accent)] hover:underline",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--background)]"
              )}
            >
              {forumId}
              {/* Inherits currentColor from the <a>; aria-hidden because the
                  label already says the link opens in a new tab. */}
              <ExternalLink className="h-3 w-3" aria-hidden />
            </a>
          </>
        )}
      </div>
    </div>
  );
}
