"use client";

import { useEmailThread } from "@/hooks";
import { ErrorBanner, LoadingSpinner } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import type { Email, EmailThreadMessage } from "@/types";

type TurnKind = "requester" | "support" | "internal";

function turnMeta(m: EmailThreadMessage): { label: string; kind: TurnKind } {
  if (!m.public) return { label: "Internal note", kind: "internal" };
  if (m.author_role === "end-user") return { label: "Requester", kind: "requester" };
  return { label: "Support", kind: "support" };
}

const KIND_STYLE: Record<TurnKind, { bg: string; border: string; accent: string }> = {
  requester: {
    bg: "var(--surface-raised)",
    border: "var(--border-subtle)",
    accent: "var(--text-primary)",
  },
  support: {
    bg: "var(--accent-subtle)",
    border: "var(--accent)",
    accent: "var(--accent)",
  },
  internal: {
    bg: "var(--surface)",
    border: "var(--warning)",
    accent: "var(--warning)",
  },
};

function MessageBubble({ message }: { message: EmailThreadMessage }) {
  const { label, kind } = turnMeta(message);
  const style = KIND_STYLE[kind];
  return (
    <div
      className="rounded-lg border p-4"
      style={{
        backgroundColor: style.bg,
        borderColor: style.border,
        borderStyle: kind === "internal" ? "dashed" : "solid",
      }}
    >
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="font-semibold" style={{ color: style.accent }}>
          {label}
        </span>
        <span style={{ color: "var(--text-muted)" }}>
          {formatDateTime(message.created_at)}
        </span>
      </div>
      {message.html_body ? (
        // Rich HTML rendering. Content is sanitized server-side (backend bleach
        // allowlist) before it reaches the client, so injecting it is safe.
        <div
          className="conf-html text-base leading-relaxed"
          style={{ color: "var(--text-primary)", overflowWrap: "anywhere" }}
          dangerouslySetInnerHTML={{ __html: message.html_body }}
        />
      ) : (
        <div
          className="text-base leading-relaxed"
          style={{
            color: "var(--text-primary)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {message.plain_body ?? ""}
        </div>
      )}
    </div>
  );
}

/**
 * The ticket conversation, rendered as multi-turn message bubbles (requester /
 * support / internal-note, internal notes visually distinct). Falls back to the
 * single stored `email.body` as one requester turn for non-Zendesk rows (which
 * have no thread messages). Fetches per selected email via useEmailThread.
 */
export function ConversationThread({ email }: { email: Email }) {
  const { messages, isLoading, isError } = useEmailThread(email.id);

  if (isLoading) {
    return (
      <div className="flex justify-center py-6">
        <LoadingSpinner />
      </div>
    );
  }
  if (isError) {
    return <ErrorBanner message="Could not load the conversation." />;
  }

  if (messages.length === 0) {
    // Non-Zendesk / no-thread row: show the stored body as a single turn.
    return (
      <div
        className="rounded-lg border p-4 text-base leading-relaxed"
        style={{
          backgroundColor: "var(--surface-raised)",
          borderColor: "var(--border-subtle)",
          color: "var(--text-primary)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {email.body}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {messages.map((m, i) => (
        <MessageBubble key={m.comment_id ?? i} message={m} />
      ))}
    </div>
  );
}
