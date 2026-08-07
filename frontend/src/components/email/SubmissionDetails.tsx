import { cn } from "@/lib/utils";
import type { ExtractionData } from "@/types";

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
 * (2a: submission_number only. The OpenReview link is 2b and authors are 2c —
 * but the emptiness check below already considers all three, so neither of those
 * subtasks has to revisit this component's render/don't-render decision.)
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

      {hasSubmissionNumber && (
        <div className="mt-1.5 flex items-center gap-3">
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
        </div>
      )}
    </div>
  );
}
