"use client";

/**
 * OpenReview Replies — STUB.
 *
 * Route + shell only. The list, its data fetch and every chair action land in a
 * follow-up piece; `getOpenReviewQueue` already exists in the API client but is
 * deliberately NOT called here yet, so this commit adds a reachable, correctly
 * highlighted route and nothing that can fail at runtime.
 *
 * Structure matches every other page: `AppShell` is applied globally in
 * `app/layout.tsx`, so a page owns only its own content, and that content opens
 * with the same centred wrapper + header block the other pages use. Copying that
 * shape now means the follow-up adds a list under an unchanged header rather
 * than restyling the page.
 */
export default function OpenReviewRepliesPage() {
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
    </div>
  );
}
