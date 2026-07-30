"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BookOpen, ExternalLink, Lightbulb, Plus, RefreshCw, X } from "lucide-react";

import {
  useAcceptSuggestion,
  usePolicies,
  useReactivatePolicy,
  useRecheckPolicy,
  useReevaluatePolicies,
  useRejectSuggestion,
  useRetirePolicy,
  useSuggestions,
  useSuggestionsCount,
} from "@/hooks";
import { AddPolicyPanel } from "@/components/kb/AddPolicyPanel";
import { PolicyFilters } from "@/components/kb/PolicyFilters";
import { PolicyHistory } from "@/components/kb/PolicyHistory";
import { PolicyList } from "@/components/kb/PolicyList";
import { SuggestionList } from "@/components/kb/SuggestionList";
import { Badge, Button, EmptyState, ErrorBanner, LoadingSpinner } from "@/components/ui";
import { cn } from "@/lib/utils";
import type { ConflictReport, PolicyListParams, PolicySuggestion } from "@/types";

type View = "policies" | "suggestions" | "history";
type VisibilityFilter = "all" | "public" | "internal";
type StatusFilter = "active" | "inactive" | "all";

const VIEW_OPTIONS: { value: View; label: string }[] = [
  { value: "policies", label: "Policies" },
  { value: "suggestions", label: "Suggestions" },
  { value: "history", label: "History" },
];

export default function KnowledgeBasePage() {
  const [view, setView] = useState<View>("policies");
  const [addOpen, setAddOpen] = useState(false);

  // Add-internal-policy draft fields, lifted out of AddPolicyPanel so they
  // survive the panel unmounting on close: closing and reopening keeps whatever
  // the chair typed; the fields clear only after a successful create (2b).
  const [draftTitle, setDraftTitle] = useState("");
  const [draftContent, setDraftContent] = useState("");
  const [draftCategory, setDraftCategory] = useState("");

  // Suggestions review (Continual Experience Learning, Task 6): the suggestion
  // currently under review. Selecting one seeds the SAME lifted draft fields
  // above, so reviewing a suggestion reuses the add-internal-policy flow
  // pre-filled rather than a bespoke editor.
  const [selectedSuggestion, setSelectedSuggestion] = useState<PolicySuggestion | null>(null);

  const [search, setSearch] = useState("");
  const [visibility, setVisibility] = useState<VisibilityFilter>("all");
  // Default to "active" — retired policies are the exception, not the norm,
  // so keep them out of the way until the reviewer opts in.
  const [status, setStatus] = useState<StatusFilter>("active");
  // "Conflicts only" (2e): show just the active policies with a live conflict.
  const [conflictsOnly, setConflictsOnly] = useState(false);

  // Debounce the search box so typing doesn't fire a request per keystroke
  // (mirrors the queue page's search debounce).
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);

  const params = useMemo<PolicyListParams>(() => {
    const p: PolicyListParams = {};
    if (visibility !== "all") p.visibility = visibility;
    if (conflictsOnly) {
      // Conflicts are an active-policy concern — force active + the flag.
      p.status = "active";
      p.has_conflicts = true;
    } else if (status !== "all") {
      p.status = status;
    }
    if (debouncedSearch) p.search = debouncedSearch;
    return p;
  }, [visibility, status, conflictsOnly, debouncedSearch]);

  const { policies, isLoading, isError, refetch } = usePolicies(params);
  const retireMutation = useRetirePolicy();
  const reactivateMutation = useReactivatePolicy();
  const recheckMutation = useRecheckPolicy();
  const reevaluate = useReevaluatePolicies();

  // Suggestions review (Task 6). Pending count drives the segment badge
  // regardless of which view is active; the list itself only fetches while
  // the Suggestions segment is showing.
  const { pending: pendingSuggestionCount } = useSuggestionsCount();
  const {
    suggestions,
    isLoading: suggestionsLoading,
    isError: suggestionsError,
    refetch: refetchSuggestions,
  } = useSuggestions("pending", { enabled: view === "suggestions" });
  const rejectSuggestionMutation = useRejectSuggestion();
  const acceptSuggestionMutation = useAcceptSuggestion();

  const pendingKey = retireMutation.isPending
    ? retireMutation.variables ?? null
    : reactivateMutation.isPending
      ? reactivateMutation.variables ?? null
      : null;
  const recheckingKey = recheckMutation.isPending
    ? recheckMutation.variables ?? null
    : null;

  // Non-modal heads-up after a KB change introduces conflicts (2e). Advisory —
  // the durable detail lives on the policy's own card below.
  const [conflictBanner, setConflictBanner] = useState<
    { policyKey: string; report: ConflictReport } | null
  >(null);
  const announceConflicts = (policyKey: string, report?: ConflictReport | null) => {
    if (report && report.available !== false && report.conflicts.length > 0) {
      setConflictBanner({ policyKey, report });
    }
  };
  const bannerPolicyTitle = conflictBanner
    ? policies.find((p) => p.policy_key === conflictBanner.policyKey)?.title ?? null
    : null;

  return (
    <div className="mx-auto w-full max-w-4xl px-8 py-10">
      {/* Header */}
      <header className="mb-8 flex flex-col gap-1">
        <h1
          className="text-2xl font-semibold tracking-tight"
          style={{ color: "var(--text-primary)" }}
        >
          Knowledge Base
        </h1>
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          Manage the policy documents that ground FAQ replies and chair drafts.
        </p>
      </header>

      {/* View toggle */}
      <div
        className="mb-6 flex w-fit gap-1 rounded-lg p-1"
        style={{ backgroundColor: "var(--surface)" }}
      >
        {VIEW_OPTIONS.map(({ value, label }) => {
          const active = view === value;
          return (
            <button
              key={value}
              type="button"
              onClick={() => {
                setView(value);
                // Leaving the Suggestions segment ends the in-progress review
                // (the panel below is gated on selectedSuggestion !== null).
                if (value !== "suggestions") setSelectedSuggestion(null);
              }}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-4 py-1.5 text-sm font-medium transition-colors"
              )}
              style={
                active
                  ? {
                      backgroundColor: "var(--accent-subtle)",
                      color: "var(--accent)",
                    }
                  : { color: "var(--text-secondary)" }
              }
            >
              {label}
              {value === "suggestions" && pendingSuggestionCount > 0 && (
                <Badge variant="accent" size="sm">
                  {pendingSuggestionCount}
                </Badge>
              )}
            </button>
          );
        })}
      </div>

      {/* Hoisted above the view conditional (not scoped to the Policies
          segment): both the manual Add-panel AND the suggestions-review
          create flow can raise this, so it must show regardless of which
          segment is active — the app has no toast layer, this banner IS the
          notification. */}
      {conflictBanner && (
        <div
          className="mb-6 flex items-start gap-3 rounded-xl border px-4 py-3 text-sm"
          style={{
            backgroundColor: "var(--danger-subtle)",
            borderColor: "var(--danger)",
            color: "var(--text-primary)",
          }}
          role="alert"
        >
          <AlertTriangle
            className="mt-0.5 h-5 w-5 shrink-0"
            style={{ color: "var(--danger)" }}
            aria-hidden
          />
          <div className="min-w-0 flex-1">
            <p className="font-medium">{conflictBanner.report.summary}</p>
            <p className="mt-0.5 text-xs" style={{ color: "var(--text-secondary)" }}>
              {bannerPolicyTitle ? `On “${bannerPolicyTitle}”. ` : ""}
              Expand “conflicts” on its card to reconcile, or
            </p>
            <button
              type="button"
              onClick={() => {
                setConflictsOnly(true);
                setStatus("active");
                setConflictBanner(null);
                // The filtered list this links to lives on the Policies
                // segment — switch there so the CTA actually lands somewhere,
                // since the banner can now surface from any segment.
                setView("policies");
                setSelectedSuggestion(null);
              }}
              className="mt-1 text-xs font-semibold underline transition-opacity hover:opacity-80"
              style={{ color: "var(--danger)" }}
            >
              Show all policies with conflicts →
            </button>
          </div>
          <button
            type="button"
            onClick={() => setConflictBanner(null)}
            aria-label="Dismiss conflict notice"
            className="shrink-0 rounded-md p-1 transition-colors hover:bg-[var(--surface)]"
            style={{ color: "var(--text-muted)" }}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {view === "history" ? (
        <PolicyHistory />
      ) : view === "suggestions" ? (
        <div className="flex flex-col gap-6">
          {suggestionsError ? (
            <ErrorBanner
              message="Couldn't load suggestions."
              onRetry={() => refetchSuggestions()}
            />
          ) : suggestionsLoading ? (
            <div className="flex items-center justify-center py-24">
              <LoadingSpinner size="lg" />
            </div>
          ) : suggestions.length === 0 ? (
            <EmptyState
              icon={<Lightbulb className="h-5 w-5" />}
              title="No pending suggestions"
              description="Policy suggestions learned from chair edits will appear here for review."
            />
          ) : (
            <SuggestionList
              suggestions={suggestions}
              selectedId={selectedSuggestion?.id ?? null}
              onSelect={(s) => {
                setSelectedSuggestion(s);
                // Seed the SAME lifted draft fields the "Add internal policy"
                // flow uses below — this is the pre-fill, not a new form.
                setDraftTitle(s.title);
                setDraftContent(s.content);
                setDraftCategory(s.category ?? "");
              }}
              onReject={(s) =>
                rejectSuggestionMutation.mutate(
                  { id: s.id },
                  {
                    onSuccess: () => {
                      if (selectedSuggestion?.id === s.id) setSelectedSuggestion(null);
                    },
                  }
                )
              }
              rejectingId={
                rejectSuggestionMutation.isPending
                  ? rejectSuggestionMutation.variables?.id ?? null
                  : null
              }
            />
          )}

          {selectedSuggestion && (
            <div
              className="flex flex-col gap-4 rounded-xl border p-5"
              style={{ backgroundColor: "var(--surface-raised)", borderColor: "var(--border)" }}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <h3 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                    Reviewing suggestion
                  </h3>
                  <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
                    {selectedSuggestion.experience_summary}
                  </p>
                  {selectedSuggestion.reason && (
                    <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
                      {selectedSuggestion.reason}
                    </p>
                  )}
                  {selectedSuggestion.intents.length > 0 && (
                    <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
                      Intents: {selectedSuggestion.intents.join(", ")}
                    </p>
                  )}
                </div>
                {selectedSuggestion.source_zendesk_ticket_id != null && (
                  <button
                    type="button"
                    onClick={() => {
                      // window.location.origin only read inside a handler
                      // (mirrors CopyLinkButton) — avoids an SSR/hydration
                      // mismatch from resolving it during render.
                      window.open(
                        `${window.location.origin}/tickets/${selectedSuggestion.source_zendesk_ticket_id}`,
                        "_blank",
                        "noopener,noreferrer"
                      );
                    }}
                    className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border px-2 py-1 text-[11px] font-medium leading-none transition-colors hover:border-[var(--accent)] hover:bg-[var(--accent-subtle)] hover:text-[var(--accent)]"
                    style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
                  >
                    <ExternalLink className="h-3 w-3" aria-hidden />
                    Ticket #{selectedSuggestion.source_zendesk_ticket_id}
                  </button>
                )}
              </div>

              {selectedSuggestion.conflict_report &&
                selectedSuggestion.conflict_report.available !== false &&
                selectedSuggestion.conflict_report.conflicts.length > 0 && (
                  <div
                    className="overflow-hidden rounded-md border"
                    style={{ borderColor: "var(--danger)", backgroundColor: "var(--danger-subtle)" }}
                  >
                    <div
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium"
                      style={{ color: "var(--danger)" }}
                    >
                      <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                      {selectedSuggestion.conflict_report.conflicts.length} conflict
                      {selectedSuggestion.conflict_report.conflicts.length > 1 ? "s" : ""} with an
                      existing policy
                    </div>
                    <div className="space-y-2 px-3 pb-2">
                      {selectedSuggestion.conflict_report.conflicts.map((c) => (
                        <div key={c.policy_key} className="text-xs" style={{ color: "var(--text-primary)" }}>
                          <span className="font-medium">{c.title || c.policy_key}</span>
                          <span style={{ color: "var(--text-muted)" }}> ({c.policy_key})</span>
                          {c.explanation ? <span> — {c.explanation}</span> : null}
                          {c.snippets.map((snippet, i) => (
                            <span
                              key={i}
                              className="mt-0.5 block italic"
                              style={{ color: "var(--text-secondary)" }}
                            >
                              “{snippet}”
                            </span>
                          ))}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

              {/* Reusing the existing add-internal-policy flow, pre-filled —
                  NOT a bespoke editor. `key` forces a remount per suggestion so
                  each review starts with a clean "Check for related" / retire-
                  key state rather than carrying over the previous one. */}
              <AddPolicyPanel
                key={`suggestion-${selectedSuggestion.id}`}
                title={draftTitle}
                content={draftContent}
                category={draftCategory}
                setTitle={setDraftTitle}
                setContent={setDraftContent}
                setCategory={setDraftCategory}
                onClose={() => setSelectedSuggestion(null)}
                onCreated={(created) => {
                  refetch();
                  // Only link+accept on an ACTUAL create (useCreatePolicy
                  // success). The panel's "Edit" reconcile-into-an-existing-
                  // policy path also calls onCreated but with no argument —
                  // that doesn't produce a resulting_policy_key for this
                  // suggestion, so it's deliberately left pending rather than
                  // guessing which policy to link it to.
                  if (created) {
                    acceptSuggestionMutation.mutate({
                      id: selectedSuggestion.id,
                      policyKey: created.policy_key,
                    });
                    announceConflicts(created.policy_key, created.conflict_report);
                    setSelectedSuggestion(null);
                  }
                }}
              />
            </div>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-6">
          <div className="flex items-center justify-end gap-3">
            {reevaluate.isSuccess && (
              <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
                {reevaluate.data.open === 0
                  ? "No open tickets to re-evaluate."
                  : `Re-evaluating ${reevaluate.data.open} open ticket${
                      reevaluate.data.open === 1 ? "" : "s"
                    }…`}
              </span>
            )}
            <Button
              type="button"
              onClick={() => reevaluate.mutate()}
              disabled={reevaluate.isPending}
            >
              <RefreshCw className="h-4 w-4" />
              {reevaluate.isPending ? "Starting…" : "Re-evaluate open tickets"}
            </Button>
            <Button type="button" onClick={() => setAddOpen((v) => !v)}>
              <Plus className="h-4 w-4" />
              Add internal policy
            </Button>
          </div>

          {addOpen && (
            <AddPolicyPanel
              title={draftTitle}
              content={draftContent}
              category={draftCategory}
              setTitle={setDraftTitle}
              setContent={setDraftContent}
              setCategory={setDraftCategory}
              onClose={() => setAddOpen(false)}
              onCreated={(created) => {
                refetch();
                if (created) announceConflicts(created.policy_key, created.conflict_report);
              }}
            />
          )}

          <PolicyFilters
            search={search}
            onSearchChange={setSearch}
            visibility={visibility}
            onVisibilityChange={setVisibility}
            status={status}
            onStatusChange={setStatus}
            conflictsOnly={conflictsOnly}
            onConflictsOnlyChange={(v) => {
              setConflictsOnly(v);
              if (v) setStatus("active");
            }}
          />

          {isError ? (
            <ErrorBanner
              message="Couldn't load the knowledge base."
              onRetry={() => refetch()}
            />
          ) : isLoading ? (
            <div className="flex items-center justify-center py-24">
              <LoadingSpinner size="lg" />
            </div>
          ) : policies.length === 0 ? (
            <EmptyState
              icon={<BookOpen className="h-5 w-5" />}
              title={
                conflictsOnly
                  ? "No policies with conflicts"
                  : search
                    ? "No matching policies"
                    : "No policies yet"
              }
              description={
                conflictsOnly
                  ? "No active policy currently has a flagged conflict."
                  : search
                    ? "Try a different search term or filter."
                    : "Policy documents will appear here once added to the knowledge base."
              }
            />
          ) : (
            <PolicyList
              policies={policies}
              onRetire={(key) => retireMutation.mutate(key)}
              onReactivate={(key) =>
                reactivateMutation.mutate(key, {
                  onSuccess: (data) =>
                    announceConflicts(data.policy_key, data.conflict_report),
                })
              }
              onRecheck={(key) => recheckMutation.mutate(key)}
              pendingKey={pendingKey}
              recheckingKey={recheckingKey}
            />
          )}
        </div>
      )}
    </div>
  );
}
