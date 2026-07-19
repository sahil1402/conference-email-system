"use client";

import { useState } from "react";
import { ChevronDown, X } from "lucide-react";

import { ACTOR, useCreatePolicy, useFindSimilar } from "@/hooks";
import { Button, ErrorBanner, LoadingSpinner } from "@/components/ui";
import { cn } from "@/lib/utils";
import type { ApiError } from "@/types";

const FIELD_STYLE = {
  backgroundColor: "var(--surface)",
  borderColor: "var(--border)",
  color: "var(--text-primary)",
} as const;

interface AddPolicyPanelProps {
  onClose: () => void;
  onCreated: () => void;
}

/**
 * Inline "Add internal policy" form (mirrors IngestPanel.tsx's bordered-panel
 * + close-button + inline-result pattern — no modal, no toast).
 *
 * Governance flow: chair fills title + content (+ optional category/tags) →
 * "Check for related policies" surfaces existing SimilarPolicy hits, each with
 * a "supersede (retire this)" checkbox → checked keys ride along as
 * `retire_keys` on create, so the new policy can retire what it supersedes in
 * one step.
 */
export function AddPolicyPanel({ onClose, onCreated }: AddPolicyPanelProps) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [category, setCategory] = useState("");
  const [tagsText, setTagsText] = useState("");
  const [retireKeys, setRetireKeys] = useState<Set<string>>(new Set());
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());

  const findSimilar = useFindSimilar();
  const createPolicy = useCreatePolicy();

  const canCheckSimilar = title.trim().length > 0 || content.trim().length > 0;
  const similar = findSimilar.data?.similar ?? [];
  // useCreatePolicy()/useFindSimilar() don't pin the mutation's TError, so it
  // defaults to `Error` — the client interceptor always rejects with ApiError
  // at runtime (see lib/api/client.ts), same cast EmailDetail.tsx uses.
  const createError = createPolicy.error as ApiError | null;
  const findSimilarError = findSimilar.error as ApiError | null;

  function toggleRetireKey(key: string) {
    setRetireKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function toggleExpanded(key: string) {
    setExpandedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
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
          Add internal policy
        </h3>
        <button
          type="button"
          onClick={onClose}
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
          const tags = tagsText
            .split(",")
            .map((t) => t.trim())
            .filter(Boolean);

          createPolicy.mutate(
            {
              title: title.trim(),
              content: content.trim(),
              category: category.trim() || null,
              tags,
              actor: ACTOR,
              retire_keys: Array.from(retireKeys),
            },
            {
              onSuccess: () => {
                onCreated();
                onClose();
              },
            }
          );
        }}
        className="space-y-3"
      >
        <Field label="Title">
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            className="w-full rounded-lg border px-3 py-2 text-sm outline-none transition-colors focus:border-[var(--accent)]"
            style={FIELD_STYLE}
          />
        </Field>
        <Field label="Content">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={4}
            required
            className="w-full resize-y rounded-lg border px-3 py-2 text-sm leading-relaxed outline-none transition-colors focus:border-[var(--accent)]"
            style={FIELD_STYLE}
          />
        </Field>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Category (optional)">
            <input
              type="text"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full rounded-lg border px-3 py-2 text-sm outline-none transition-colors focus:border-[var(--accent)]"
              style={FIELD_STYLE}
            />
          </Field>
          <Field label="Tags (comma-separated, optional)">
            <input
              type="text"
              value={tagsText}
              onChange={(e) => setTagsText(e.target.value)}
              className="w-full rounded-lg border px-3 py-2 text-sm outline-none transition-colors focus:border-[var(--accent)]"
              style={FIELD_STYLE}
            />
          </Field>
        </div>

        <div className="flex flex-wrap items-center gap-3 pt-1">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={!canCheckSimilar || findSimilar.isPending}
            onClick={() => {
              findSimilar.mutate({ title, content }, {
                onSuccess: (data) => {
                  // Prune retireKeys to only include keys present in new results
                  const similarKeys = new Set<string>();
                  if (data?.similar) {
                    data.similar.forEach((policy) => {
                      similarKeys.add(policy.policy_key);
                    });
                  }
                  setRetireKeys((prev) => {
                    const pruned = new Set<string>();
                    prev.forEach((key) => {
                      if (similarKeys.has(key)) {
                        pruned.add(key);
                      }
                    });
                    return pruned;
                  });
                },
              });
            }}
          >
            {findSimilar.isPending ? <LoadingSpinner size="sm" /> : null}
            Check for related policies
          </Button>
          <Button type="submit" size="sm" disabled={createPolicy.isPending}>
            {createPolicy.isPending ? <LoadingSpinner size="sm" /> : null}
            Create internal policy
          </Button>
        </div>

        {findSimilarError && (
          <div
            className="rounded-lg px-3 py-2 text-xs"
            style={{
              color: "var(--danger)",
              backgroundColor: "var(--surface)",
              borderLeft: "3px solid var(--danger)",
            }}
          >
            {findSimilarError.detail || "Couldn't check for related policies."}
          </div>
        )}

        {findSimilar.isSuccess && (
          <div
            className="space-y-2 rounded-lg border p-3"
            style={{
              borderColor: "var(--border-subtle)",
              backgroundColor: "var(--surface)",
            }}
          >
            <p
              className="text-xs font-medium"
              style={{ color: "var(--text-secondary)" }}
            >
              {similar.length > 0
                ? "Related existing policies"
                : "No related policies found."}
            </p>
            {similar.map((policy) => {
              const isExpanded = expandedKeys.has(policy.policy_key);
              return (
                <div
                  key={policy.policy_key}
                  className="rounded-md border px-3 py-2"
                  style={{ borderColor: "var(--border-subtle)" }}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p
                        className="truncate text-sm"
                        style={{ color: "var(--text-primary)" }}
                      >
                        {policy.title}
                      </p>
                      <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                        {policy.policy_key} · score {policy.score.toFixed(2)}
                      </p>
                    </div>
                    <label
                      className="flex shrink-0 items-center gap-2 text-xs"
                      style={{ color: "var(--text-secondary)" }}
                    >
                      <input
                        type="checkbox"
                        checked={retireKeys.has(policy.policy_key)}
                        onChange={() => toggleRetireKey(policy.policy_key)}
                        className="h-4 w-4"
                      />
                      supersede (retire this)
                    </label>
                  </div>

                  <p
                    className={cn(
                      "mt-1.5 text-xs leading-relaxed",
                      !isExpanded && "line-clamp-2"
                    )}
                    style={{ color: "var(--text-secondary)" }}
                  >
                    {policy.content}
                  </p>

                  <button
                    type="button"
                    onClick={() => toggleExpanded(policy.policy_key)}
                    aria-expanded={isExpanded}
                    className="mt-1 inline-flex items-center gap-1 text-xs font-medium transition-opacity hover:opacity-80"
                    style={{ color: "var(--accent)" }}
                  >
                    <ChevronDown
                      className={cn(
                        "h-3.5 w-3.5 transition-transform duration-200",
                        isExpanded && "rotate-180"
                      )}
                    />
                    {isExpanded ? "Show less" : "Show more"}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </form>

      {createError && (
        <ErrorBanner
          className="mt-4"
          message={createError.detail || "Couldn't create the policy."}
        />
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
