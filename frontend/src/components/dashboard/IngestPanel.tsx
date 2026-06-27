"use client";

import { useEffect, useState } from "react";
import { Plus, ArrowRight, X } from "lucide-react";

import { useIngestEmail } from "@/hooks/useEmailActions";
import { Badge, ErrorBanner, LoadingSpinner } from "@/components/ui";
import { formatIntentLabel, laneLabel } from "@/lib/format";

const DEFAULTS = {
  from: "author@university.edu",
  to: "chairs@aaai.org",
  subject: "Question about submission deadline",
  body:
    "Hi, I wanted to confirm the final submission deadline for AAAI 2025. " +
    "Is there any possibility of an extension?",
};

const FIELD_STYLE = {
  backgroundColor: "var(--surface)",
  borderColor: "var(--border)",
  color: "var(--text-primary)",
} as const;

/**
 * Self-contained demo panel: submit a test email straight through the pipeline
 * from the UI (no curl). Collapsed by default; shows the PipelineResult inline.
 */
export function IngestPanel() {
  const [isOpen, setIsOpen] = useState(false);
  const [from, setFrom] = useState(DEFAULTS.from);
  const [to, setTo] = useState(DEFAULTS.to);
  const [subject, setSubject] = useState(DEFAULTS.subject);
  const [body, setBody] = useState(DEFAULTS.body);

  const { mutate, data, error, isPending, isSuccess, reset } = useIngestEmail();

  // Auto-collapse a few seconds after a successful run.
  useEffect(() => {
    if (!isSuccess) return;
    const id = setTimeout(() => setIsOpen(false), 3000);
    return () => clearTimeout(id);
  }, [isSuccess]);

  if (!isOpen) {
    return (
      <button
        type="button"
        onClick={() => {
          reset();
          setIsOpen(true);
        }}
        className="inline-flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium transition-colors hover:bg-[var(--surface-raised)]"
        style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
      >
        <Plus className="h-4 w-4" />
        Inject Test Email
      </button>
    );
  }

  return (
    <div
      className="rounded-xl border p-5"
      style={{
        backgroundColor: "var(--surface-raised)",
        borderColor: "var(--border)",
      }}
    >
      <div className="mb-4 flex items-center justify-between">
        <h3
          className="text-sm font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          Inject Test Email
        </h3>
        <button
          type="button"
          onClick={() => setIsOpen(false)}
          className="rounded-md p-1 transition-colors hover:bg-[var(--surface)]"
          style={{ color: "var(--text-muted)" }}
          aria-label="Close"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          mutate({ from, to, subject, body });
        }}
        className="space-y-3"
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="From">
            <input
              type="email"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              required
              className="w-full rounded-lg border px-3 py-2 text-sm outline-none transition-colors focus:border-[var(--accent)]"
              style={FIELD_STYLE}
            />
          </Field>
          <Field label="To">
            <input
              type="email"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              required
              className="w-full rounded-lg border px-3 py-2 text-sm outline-none transition-colors focus:border-[var(--accent)]"
              style={FIELD_STYLE}
            />
          </Field>
        </div>
        <Field label="Subject">
          <input
            type="text"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            required
            className="w-full rounded-lg border px-3 py-2 text-sm outline-none transition-colors focus:border-[var(--accent)]"
            style={FIELD_STYLE}
          />
        </Field>
        <Field label="Body">
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={4}
            required
            className="w-full resize-y rounded-lg border px-3 py-2 text-sm leading-relaxed outline-none transition-colors focus:border-[var(--accent)]"
            style={FIELD_STYLE}
          />
        </Field>

        <button
          type="submit"
          disabled={isPending}
          className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
          style={{ backgroundColor: "var(--accent)", color: "var(--text-primary)" }}
        >
          {isPending ? <LoadingSpinner size="sm" /> : null}
          Run Pipeline
          {!isPending && <ArrowRight className="h-4 w-4" />}
        </button>
      </form>

      {error && (
        <ErrorBanner
          className="mt-4"
          message={error.detail || "Pipeline failed."}
        />
      )}

      {isSuccess && data && (
        <div
          className="mt-4 space-y-2 rounded-lg border p-4 text-sm"
          style={{
            borderColor: "var(--border-subtle)",
            backgroundColor: "var(--surface)",
          }}
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="neutral" size="sm">
              {formatIntentLabel(data.classification.intent)}
            </Badge>
            <span style={{ color: "var(--text-secondary)" }}>
              {(data.classification.confidence * 100).toFixed(0)}% confidence
            </span>
            <span style={{ color: "var(--text-muted)" }}>·</span>
            <Badge
              variant={data.routing.lane === "faq" ? "faq" : "review"}
              size="sm"
            >
              {laneLabel(data.routing.lane)}
            </Badge>
          </div>
          <p
            className="line-clamp-3 text-xs leading-relaxed"
            style={{ color: "var(--text-muted)" }}
          >
            {data.draft.draft_text.slice(0, 100)}
            {data.draft.draft_text.length > 100 ? "…" : ""}
          </p>
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
        {label}
      </span>
      {children}
    </label>
  );
}
