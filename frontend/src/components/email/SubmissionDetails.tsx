import { Fragment, type ReactNode } from "react";
import { ExternalLink } from "lucide-react";

import { cn } from "@/lib/utils";
import type { AuthorMention, ExtractionData } from "@/types";

/** Public forum page for a submission, keyed by its OpenReview forum id. */
const OPENREVIEW_FORUM_URL = "https://openreview.net/forum";

/**
 * Ties the group's accessible name to its visible heading. A module constant
 * (not useId) matching PolicyDetailModal's `titleId`: one email detail renders
 * at a time, so a second instance on the same page — which would duplicate this
 * id — is not a case the app produces.
 */
const TITLE_ID = "submission-details-title";

interface SubmissionDetailsProps {
  /**
   * The email's `extraction` (Email.extraction). Null when the row was never
   * examined — see ExtractionData for why that differs from an examined result
   * that found nothing. Both render nothing here.
   */
  extraction: ExtractionData | null;
  className?: string;
}

/**
 * A value counts as present only if it is a non-blank string.
 *
 * Typed as a predicate so a guarded field narrows to `string`, which is what
 * lets the call sites below read `author.name` without a non-null assertion.
 */
function isPresent(value: string | null | undefined): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

/**
 * One person, as their populated fields joined by the app's middot separator.
 *
 * Every field is independently optional, so the parts are collected first and
 * only then joined — that way a blank field contributes no text AND no stray
 * separator, and a mention with nothing populated renders nothing at all rather
 * than an empty line.
 *
 * The email is deliberately NOT a mailto link. The directly comparable field —
 * the requester's own address in the detail header — is plain text, and the only
 * mailto in this codebase is inside ConversationThread's generic linkifier for
 * untrusted message prose, not a structured field. More importantly, replies
 * here are meant to travel through Zendesk: a mailto invites answering
 * out-of-band, which loses the ticket's audit trail and the ai_drafted /
 * ai_auto_replied tagging that the send path applies.
 */
function AuthorLine({ author }: { author: AuthorMention }) {
  const parts: { key: string; text: string; primary: boolean }[] = [];
  if (isPresent(author.name)) {
    parts.push({ key: "name", text: author.name.trim(), primary: true });
  }
  if (isPresent(author.email)) {
    parts.push({ key: "email", text: author.email.trim(), primary: false });
  }
  if (isPresent(author.affiliation)) {
    parts.push({
      key: "affiliation",
      text: author.affiliation.trim(),
      primary: false,
    });
  }
  if (parts.length === 0) return null;

  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-1.5 text-sm">
      {parts.map((part, index) => (
        <Fragment key={part.key}>
          {/* aria-hidden: the parts are separate elements, so assistive tech
              already separates them — the middot would just be read as noise.
              Spacing comes from the flex gap, not from text, so hiding it
              cannot run the words together. */}
          {index > 0 && (
            <span aria-hidden style={{ color: "var(--text-muted)" }}>
              ·
            </span>
          )}
          <span
            className={part.primary ? "font-medium" : undefined}
            style={{
              color: part.primary
                ? "var(--text-primary)"
                : "var(--text-secondary)",
            }}
          >
            {part.text}
          </span>
        </Fragment>
      ))}
    </span>
  );
}

/**
 * Several short values in ONE grid cell: wrapping, middot-separated.
 *
 * Reuses AuthorLine's separator idiom — a muted `aria-hidden` middot with
 * spacing from the flex gap, so assistive tech reads the values as separate
 * elements without announcing the punctuation. Inline (not stacked like the
 * authors column) because each value here is a single atomic token, so there is
 * no internal structure a separator could be confused with.
 */
function InlineValueList({ children }: { children: ReactNode[] }) {
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-1.5">
      {children.map((child, index) => (
        <Fragment key={index}>
          {index > 0 && (
            <span aria-hidden style={{ color: "var(--text-muted)" }}>
              ·
            </span>
          )}
          {child}
        </Fragment>
      ))}
    </span>
  );
}

/**
 * Which submissions an email refers to, and who it names: the submission
 * numbers, links to their OpenReview forum pages, and the people it identifies.
 *
 * Owns its own "render nothing when there is nothing to show" logic, so callers
 * can pass the raw nullable `extraction` without a surrounding conditional —
 * same contract as ZendeskLinkButton.
 *
 * Structure: the inline Intent-row idiom (surface-tinted `rounded-lg`, uppercase
 * muted label + value), NOT a Collapsible card. Collapsibles here exist to
 * bound the vertical cost of an unbounded list — Policy Citations renders N
 * policy cards, Previous drafts renders N drafts. This panel holds at most three
 * rows, so a disclosure control would hide already-cheap content behind a click
 * and add a box around almost nothing.
 */
export function SubmissionDetails({
  extraction,
  className,
}: SubmissionDetailsProps) {
  if (extraction == null) return null;

  // The panel's job is "is there anything worth showing", which is a property of
  // the whole extraction — so every field is consulted, and each one that counts
  // here also has a row below. An extraction that passes this guard can never
  // render as a bare title with no content.
  // Blank entries are stripped and values trimmed defensively. The extractor
  // already does both, but one slipping through would render as an empty value
  // or, worse, a link whose href carries %20. Emptiness is then derived from the
  // CLEANED lists, so a list of nothing but blanks correctly counts as empty.
  const submissionNumbers = extraction.submission_numbers
    .filter(isPresent)
    .map((value) => value.trim());
  const forumIds = extraction.openreview_forum_ids
    .filter(isPresent)
    .map((value) => value.trim());

  const hasSubmissionNumbers = submissionNumbers.length > 0;
  const hasForumIds = forumIds.length > 0;
  const hasAuthors = extraction.authors.some(
    (author) =>
      isPresent(author.name) ||
      isPresent(author.email) ||
      isPresent(author.affiliation)
  );

  if (!hasSubmissionNumbers && !hasForumIds && !hasAuthors) return null;

  return (
    <div
      role="group"
      /* Named by the visible heading rather than a duplicate aria-label string,
         so the accessible name has one source of truth and cannot drift from
         what is on screen. */
      aria-labelledby={TITLE_ID}
      className={cn("rounded-lg px-3 py-2", className)}
      style={{ backgroundColor: "var(--surface)" }}
    >
      <h3
        id={TITLE_ID}
        className="text-xs font-semibold uppercase tracking-wide"
        style={{ color: "var(--text-muted)" }}
      >
        Submission Details
      </h3>

      {/* Two-column grid rather than a flex row per field: the label column
          auto-sizes to the widest label, so the value column stays aligned
          across all three rows without hand-tuned widths. */}
      <div className="mt-1.5 grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-1">
        {hasSubmissionNumbers && (
          <>
            {/* Pluralised from the count rather than a fixed "Number(s)":
                several submissions per email are now routine, not an edge case,
                and the grid's label column auto-sizes so the width change costs
                nothing. */}
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>
              {submissionNumbers.length === 1 ? "Number" : "Numbers"}
            </span>
            <InlineValueList>
              {submissionNumbers.map((number) => (
                /* tabular-nums so ids line up with each other and with the
                   ticket badge nearby. */
                <span
                  key={number}
                  className="text-sm font-semibold tabular-nums"
                  style={{ color: "var(--text-primary)" }}
                >
                  {number}
                </span>
              ))}
            </InlineValueList>
          </>
        )}

        {hasForumIds && (
          <>
            {/* Not pluralised: "OpenReview" names the destination, not a count,
                so it reads correctly for any number of links. */}
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>
              OpenReview
            </span>
            <InlineValueList>
              {forumIds.map((id) => (
                /* One link PER id — they are different papers (ids are
                   case-sensitive), so a single combined link would be wrong. */
                <a
                  key={id}
                  href={`${OPENREVIEW_FORUM_URL}?id=${encodeURIComponent(id)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  /* "(opens in new tab)" suffix matches ZendeskLinkButton, the
                     external-link convention this component already follows.
                     Each link names ITS OWN id: a shared generic label would
                     leave a screen-reader user unable to tell several links
                     apart, and the id is otherwise an opaque string. It stays a
                     prefix of the visible text, so the accessible name still
                     contains the label. */
                  aria-label={`Open submission ${id} in OpenReview (opens in new tab)`}
                  className={cn(
                    "inline-flex items-center gap-1 text-sm font-semibold",
                    "text-[var(--accent)] hover:underline",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--background)]"
                  )}
                >
                  {id}
                  {/* Inherits currentColor from the <a>; aria-hidden because the
                      label already says the link opens in a new tab. */}
                  <ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              ))}
            </InlineValueList>
          </>
        )}

        {hasAuthors && (
          <>
            {/* `self-start` on both cells: the grid centres its rows, which
                would float this label in the middle of a multi-person stack.
                Number/OpenReview stay centred — only this row opts out. */}
            {/* NOTE: on the regex-fallback path this is only the email's
                sender, who is not necessarily a verified author of the paper —
                "Authors" is the backend's field name, not a guarantee. */}
            <span
              className="self-start text-xs"
              style={{ color: "var(--text-muted)" }}
            >
              Authors
            </span>
            {/* Stacked one per line rather than comma-joined inline. Each entry
                is itself composite (up to three fields already joined by a
                middot), so an inline list would need two separator levels and a
                reader could not tell where one person ends. Stacking keeps the
                grid intact — the label column still aligns and the value column
                still starts at the same x — only this cell's height varies,
                which is exactly what a grid row handles for free. */}
            <div className="flex flex-col gap-0.5 self-start">
              {extraction.authors.map((author, index) => (
                <AuthorLine key={index} author={author} />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
