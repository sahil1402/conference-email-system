"use client";

import { useState } from "react";
import { AlertTriangle, Check, ChevronDown, X } from "lucide-react";

import { ACTOR, useCreatePolicy, useFindSimilar } from "@/hooks";
import { Button, ErrorBanner, LoadingSpinner } from "@/components/ui";
import { HighlightText } from "@/components/kb/HighlightText";
import { PolicyEditor } from "@/components/kb/PolicyEditor";
import { cn } from "@/lib/utils";
import type { ApiError, ConflictReport } from "@/types";

const FIELD_STYLE = {
  backgroundColor: "var(--surface)",
  borderColor: "var(--border)",
  color: "var(--text-primary)",
} as const;

interface AddPolicyPanelProps {
  /**
   * The policy draft fields, owned by the parent page so they survive this
   * panel unmounting when the chair closes and reopens it. Cleared only after a
   * successful create (never on close) — see the create onSuccess below.
   */
  title: string;
  content: string;
  category: string;
  setTitle: (value: string) => void;
  setContent: (value: string) => void;
  setCategory: (value: string) => void;
  onClose: () => void;
  /** Called after a successful create (with the new policy's conflict report so
   *  the page can raise a heads-up banner) or a reconcile edit (no arg). */
  onCreated: (created?: {
    policy_key: string;
    conflict_report?: ConflictReport | null;
  }) => void;
}

/**
 * Inline "Add internal policy" form (mirrors IngestPanel.tsx's bordered-panel
 * + close-button + inline-result pattern — no modal, no toast).
 *
 * Governance flow: chair fills title + content (+ optional category/tags) →
 * "Check for related policies" surfaces existing SimilarPolicy hits, each with
 * a "supersede" checkbox → checked keys ride along as
 * `retire_keys` on create, so the new policy can retire what it supersedes in
 * one step.
 */
export function AddPolicyPanel({
  title,
  content,
  category,
  setTitle,
  setContent,
  setCategory,
  onClose,
  onCreated,
}: AddPolicyPanelProps) {
  // [tags-dropped E007] const [tagsText, setTagsText] = useState("");
  const [retireKeys, setRetireKeys] = useState<Set<string>>(new Set());
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());
  const [editingSimilarKey, setEditingSimilarKey] = useState<string | null>(null);
  const [reconciledKeys, setReconciledKeys] = useState<Set<string>>(new Set());

  const findSimilar = useFindSimilar();
  const createPolicy = useCreatePolicy();

  const canCheckSimilar = title.trim().length > 0 || content.trim().length > 0;
  const similar = findSimilar.data?.similar ?? [];
  // useCreatePolicy()/useFindSimilar() don't pin the mutation's TError, so it
  // defaults to `Error` — the client interceptor always rejects with ApiError
  // at runtime (see lib/api/client.ts), same cast EmailDetail.tsx uses.
  const createError = createPolicy.error as ApiError | null;
  const findSimilarError = findSimilar.error as ApiError | null;

  // Conflict detection (2e): the /similar call also returns the conflict report
  // over those same hits. Index it by key for per-card badges/highlights.
  const conflictReport = findSimilar.data?.conflict_report ?? null;
  const conflictByKey = new Map(
    (conflictReport?.conflicts ?? []).map((c) => [c.policy_key, c] as const)
  );
  const conflictCount = conflictReport?.conflicts.length ?? 0;
  const conflictChecked = conflictReport != null && conflictReport.available !== false;
  // Reuse the report on create only while the checked text is unchanged — else
  // the backend recomputes it for the text actually being created.
  const reusableReport =
    conflictReport &&
    findSimilar.variables?.title === title &&
    findSimilar.variables?.content === content
      ? conflictReport
      : undefined;

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
          // [tags-dropped E007] tags input removed; no longer sent.
          // const tags = tagsText
          //   .split(",")
          //   .map((t) => t.trim())
          //   .filter(Boolean);

          createPolicy.mutate(
            {
              title: title.trim(),
              content: content.trim(),
              category: category.trim() || null,
              // [tags-dropped E007] tags,
              actor: ACTOR,
              retire_keys: Array.from(retireKeys),
              // Skip a second model call when the panel already checked this text.
              conflict_report: reusableReport,
            },
            {
              onSuccess: (data) => {
                // Clear the draft ONLY now that a policy was actually created —
                // closing/reopening the panel keeps whatever was typed (2b).
                setTitle("");
                setContent("");
                setCategory("");
                onCreated(data);
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
          {/* [tags-dropped E007] Tags input removed (column dropped, no retrieval signal).
          <Field label="Tags (comma-separated, optional)">
            <input
              type="text"
              value={tagsText}
              onChange={(e) => setTagsText(e.target.value)}
              className="w-full rounded-lg border px-3 py-2 text-sm outline-none transition-colors focus:border-[var(--accent)]"
              style={FIELD_STYLE}
            />
          </Field>
          */}
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
          <Button
            type="submit"
            size="sm"
            className="ml-auto"
            disabled={createPolicy.isPending}
          >
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
            <div className="flex items-center justify-between gap-2">
              <p
                className="text-xs font-medium"
                style={{ color: "var(--text-secondary)" }}
              >
                {similar.length > 0
                  ? "Related existing policies"
                  : "No related policies found."}
              </p>
              {conflictReport &&
                (conflictReport.available === false ? (
                  <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                    Conflict check unavailable
                  </span>
                ) : conflictCount > 0 ? (
                  <span
                    className="inline-flex items-center gap-1 text-xs font-medium"
                    style={{ color: "var(--danger)" }}
                  >
                    <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                    {conflictCount} of {conflictReport.candidates_checked.length} conflict
                  </span>
                ) : (
                  <span
                    className="inline-flex items-center gap-1 text-xs"
                    style={{ color: "var(--text-muted)" }}
                  >
                    <Check className="h-3.5 w-3.5" aria-hidden /> No conflicts
                  </span>
                ))}
            </div>
            {similar.map((policy) => {
              const isExpanded = expandedKeys.has(policy.policy_key);
              const conflict = conflictByKey.get(policy.policy_key);
              return (
                <div
                  key={policy.policy_key}
                  className="rounded-md border px-3 py-2"
                  style={{ borderColor: "var(--border-subtle)" }}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p
                        className="text-sm break-words"
                        style={{ color: "var(--text-primary)" }}
                      >
                        {policy.title}
                      </p>
                      <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                        {policy.policy_key} · score {policy.score.toFixed(2)}
                      </p>
                      {conflict ? (
                        <span
                          className="mt-1 inline-flex items-center gap-1 text-xs font-medium"
                          style={{ color: "var(--danger)" }}
                        >
                          <AlertTriangle className="h-3 w-3" aria-hidden /> Conflict
                        </span>
                      ) : conflictChecked ? (
                        <span
                          className="mt-1 inline-flex items-center gap-1 text-xs"
                          style={{ color: "var(--text-muted)" }}
                        >
                          <Check className="h-3 w-3" aria-hidden /> No conflict
                        </span>
                      ) : null}
                    </div>
                    <div className="flex shrink-0 items-center gap-3">
                      <label
                        className="flex items-center gap-2 text-xs"
                        style={{
                          color: "var(--text-secondary)",
                          opacity: reconciledKeys.has(policy.policy_key) ? 0.5 : 1,
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={retireKeys.has(policy.policy_key)}
                          onChange={() => toggleRetireKey(policy.policy_key)}
                          disabled={reconciledKeys.has(policy.policy_key)}
                          className="h-4 w-4"
                        />
                        supersede
                      </label>
                      {/* Edit sits to the right of the supersede checkbox (away
                          from the Show more/less control below). */}
                      {!reconciledKeys.has(policy.policy_key) &&
                        editingSimilarKey !== policy.policy_key && (
                          <button
                            type="button"
                            onClick={() => setEditingSimilarKey(policy.policy_key)}
                            className="text-xs font-medium transition-opacity hover:opacity-80"
                            style={{ color: "var(--accent)" }}
                          >
                            Edit
                          </button>
                        )}
                    </div>
                  </div>

                  <p
                    className={cn(
                      "mt-1.5 text-xs leading-relaxed",
                      !isExpanded && "line-clamp-2"
                    )}
                    style={{ color: "var(--text-secondary)" }}
                  >
                    {conflict && conflict.snippets.length > 0 ? (
                      <HighlightText text={policy.content} snippets={conflict.snippets} />
                    ) : (
                      policy.content
                    )}
                  </p>

                  {conflict && (
                    <p className="mt-1.5 text-xs" style={{ color: "var(--danger)" }}>
                      {conflict.explanation}
                    </p>
                  )}

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

                  {reconciledKeys.has(policy.policy_key) ? (
                    <p className="mt-2 text-xs" style={{ color: "var(--success, var(--accent))" }}>
                      Reconciled — a new version was created and this policy retired.
                    </p>
                  ) : editingSimilarKey === policy.policy_key ? (
                    <div className="mt-2">
                      <PolicyEditor
                        policyKey={policy.policy_key}
                        initialTitle={policy.title}
                        initialContent={policy.content}
                        onDone={() => {
                          setReconciledKeys((prev) => new Set(prev).add(policy.policy_key));
                          setRetireKeys((prev) => {
                            const next = new Set(prev);
                            next.delete(policy.policy_key);
                            return next;
                          });
                          setEditingSimilarKey(null);
                          onCreated();
                        }}
                        onCancel={() => setEditingSimilarKey(null)}
                      />
                    </div>
                  ) : null}
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
