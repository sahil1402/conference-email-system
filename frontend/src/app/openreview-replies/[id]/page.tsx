"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ExternalLink,
  FileQuestion,
  Info,
  Users,
} from "lucide-react";

import {
  useDismissOpenReviewCandidate,
  useEmailById,
  usePostOpenReviewReply,
  useSetEmailStatus,
} from "@/hooks";
import { EmptyState, ErrorBanner, LoadingSpinner } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  ApiError,
  Email,
  OpenReviewReaders,
  OpenReviewVisibility,
  PostOpenReviewReplyResponse,
} from "@/types";

/** The reply's audience. Mirrors the backend's `visibility` request field. */
type Visibility = OpenReviewVisibility;

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
  const [visibility, setVisibility] = useState<Visibility>("public");
  const post = usePostOpenReviewReply();

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

  const submissionNumber = email ? submissionNumberFor(email) : null;
  const trimmedReply = replyText.trim();
  // ⚠️ EVERY REASON THE BUTTON IS DISABLED IS ALSO A SENTENCE ON SCREEN. A
  // silently dead submit on the one page whose whole purpose is submitting is
  // indistinguishable from a broken page, so each guard names itself below.
  const blockedReason =
    trimmedReply === ""
      ? "Write the reply before posting it."
      : submissionNumber === null
        ? "No submission number was extracted from this email, so there is no " +
          "OpenReview discussion to post under."
        : null;

  function handlePost() {
    if (blockedReason !== null || submissionNumber === null) return;
    post.mutate({
      id,
      data: {
        // The EDITED text, read straight off the box the chair is looking at —
        // never `serverText`, which is what the extractor produced before any
        // edit. The backend posts this verbatim.
        reply_text: replyText,
        submission_number: submissionNumber,
        visibility,
      },
    });
  }

  const alreadyPosted = isAlreadyPosted(post.error);

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

          {/* A FACT about the parent note: who can see the comment being
              answered. Does not move when the toggle does — the original
              comment's audience is not something this page changes. */}
          <OriginalCommentAudience readers={email.openreview_readers} />

          {/* The CONSEQUENCE of the chair's choice, kept visually and
              structurally separate from the fact above. */}
          <VisibilityChoice
            value={visibility}
            onChange={setVisibility}
            readers={email.openreview_readers}
          />

          {post.isSuccess ? (
            <PostedPanel
              result={post.data}
              commentUrl={openReviewCommentUrl(email)}
            />
          ) : (
          /* The reply itself — editable, because the chair may adjust it
             before it is relayed. */
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

            {alreadyPosted ? (
              // Not an error, and deliberately not styled as one. The chair
              // asked for something that is already true — most often a
              // double-click or a revisit of a handled item — and a red banner
              // would read as "your action failed" when in fact it succeeded,
              // just not now.
              <div
                className="flex items-start gap-2 rounded-lg border p-3 text-sm"
                style={{
                  borderColor: "var(--border)",
                  backgroundColor: "var(--surface-raised)",
                  color: "var(--text-primary)",
                }}
                role="status"
              >
                <CheckCircle2
                  className="h-4 w-4 shrink-0 translate-y-0.5"
                  style={{ color: "var(--success)" }}
                  aria-hidden
                />
                <span>
                  This reply is already on OpenReview — it was posted earlier,
                  so nothing was sent again.
                </span>
              </div>
            ) : (
              post.isError && (
                <ErrorBanner message={postFailureMessage(post.error)} />
              )
            )}

            <div className="flex items-center gap-3">
              <Button
                type="button"
                onClick={handlePost}
                // `isPending` is in the disabled set, so a double-click cannot
                // fire a second request: the idempotency gate is the backstop
                // for a revisit, not for the button's own behaviour.
                disabled={post.isPending || blockedReason !== null}
                aria-busy={post.isPending}
              >
                {post.isPending && (
                  <LoadingSpinner size="sm" className="!text-white" />
                )}
                {post.isPending ? "Posting…" : "Approve & Post"}
              </Button>

              {blockedReason && (
                <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                  {blockedReason}
                </span>
              )}
            </div>

            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              {/* Said plainly before the fact, because the action cannot be
                  undone from this page — or from anywhere in this app. */}
              Posting publishes this comment on OpenReview. It can&apos;t be
              undone here.
            </p>

            {/* ⚠️ HIDDEN ONCE THE REPLY IS ALREADY ON OPENREVIEW — see
                NotAnOpenReviewReply. Rendered last: it is the escape hatch for
                the minority of emails that do not belong here at all, and
                should not compete with the action most of them need. */}
            {!alreadyPosted && <NotAnOpenReviewReply email={email} />}
          </section>
          )}
        </>
      ) : null}
    </div>
  );
}

/**
 * The longest shared `/`-separated prefix across reader ids, or `""`.
 *
 * Used ONLY to decide what to de-emphasise visually — never to shorten, rename
 * or hide anything. Every group id is rendered in full; the shared venue head
 * is just drawn in a muted colour so the eye lands on the part that differs.
 *
 * The `p.length > i + 1` guard is the mechanism that matters: it stops the scan
 * one segment before the end of the SHORTEST id, so every reader always keeps at
 * least one distinguishing segment at full weight — even for two identical ids,
 * which would otherwise be dimmed into two blank-looking rows.
 *
 * NOT exported — same constraint as `openReviewCommentUrl` below: an App Router
 * `page.tsx` may only export `default` plus a fixed set of framework names, and
 * a stray named export fails `next build` while passing tsc, lint AND the tests.
 */
function commonGroupPrefix(readers: string[]): string {
  if (readers.length < 2) return "";
  const parts = readers.map((r) => r.split("/"));
  const shared: string[] = [];
  for (let i = 0; i < parts[0].length; i += 1) {
    const segment = parts[0][i];
    // Bounding the loop at `parts[0].length - 1` as well was tried and removed:
    // it provably never changes the result, because this guard already stops the
    // scan first on every input. A redundant bound reads as a second safeguard
    // and would send the next reader looking for the case that needs it.
    const allMatch = parts.every((p) => p.length > i + 1 && p[i] === segment);
    if (!allMatch) break;
    shared.push(segment);
  }
  return shared.length > 0 ? `${shared.join("/")}/` : "";
}

/**
 * One OpenReview group id.
 *
 * ⚠️ RENDERED VERBATIM. These are ugly — `AAAI.org/2027/Submission1030/Reviewers`
 * — and the temptation is to prettify them into "Reviewers". That is not done
 * anywhere here, on purpose: a friendly label is a CLAIM about who is included,
 * and this page is where a chair decides who may read something. A relabelling
 * that is subtly wrong (Reviewers vs Area Chairs vs Senior Area Chairs, all of
 * which exist in a real venue) would misinform exactly the decision it decorates.
 *
 * So the id is shown complete, monospaced, and selectable; only the shared venue
 * prefix is dimmed. Nothing is removed, so nothing can be misread.
 *
 * ⚠️ THE COMPLETENESS IS STRUCTURAL, NOT A CONVENTION TO UPHOLD: `head` is only
 * ever a prefix `id` actually starts with, and `tail` is the remainder by
 * `slice`, so `head + tail === id` for every input — including a prefix computed
 * wrongly, or not at all. `commonGroupPrefix` can therefore only change how much
 * of the id is dimmed; it cannot drop, reorder or truncate a character of it.
 * That is what makes the dimming safe to do at all on a page about disclosure.
 */
function ReaderGroup({ id, prefix }: { id: string; prefix: string }) {
  const head = prefix && id.startsWith(prefix) ? prefix : "";
  const tail = head ? id.slice(head.length) : id;

  return (
    <li
      className="inline-flex max-w-full items-center rounded-md border px-2 py-1 font-mono text-xs"
      style={{
        borderColor: "var(--border)",
        backgroundColor: "var(--surface)",
      }}
    >
      <span className="truncate">
        {head && <span style={{ color: "var(--text-muted)" }}>{head}</span>}
        <span style={{ color: "var(--text-primary)" }}>{tail}</span>
      </span>
    </li>
  );
}

/**
 * Who can currently see the OpenReview comment being replied to.
 *
 * Three states, handled as three genuinely different things:
 *
 * - `not_applicable` → renders NOTHING. There is no parent comment, so there is
 *   no audience, and an empty panel would imply one exists and is empty.
 *   ⚠️ This IS reachable here, not merely defensive — see the note in the
 *   component body.
 * - `failed` → says so, and shows the error. Never a blank list: "we could not
 *   find out" and "nobody can see it" are opposite facts and must not share a
 *   rendering.
 * - `fetched` → the live list, verbatim. An EMPTY list is a real answer and gets
 *   its own wording, distinct from the failure above.
 */
function OriginalCommentAudience({
  readers,
}: {
  readers: OpenReviewReaders | undefined;
}) {
  // Absent entirely: an older backend, or a response shape that predates the
  // field. Treated like not-applicable rather than guessed at.
  if (!readers || readers.state === "not_applicable") return null;

  const heading = (
    <span
      className="inline-flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide"
      style={{ color: "var(--text-muted)" }}
    >
      <Users className="h-3.5 w-3.5" aria-hidden />
      Who can see the original comment
    </span>
  );

  if (readers.state === "failed") {
    return (
      <section
        className="mb-6 flex flex-col gap-2 rounded-lg border p-4"
        style={{
          borderColor: "var(--border)",
          backgroundColor: "var(--surface-raised)",
        }}
      >
        {heading}
        <p
          className="flex items-start gap-1.5 text-sm"
          style={{ color: "var(--text-secondary)" }}
        >
          <AlertTriangle
            className="h-4 w-4 shrink-0 translate-y-0.5"
            style={{ color: "var(--warning)" }}
            aria-hidden
          />
          <span>
            Couldn&apos;t confirm the audience — OpenReview didn&apos;t answer.
            The reply can still be posted; it will reach whoever can see the
            original comment.
          </span>
        </p>
        {readers.error && (
          <p className="pl-6 font-mono text-xs" style={{ color: "var(--text-muted)" }}>
            {readers.error}
          </p>
        )}
      </section>
    );
  }

  const list = readers.readers ?? [];
  const prefix = commonGroupPrefix(list);

  return (
    <section
      className="mb-6 flex flex-col gap-2 rounded-lg border p-4"
      style={{
        borderColor: "var(--border)",
        backgroundColor: "var(--surface-raised)",
      }}
    >
      {heading}
      {list.length === 0 ? (
        // Fetched, and genuinely empty. Worded so it cannot be read as the
        // failure above: we DID find out, and the answer was "no groups".
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          OpenReview lists no reader groups on this comment.
        </p>
      ) : (
        <ul
          aria-label="Reader groups on the original comment"
          className="flex flex-wrap gap-1.5"
        >
          {list.map((groupId) => (
            <ReaderGroup key={groupId} id={groupId} prefix={prefix} />
          ))}
        </ul>
      )}
    </section>
  );
}

const VISIBILITY_OPTIONS: {
  value: Visibility;
  label: string;
  blurb: string;
}[] = [
  {
    value: "public",
    label: "Public",
    blurb: "Same audience as the original comment.",
  },
  {
    value: "internal",
    label: "Internal",
    blurb: "Only the Program Chairs and this paper's authors.",
  },
];

/**
 * Choose the reply's audience.
 *
 * A RADIO GROUP, not a switch, and that is a correction rather than a
 * preference. The app had a two-state visibility switch on the Zendesk send
 * path and it was removed (2026-08-05) because a switch is a MODE the chair has
 * to remember to set, and a wrong setting was silent in both directions. Here
 * both options are on screen at once, each spelled out, with the selected one
 * marked — there is no state to remember, only a choice to read.
 *
 * Native `<input type="radio">` under an `sr-only` class rather than
 * `aria-checked` buttons: grouping, arrow-key navigation and screen-reader
 * announcement come from the browser instead of from ARIA this file would have
 * to keep correct.
 */
function VisibilityChoice({
  value,
  onChange,
  readers,
}: {
  value: Visibility;
  onChange: (next: Visibility) => void;
  readers: OpenReviewReaders | undefined;
}) {
  return (
    <section className="mb-6 flex flex-col gap-3">
      <fieldset className="flex flex-col gap-2">
        <legend
          className="mb-2 text-xs font-medium uppercase tracking-wide"
          style={{ color: "var(--text-muted)" }}
        >
          Reply visibility
        </legend>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {VISIBILITY_OPTIONS.map((option) => {
            const selected = value === option.value;
            return (
              <label
                key={option.value}
                className={cn(
                  "flex cursor-pointer flex-col gap-1 rounded-lg border p-3 transition-colors",
                  "focus-within:ring-2 focus-within:ring-[var(--accent)]",
                  "focus-within:ring-offset-2 focus-within:ring-offset-[var(--background)]"
                )}
                style={{
                  borderColor: selected ? "var(--accent)" : "var(--border)",
                  backgroundColor: selected
                    ? "var(--accent-subtle)"
                    : "var(--surface-raised)",
                }}
              >
                <input
                  type="radio"
                  name="openreview-reply-visibility"
                  value={option.value}
                  checked={selected}
                  onChange={() => onChange(option.value)}
                  className="sr-only"
                />
                <span
                  className="text-sm font-semibold"
                  style={{
                    color: selected ? "var(--accent)" : "var(--text-primary)",
                  }}
                >
                  {option.label}
                </span>
                <span
                  className="text-xs leading-relaxed"
                  style={{ color: "var(--text-secondary)" }}
                >
                  {option.blurb}
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>

      <AudiencePreview value={value} readers={readers} />
    </section>
  );
}

/**
 * What the current choice means for this reply, in one line.
 *
 * ⚠️ THE INTERNAL BRANCH DELIBERATELY SHOWS NO GROUP IDS. It cannot: the
 * internal audience is `{venue}/Program_Chairs` + `{venue}/Submission{n}/Authors`,
 * built server-side from `OPENREVIEW_VENUE_ID` — backend-only config that never
 * reaches the browser — and from a submission number the backend is given per
 * request. Rendering a guess at those two strings would put fabricated ids in
 * the same monospace pills as the REAL fetched ones above, where they would read
 * as equally authoritative. Plain language is the honest form of an answer this
 * page does not have.
 *
 * The public branch has the opposite property: it can point at the real list,
 * because the real list is right above it.
 */
function AudiencePreview({
  value,
  readers,
}: {
  value: Visibility;
  readers: OpenReviewReaders | undefined;
}) {
  let text: string;

  if (value === "internal") {
    text =
      "Your reply will be visible only to the Program Chairs and this paper's " +
      "authors — narrower than the audience above. OpenReview resolves the " +
      "exact groups when the reply is posted.";
  } else if (readers?.state === "fetched" && readers.readers) {
    const count = readers.readers.length;
    text =
      count === 0
        ? "Your reply will inherit the original comment's audience, which lists no groups."
        : `Your reply will be visible to the ${count} group${
            count === 1 ? "" : "s"
          } listed above.`;
  } else {
    // Failed, not applicable, or absent — the inheritance is still true, the
    // list simply isn't known. Stated without a number rather than with a
    // fabricated one.
    text = "Your reply will inherit the original comment's audience.";
  }

  return (
    <p
      data-testid="reply-audience"
      className="flex items-start gap-1.5 text-xs"
      style={{ color: "var(--text-secondary)" }}
    >
      <Info className="h-3.5 w-3.5 shrink-0 translate-y-px" aria-hidden />
      {text}
    </p>
  );
}

/**
 * Record that this email is NOT a reply to an OpenReview notification.
 *
 * ⚠️ NOT A REROUTE, in wording or in mechanism, and the two must not be
 * conflated. The Inbox's Reroute changes an email's routing LANE and feeds the
 * RL bandit and active learning on the premise that the lane decision was
 * wrong. Here the lane may have been perfectly correct — what was wrong is the
 * text-based detection that read this email as answering an OpenReview
 * notification. The endpoint touches no routing and fires neither signal. The
 * word "reroute" appears nowhere in this control, and a test asserts that.
 *
 * WORDING: "Not an OpenReview reply" states what the CHAIR is asserting, in
 * their own terms. They read the email; they know whether it answers a
 * notification. "Dismiss as false positive" was rejected as system jargon — a
 * false positive is a property of the detector, and a chair should not have to
 * model the detector to use the control.
 *
 * Follows the Inbox's reroute-reason INTERACTION exactly (a toggle opening an
 * inline form, never a modal, so the email stays readable while the reason is
 * written; Confirm disabled until the reason is non-empty) — the shape is the
 * project's existing "action that needs a reason", even though the concept is
 * different.
 */
function NotAnOpenReviewReply({ email }: { email: Email }) {
  const router = useRouter();
  const dismiss = useDismissOpenReviewCandidate();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Already dismissed, reached by a direct link: the list excludes these, so
  // this is a stale URL rather than a normal path. Offering the control again
  // would invite an action that writes nothing.
  if (email.openreview_candidate_dismissed) {
    return (
      <p className="text-xs" style={{ color: "var(--text-muted)" }}>
        This email was already marked as not an OpenReview reply. It appears in
        the main inbox now.
      </p>
    );
  }

  function submit() {
    const trimmed = reason.trim();
    if (trimmed === "" || dismiss.isPending) return;
    dismiss.mutate(
      // `dismissed_by` sent explicitly rather than left to the backend default,
      // matching the Inbox's `approved_by`/`rerouted_by` call sites. The audit
      // entry IS the product of this action — the reason is the feedback signal
      // for tuning the detection — so its actor is not left implicit.
      { id: email.id, data: { reason: trimmed, dismissed_by: "chair" } },
      {
        // Leaves the page, unlike a successful post. The email is no longer a
        // candidate, so this detail route no longer describes it and the list
        // it came from will not contain it on the next fetch. Staying would
        // leave a chair looking at a page about a queue the email has left.
        onSuccess: () => router.push("/openreview-replies"),
      }
    );
  }

  return (
    <div className="mt-2 flex flex-col gap-2">
      {dismiss.isError && (
        <ErrorBanner message={dismissFailureMessage(dismiss.error)} />
      )}

      {open ? (
        <div
          className="flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center"
          style={{
            borderColor: "var(--border)",
            backgroundColor: "var(--surface-raised)",
          }}
        >
          <input
            ref={inputRef}
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            aria-label="Why this isn't an OpenReview reply"
            placeholder="Why isn't this an OpenReview reply?…"
            className="flex-1 rounded-md border px-3 py-1.5 text-sm outline-none transition-colors focus:border-[var(--accent)]"
            style={{
              backgroundColor: "var(--surface)",
              borderColor: "var(--border)",
              color: "var(--text-primary)",
            }}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={dismiss.isPending || reason.trim() === ""}
            aria-busy={dismiss.isPending}
            onClick={submit}
          >
            {dismiss.isPending && (
              <LoadingSpinner size="sm" className="!text-[var(--text-primary)]" />
            )}
            {dismiss.isPending ? "Moving…" : "Confirm"}
          </Button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => {
            setOpen(true);
            requestAnimationFrame(() => inputRef.current?.focus());
          }}
          className="w-fit text-xs underline-offset-4 transition-colors hover:underline"
          style={{ color: "var(--text-secondary)" }}
        >
          Not an OpenReview reply
        </button>
      )}

      {open && (
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Moves it out of this queue and back to the main inbox. Its routing and
          assigned chair are unchanged.
        </p>
      )}
    </div>
  );
}

/** A specific sentence for each way a dismissal can be refused. */
function dismissFailureMessage(error: ApiError): string {
  const detail = errorDetail(error);
  if (error.status === 409) {
    return (
      detail.message ??
      "This email was never detected as an OpenReview reply, so there is " +
        "nothing to dismiss."
    );
  }
  if (error.status === 422) {
    return "A reason is required before this can be recorded.";
  }
  if (error.status === 404) {
    // ⚠️ The one error whose `detail` is a plain string, not an object.
    return error.detail || "This email no longer exists.";
  }
  return (
    detail.message ??
    error.detail ??
    "Couldn't record this. The email is unchanged and still in this queue."
  );
}

/**
 * Recovery for the partial-success case: posted to OpenReview, ticket still open.
 *
 * The action is `POST /emails/{id}/set-status` with `"solved"` — the SAME
 * endpoint and the same `useSetEmailStatus` hook the main Inbox already uses
 * for its mark-solved control. Nothing new is added backend-side: the auto-solve
 * in commit 12 calls `ZendeskSender.set_status_only` directly, and this calls it
 * over HTTP, so a manual retry is genuinely the same write the automatic attempt
 * made — which is why retrying it is a real fix and not a hopeful gesture.
 *
 * ⚠️ THE OPENREVIEW POST IS NOT RE-SENT AND CANNOT BE. That comment is already
 * public; only the Zendesk half is outstanding. Wiring this to anything that
 * touched `/post-openreview-reply` again would be blocked by the idempotency
 * gate at best, and duplicate a public comment at worst.
 */
function SolveRecovery({ emailId }: { emailId: number }) {
  const solve = useSetEmailStatus();

  if (solve.isSuccess) {
    // Reads as the amber block RESOLVING, not as a second success competing
    // with the OpenReview confirmation above: no panel, no border, no second
    // green box — one quiet line completing the state that was incomplete.
    return (
      <p
        className="flex items-center gap-2 text-sm"
        style={{ color: "var(--text-secondary)" }}
        role="status"
      >
        <CheckCircle2
          className="h-4 w-4 shrink-0"
          style={{ color: "var(--success)" }}
          aria-hidden
        />
        Ticket marked solved — this reply is now fully resolved.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div
        className="flex items-start gap-2 rounded-lg border p-3 text-sm"
        style={{
          borderColor: "var(--warning)",
          backgroundColor: "var(--warning-subtle)",
          color: "var(--text-primary)",
        }}
        role="alert"
      >
        <AlertTriangle
          className="h-4 w-4 shrink-0 translate-y-0.5"
          style={{ color: "var(--warning)" }}
          aria-hidden
        />
        <span>
          The reply is posted, but the Zendesk ticket couldn&apos;t be closed
          automatically and is still open.
        </span>
      </div>

      {solve.isError && (
        // Scoped to THIS retry. The OpenReview confirmation above stays exactly
        // where it is and stays true — the post succeeded and nothing here can
        // change that, so an error must not read as though the whole action
        // came undone.
        <ErrorBanner
          message={`Marking the ticket solved failed${
            solve.error?.detail ? `: ${solve.error.detail}` : "."
          } The reply IS posted on OpenReview; only the ticket is still open. Try again, or close it from the ticket itself.`}
        />
      )}

      <Button
        type="button"
        variant="outline"
        onClick={() => solve.mutate({ id: emailId, status: "solved" })}
        // Same in-flight guard as Approve & Post: a disabled button is the
        // structural protection against a double-click, not a hope that the
        // second request is harmless.
        disabled={solve.isPending}
        aria-busy={solve.isPending}
        className="w-fit"
      >
        {solve.isPending && (
          <LoadingSpinner size="sm" className="!text-[var(--text-primary)]" />
        )}
        {solve.isPending ? "Marking solved…" : "Mark solved"}
      </Button>
    </div>
  );
}

/**
 * What happened, once the reply is on OpenReview.
 *
 * Replaces the editor rather than sitting above it. The comment is public and
 * cannot be un-posted from here, so leaving an editable box and a live button
 * on screen would invite a second post of text that no longer matches what is
 * actually on the forum.
 */
function PostedPanel({
  result,
  commentUrl,
}: {
  result: PostOpenReviewReplyResponse;
  commentUrl: string | null;
}) {
  const { openreview_post: post, ticket_resolution: resolution } = result;
  // ⚠️ BRANCHED ON `outcome`, NEVER ON THE TOP-LEVEL `warning`. The backend
  // sets `warning` for every non-`solved` outcome, including both benign skips
  // ("this email has no Zendesk ticket"), so keying on it would raise an alarm
  // about nothing on perfectly complete relays.
  const solveFailed = resolution.outcome === "solve_failed";

  return (
    <section className="flex flex-col gap-4">
      <div
        className="flex flex-col gap-3 rounded-lg border p-4"
        style={{
          borderColor: "var(--success)",
          backgroundColor: "var(--success-subtle)",
        }}
        role="status"
      >
        <p
          className="flex items-center gap-2 text-sm font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          <CheckCircle2
            className="h-4 w-4 shrink-0"
            style={{ color: "var(--success)" }}
            aria-hidden
          />
          Reply posted to OpenReview
        </p>

        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          Posted on submission {post.submission_number} as a{" "}
          {post.visibility === "internal" ? "restricted" : "public"} comment,
          visible to {post.readers.length}{" "}
          {post.readers.length === 1 ? "group" : "groups"}.
        </p>

        {commentUrl && (
          <a
            href={commentUrl}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="View the posted comment on OpenReview (opens in new tab)"
            className="inline-flex w-fit items-center gap-1.5 text-sm font-medium underline-offset-4 hover:underline"
            style={{ color: "var(--accent)" }}
          >
            <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            View it on OpenReview
          </a>
        )}
      </div>

      {solveFailed && <SolveRecovery emailId={result.id} />}

      <Link
        href="/openreview-replies"
        className="inline-flex w-fit items-center gap-1.5 text-sm transition-colors hover:text-[var(--accent)]"
        style={{ color: "var(--text-secondary)" }}
      >
        <ArrowLeft className="h-4 w-4" aria-hidden />
        Back to OpenReview Replies
      </Link>
    </section>
  );
}

/**
 * The submission number to post under, or null.
 *
 * The endpoint REQUIRES this (`submission_number: int, gt=0`) — it builds the
 * `Official_Comment` invitation from it — and the extractor reports
 * `submission_numbers` as a LIST, so a value has to be picked. The first is
 * taken, matching what the header already displays as "the" paper, and a
 * non-numeric or absent entry yields null so the caller can refuse to submit
 * rather than send something the backend will reject with a 422.
 */
function submissionNumberFor(email: Email): number | null {
  const raw = email.extraction?.submission_numbers?.[0];
  if (!raw) return null;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

/** The structured `detail` body the backend returns for a refused post. */
type ErrorDetail = {
  message?: string;
  reason?: string;
  error?: string;
  error_type?: string;
};

function errorDetail(error: ApiError | null): ErrorDetail {
  const raw = error?.data;
  return raw && typeof raw === "object" ? (raw as ErrorDetail) : {};
}

/**
 * True when the gate refused because this reply is ALREADY on OpenReview.
 *
 * ⚠️ MATCHED ON PROSE, which is a real weakness and not a stylistic one. The
 * gate returns `{message, reason}` with no machine-readable code, so "already
 * posted" is indistinguishable from "not a candidate" except by reading the
 * sentence — and a reworded reason silently turns a handled, benign state back
 * into a red error. The fix belongs in the backend (a `code` field on the gate
 * decision); until then this is deliberately narrow, and every other 409 falls
 * through to the generic gate message rather than being guessed at.
 */
function isAlreadyPosted(error: ApiError | null): boolean {
  if (!error || error.status !== 409) return false;
  return /already been posted/i.test(errorDetail(error).reason ?? "");
}

/**
 * A specific, actionable sentence for each way a post can fail.
 *
 * Generic failure text is the thing to avoid here above all: every branch below
 * leads to a DIFFERENT next action — retry, edit and retry, reroute, or call
 * someone — and "something went wrong" leaves a chair to guess which, holding a
 * reply that never reached the person waiting for it.
 *
 * Every case here means NOTHING WAS POSTED. The partial-success case (posted,
 * ticket not closed) arrives as a 200 and is handled in the success path.
 */
function postFailureMessage(error: ApiError): string {
  const detail = errorDetail(error);

  switch (detail.error_type) {
    case "OpenReviewNoteNotFoundError":
      return (
        "The comment being replied to no longer exists on OpenReview — it may " +
        "have been deleted. Nothing was posted. Check the original discussion " +
        "before trying again."
      );
    case "OpenReviewPermissionError":
      return (
        "OpenReview refused the post: this account isn't allowed to comment on " +
        "this submission. Nothing was posted, and retrying won't help until " +
        "the account's venue permissions are changed."
      );
    case "OpenReviewThreadMismatchError":
      return (
        "The comment being replied to belongs to a different submission than " +
        "this email names, so posting would have put it in the wrong paper's " +
        "discussion. Nothing was posted."
      );
    case "OpenReviewAPIError":
      return `OpenReview rejected the request, and nothing was posted.${
        detail.error ? ` ${detail.error}` : ""
      } Retrying may work if this was temporary.`;
    default:
      break;
  }

  if (error.status === 409) {
    // A gate refusal other than already-posted: not a candidate, no note id, no
    // forum id, empty text. The gate's own `reason` is written for a person, so
    // it is shown rather than paraphrased.
    return (
      detail.reason ??
      "This reply can't be posted to OpenReview. Nothing was posted."
    );
  }
  if (error.status === 501) {
    return (
      "This deployment isn't configured to post to OpenReview" +
      `${detail.error ? ` (${detail.error})` : ""}. Nothing was posted — ` +
      "this needs a configuration change, not a retry."
    );
  }
  if (error.status === 404) {
    // ⚠️ The one error whose `detail` is a plain string, not an object.
    return error.detail || "This email no longer exists.";
  }
  return (
    detail.message ??
    error.detail ??
    "Posting the reply to OpenReview failed. Nothing was posted."
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
