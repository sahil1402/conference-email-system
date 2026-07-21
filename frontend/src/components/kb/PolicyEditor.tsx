"use client";

import { useState } from "react";

import { useEditPolicy } from "@/hooks";
import { Button, ErrorBanner, LoadingSpinner } from "@/components/ui";
import type { ApiError, PolicyVisibility } from "@/types";

const FIELD_STYLE = {
  backgroundColor: "var(--surface)",
  borderColor: "var(--border)",
  color: "var(--text-primary)",
} as const;

interface PolicyEditorProps {
  policyKey: string;
  initialTitle: string;
  initialContent: string;
  initialCategory?: string | null;
  /** Undefined ⇒ unknown current visibility (injection list) → default to internal. */
  initialVisibility?: PolicyVisibility;
  /** Undefined ⇒ skip optimistic-concurrency check. */
  expectedUpdatedAt?: string | null;
  onDone: () => void;
  onCancel: () => void;
}

/**
 * Inline edit form for one policy. Commit creates a new version (activated) and
 * retires the base via PATCH /policies/{key}/edit; Cancel discards with no call.
 * Reused by the KB PolicyList and the injection similar-policies list.
 */
export function PolicyEditor({
  policyKey,
  initialTitle,
  initialContent,
  initialCategory,
  initialVisibility,
  expectedUpdatedAt,
  onDone,
  onCancel,
}: PolicyEditorProps) {
  const [title, setTitle] = useState(initialTitle);
  const [content, setContent] = useState(initialContent);
  const [category, setCategory] = useState(initialCategory ?? "");
  const [visibility, setVisibility] = useState<PolicyVisibility>(
    initialVisibility ?? "internal",
  );

  const editPolicy = useEditPolicy();
  const error = editPolicy.error as ApiError | null;

  function commit() {
    editPolicy.mutate(
      {
        key: policyKey,
        body: {
          title: title.trim(),
          content: content.trim(),
          category: category.trim() || null,
          visibility,
          expected_updated_at: expectedUpdatedAt ?? undefined,
        },
      },
      { onSuccess: () => onDone() },
    );
  }

  const canCommit =
    title.trim().length > 0 &&
    content.trim().length > 0 &&
    !editPolicy.isPending;

  return (
    <div
      className="space-y-3 rounded-lg border p-3"
      style={{ borderColor: "var(--accent)", backgroundColor: "var(--surface)" }}
    >
      <label className="block space-y-1">
        <span className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
          Title
        </span>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="w-full rounded-lg border px-3 py-2 text-sm outline-none focus:border-[var(--accent)]"
          style={FIELD_STYLE}
        />
      </label>
      <label className="block space-y-1">
        <span className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
          Content
        </span>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={5}
          className="w-full resize-y rounded-lg border px-3 py-2 text-sm leading-relaxed outline-none focus:border-[var(--accent)]"
          style={FIELD_STYLE}
        />
      </label>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block space-y-1">
          <span className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
            Category (optional)
          </span>
          <input
            type="text"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="w-full rounded-lg border px-3 py-2 text-sm outline-none focus:border-[var(--accent)]"
            style={FIELD_STYLE}
          />
        </label>
        <label className="block space-y-1">
          <span className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
            Visibility
          </span>
          <select
            value={visibility}
            onChange={(e) => setVisibility(e.target.value as PolicyVisibility)}
            className="w-full rounded-lg border px-3 py-2 text-sm outline-none focus:border-[var(--accent)]"
            style={FIELD_STYLE}
          >
            <option value="public">public</option>
            <option value="internal">internal</option>
          </select>
        </label>
      </div>

      <div className="flex items-center gap-3">
        <Button type="button" size="sm" onClick={commit} disabled={!canCommit}>
          {editPolicy.isPending ? <LoadingSpinner size="sm" /> : null}
          Commit edit
        </Button>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={onCancel}
          disabled={editPolicy.isPending}
        >
          Cancel
        </Button>
      </div>

      {error && (
        <ErrorBanner message={error.detail || "Couldn't save the edit."} />
      )}
    </div>
  );
}
