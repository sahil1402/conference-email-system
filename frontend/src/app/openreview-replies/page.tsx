"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { MessageSquareReply } from "lucide-react";

import { useOpenReviewQueue } from "@/hooks";
import { QUEUE_PAGE_SIZE } from "@/lib/api";
import {
  EmptyState,
  ErrorBanner,
  LoadingSpinner,
} from "@/components/ui";
import { Pagination } from "@/components/email/Pagination";
import { initials, timeAgo } from "@/lib/format";
import type { Email } from "@/types";

/** Longest reply preview rendered before an ellipsis.
 *
 * A cap, not a layout device: `truncate` already clips to one line visually, but
 * an un-capped string still ships the WHOLE reply into the DOM and into the
 * accessible name of every row. Cutting at a readable length keeps the row's
 * text content close to what a chair actually sees. */
const PREVIEW_CHARS = 160;

function preview(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > PREVIEW_CHARS
    ? `${flat.slice(0, PREVIEW_CHARS).trimEnd()}…`
    : flat;
}

/** The paper this reply belongs to, as a short label, or null.
 *
 * Prefers the submission NUMBER (what a chair recognises) over the forum id (an
 * opaque 10-character token). Both are lists — an email may name several — so
 * the first is shown and the rest are left to the detail view. */
function paperLabel(email: Email): string | null {
  const numbers = email.extraction?.submission_numbers ?? [];
  if (numbers.length > 0) return `Submission ${numbers[0]}`;
  const forums = email.extraction?.openreview_forum_ids ?? [];
  return forums.length > 0 ? `Forum ${forums[0]}` : null;
}

/**
 * One row of the OpenReview queue.
 *
 * Shows the EXTRACTED reply text, never `email.body` — the raw body still has
 * the entire quoted notification underneath the reply, so rendering it would
 * bury the two lines the chair needs and defeat the point of the quote
 * stripping. Kept local to this page: it is page-specific presentation, and the
 * shared `EmailListItem` carries lane/confidence chrome that means nothing here.
 */
function OpenReviewRow({ email, onOpen }: { email: Email; onOpen: () => void }) {
  const paper = paperLabel(email);
  const reply = preview(email.extraction?.extracted_reply_text ?? "");

  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full items-start gap-3 px-3 py-3 text-left transition-colors duration-150 hover:bg-[var(--surface-raised)]"
      style={{ borderLeft: "3px solid var(--accent)" }}
    >
      <span
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
        style={{
          backgroundColor: "var(--surface-raised)",
          color: "var(--text-secondary)",
        }}
      >
        {initials(email.sender_name, email.sender)}
      </span>

      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex min-w-0 items-baseline gap-2">
          <span
            className="truncate text-sm font-semibold"
            style={{ color: "var(--text-primary)" }}
          >
            {email.subject || "(no subject)"}
          </span>
          {paper && (
            <span
              className="shrink-0 text-xs tabular-nums"
              style={{ color: "var(--text-muted)" }}
            >
              {paper}
            </span>
          )}
        </span>

        {/* The reply itself — the reason this queue exists, so it gets the most
            room and sits directly under the subject. */}
        <span
          className="truncate text-sm"
          style={{ color: "var(--text-secondary)" }}
        >
          {reply || "(no reply text extracted)"}
        </span>

        <span className="truncate text-xs" style={{ color: "var(--text-muted)" }}>
          {(email.sender_name?.trim() || email.sender)} ·{" "}
          {timeAgo(email.received_at ?? email.created_at)}
        </span>
      </span>
    </button>
  );
}

/**
 * OpenReview Replies — replies detected as answers to an OpenReview
 * notification, waiting to be relayed back to the discussion.
 *
 * The backend routes these OUT of the main queue and serves them here, so the
 * two lists are complements: an email is in exactly one of them.
 */
export default function OpenReviewRepliesPage() {
  const router = useRouter();
  const [page, setPage] = useState(1);

  const offset = (page - 1) * QUEUE_PAGE_SIZE;
  const { emails, total, isLoading, isError, refetch } = useOpenReviewQueue({
    limit: QUEUE_PAGE_SIZE,
    offset,
  });

  const pageCount = Math.max(1, Math.ceil(total / QUEUE_PAGE_SIZE));

  // Correct a page that no longer exists — relaying a reply removes it from
  // this queue, so the set shrinks under the 15s poll.
  //
  // Gated on `total > 0`, NOT `!isLoading`: with placeholder data React Query
  // reports success mid-transition, so `isLoading` no longer means "count
  // unknown", and a transient 0 total would read as "1 page" and yank the chair
  // back to page 1. Same guard, same reason, as EmailWorkspace's.
  const hasCount = total > 0;
  useEffect(() => {
    if (hasCount && page > pageCount) setPage(pageCount);
  }, [hasCount, page, pageCount]);

  return (
    <div className="mx-auto w-full max-w-6xl px-8 py-10">
      <header className="mb-8 flex flex-col gap-1">
        <h1
          className="text-2xl font-semibold tracking-tight"
          style={{ color: "var(--text-primary)" }}
        >
          OpenReview Replies
        </h1>
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          Replies to OpenReview notifications, ready to be posted back to the
          discussion
        </p>
      </header>

      {isLoading ? (
        <div className="flex items-center justify-center py-20">
          <LoadingSpinner size="lg" />
        </div>
      ) : isError ? (
        <ErrorBanner
          message="Couldn't load OpenReview replies."
          onRetry={() => refetch()}
        />
      ) : total === 0 ? (
        <EmptyState
          icon={<MessageSquareReply className="h-5 w-5" />}
          title="No OpenReview reply candidates right now"
          description="When a reviewer or author replies to an OpenReview notification and it lands in the chair inbox, it appears here to be posted back to the discussion."
        />
      ) : (
        <>
          <ul
            style={{
              borderTop: "1px solid var(--border-subtle)",
              borderBottom: "1px solid var(--border-subtle)",
            }}
          >
            {emails.map((email) => (
              <li
                key={email.id}
                style={{ borderBottom: "1px solid var(--border-subtle)" }}
              >
                <OpenReviewRow
                  email={email}
                  onOpen={() => router.push(`/openreview-replies/${email.id}`)}
                />
              </li>
            ))}
          </ul>

          <div className="mt-6 flex justify-center">
            <Pagination
              page={page}
              pageCount={pageCount}
              onPageChange={setPage}
            />
          </div>
        </>
      )}
    </div>
  );
}
