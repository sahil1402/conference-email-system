"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ExternalLink, FileQuestion, Info } from "lucide-react";

import { useEmailById } from "@/hooks";
import { EmptyState, ErrorBanner, LoadingSpinner } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Email } from "@/types";

const FIELD_STYLE = {
  backgroundColor: "var(--surface)",
  borderColor: "var(--border)",
  color: "var(--text-primary)",
} as const;

/** Deep link to the exact comment this email replies to, or null.
 *
 * Shape confirmed against the backend's own extractor, which documents it as
 * `openreview.net/forum?id=ll0avn6ylq&noteId=jnHgRMHgrm` — the forum id selects
 * the paper's discussion, the noteId anchors to one comment inside it.
 *
 * BOTH ids are required. A forum-only link would open the discussion at the top
 * and leave the chair hunting for which comment this answers, which is exactly
 * the ambiguity the note id exists to remove — so the link is withheld rather
 * than degraded. Extraction guarantees the pair came from the SAME link, so
 * they can be combined without re-checking that they belong together. */
// NOT exported: a Next.js App Router `page.tsx` may only export `default`
// plus a fixed set of framework names, and a stray named export fails the
// production build (`next build`) while passing tsc, lint AND the tests.
function openReviewCommentUrl(email: Email): string | null {
  const forumId = email.extraction?.openreview_forum_ids?.[0];
  const noteId = email.extraction?.openreview_note_id;
  if (!forumId || !noteId) return null;
  return `https://openreview.net/forum?id=${forumId}&noteId=${noteId}`;
}

/** Read-only context line: label above value. */
function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span
        className="text-xs font-medium uppercase tracking-wide"
        style={{ color: "var(--text-muted)" }}
      >
        {label}
      </span>
      <span
        className="truncate text-sm"
        style={{ color: "var(--text-primary)" }}
      >
        {value}
      </span>
    </div>
  );
}

/**
 * Review one detected OpenReview reply before relaying it back to the
 * discussion: the email's context, a link to the comment being answered, and
 * the reply text in an editable box.
 *
 * ⚠️ NOTHING HERE POSTS. The actions that relay the reply and resolve the
 * ticket are a separate piece; this page deliberately renders NO action buttons
 * at all rather than disabled ones — see the note above the editor.
 */
export default function OpenReviewReplyDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const { id } = params;
  const { email, isLoading, isError, error, refetch } = useEmailById(id);

  const [replyText, setReplyText] = useState("");

  // Adopt the server's text once it arrives, and again if a later poll changes
  // it — but keyed on the FETCHED value, not on every render, so a chair's
  // in-progress edits are never clobbered by the 15s refetch returning the same
  // string it returned before.
  const serverText = email?.extraction?.extracted_reply_text ?? "";
  useEffect(() => {
    setReplyText(serverText);
  }, [serverText]);

  // A non-numeric id 404s at the backend just like a missing row, so unlike
  // /tickets/[ticketId] there is no 422 case to fold in — every failure that is
  // not a 404 is genuinely unexpected.
  const isNotFound = isError && error?.status === 404;

  return (
    <div className="mx-auto w-full max-w-4xl px-8 py-10">
      <Link
        href="/openreview-replies"
        className="mb-6 inline-flex items-center gap-1.5 text-sm transition-colors hover:text-[var(--accent)]"
        style={{ color: "var(--text-secondary)" }}
      >
        <ArrowLeft className="h-4 w-4" aria-hidden />
        Back to OpenReview Replies
      </Link>

      {isLoading ? (
        <div className="flex items-center justify-center py-20">
          <LoadingSpinner size="lg" />
        </div>
      ) : isNotFound ? (
        <EmptyState
          icon={<FileQuestion className="h-6 w-6" />}
          title="That reply doesn't exist"
          description="No email matches this id. It may have been removed, or the link may be wrong."
        />
      ) : isError ? (
        <ErrorBanner
          message="Couldn't load this reply."
          onRetry={() => refetch()}
        />
      ) : email ? (
        <>
          <header className="mb-8 flex flex-col gap-1">
            <h1
              className="text-2xl font-semibold tracking-tight"
              style={{ color: "var(--text-primary)" }}
            >
              {email.subject || "(no subject)"}
            </h1>
          </header>

          {/* Context the chair needs to judge the reply. */}
          <section
            className="mb-6 grid grid-cols-1 gap-4 rounded-lg border p-4 sm:grid-cols-3"
            style={{
              borderColor: "var(--border)",
              backgroundColor: "var(--surface-raised)",
            }}
          >
            <Field
              label="From"
              value={email.sender_name?.trim() || email.sender}
            />
            <Field label="Received" value={formatDateTime(email.received_at)} />
            <Field label="Paper" value={paperValue(email)} />
          </section>

          {/* The comment being answered. */}
          <section className="mb-6">
            <OpenReviewCommentLink email={email} />
          </section>

          {/* The reply itself — editable, because the chair may adjust it
              before it is relayed. */}
          <section className="flex flex-col gap-2">
            <label className="flex flex-col gap-2">
              <span
                className="text-xs font-medium uppercase tracking-wide"
                style={{ color: "var(--text-muted)" }}
              >
                Reply to post
              </span>
              <textarea
                value={replyText}
                onChange={(e) => setReplyText(e.target.value)}
                rows={10}
                aria-label="Reply to post to OpenReview"
                placeholder="No reply text was extracted from this email — write the reply to post."
                className="w-full resize-y rounded-lg border px-3 py-2 text-sm leading-relaxed outline-none focus:border-[var(--accent)]"
                style={FIELD_STYLE}
              />
            </label>

            {serverText.trim() === "" && (
              // A genuinely empty extraction is a REAL state, not a failure: the
              // body was entirely quoted material, so quote-stripping correctly
              // left nothing. Said plainly, so a chair reads it as "there is
              // nothing here yet" rather than "this page is broken".
              <p
                className="flex items-start gap-1.5 text-xs"
                style={{ color: "var(--text-secondary)" }}
              >
                <Info className="h-3.5 w-3.5 shrink-0 translate-y-px" aria-hidden />
                No reply text could be extracted — the email appears to be
                entirely quoted content. Check the original before posting.
              </p>
            )}

            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              {/* Stated in the UI, not just in a code comment: a chair who types
                  here and finds no way to submit should know that is expected
                  rather than assume the page failed. */}
              Posting this reply isn&apos;t wired up yet — edits here are not
              saved.
            </p>
          </section>
        </>
      ) : null}
    </div>
  );
}

/** The paper this reply concerns, preferring the number a chair recognises. */
function paperValue(email: Email): string {
  const numbers = email.extraction?.submission_numbers ?? [];
  if (numbers.length > 0) return `Submission ${numbers[0]}`;
  const forums = email.extraction?.openreview_forum_ids ?? [];
  return forums.length > 0 ? `Forum ${forums[0]}` : "—";
}

/**
 * Link to the OpenReview comment being replied to.
 *
 * Styled after `ZendeskLinkButton` — the app's existing external-link
 * convention: a bordered pill that shifts to the accent on hover so it reads as
 * an action rather than a status, opening in a new tab.
 *
 * When either id is missing it says so instead of rendering nothing. A silently
 * absent link looks identical to a link that failed to render, and here the
 * absence is information: without the pair there is no specific comment to
 * point at.
 */
function OpenReviewCommentLink({ email }: { email: Email }) {
  const url = openReviewCommentUrl(email);

  if (url == null) {
    return (
      <p className="text-xs" style={{ color: "var(--text-muted)" }}>
        No link to the original comment — this email didn&apos;t carry both a
        forum id and a note id.
      </p>
    );
  }

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      aria-label="Open the original comment on OpenReview (opens in new tab)"
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1.5 text-xs font-medium leading-none transition-colors",
        "border-[var(--border)] bg-[var(--surface-raised)] text-[var(--text-secondary)]",
        "hover:border-[var(--accent)] hover:bg-[var(--accent-subtle)] hover:text-[var(--accent)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--background)]"
      )}
    >
      <ExternalLink className="h-3.5 w-3.5" aria-hidden />
      View the original comment on OpenReview
    </a>
  );
}
