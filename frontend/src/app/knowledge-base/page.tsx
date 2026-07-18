"use client";

import { useEffect, useMemo, useState } from "react";
import { BookOpen, Plus } from "lucide-react";

import { usePolicies, useReactivatePolicy, useRetirePolicy } from "@/hooks";
import { PolicyFilters } from "@/components/kb/PolicyFilters";
import { PolicyList } from "@/components/kb/PolicyList";
import { Button, EmptyState, ErrorBanner, LoadingSpinner } from "@/components/ui";
import { cn } from "@/lib/utils";
import type { PolicyListParams } from "@/types";

type View = "policies" | "history";
type VisibilityFilter = "all" | "public" | "internal";
type StatusFilter = "active" | "inactive" | "all";

const VIEW_OPTIONS: { value: View; label: string }[] = [
  { value: "policies", label: "Policies" },
  { value: "history", label: "History" },
];

export default function KnowledgeBasePage() {
  const [view, setView] = useState<View>("policies");
  const [addOpen, setAddOpen] = useState(false);

  const [search, setSearch] = useState("");
  const [visibility, setVisibility] = useState<VisibilityFilter>("all");
  // Default to "active" — retired policies are the exception, not the norm,
  // so keep them out of the way until the reviewer opts in.
  const [status, setStatus] = useState<StatusFilter>("active");

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
    if (status !== "all") p.status = status;
    if (debouncedSearch) p.search = debouncedSearch;
    return p;
  }, [visibility, status, debouncedSearch]);

  const { policies, isLoading, isError, refetch } = usePolicies(params);
  const retireMutation = useRetirePolicy();
  const reactivateMutation = useReactivatePolicy();

  const pendingKey = retireMutation.isPending
    ? retireMutation.variables ?? null
    : reactivateMutation.isPending
      ? reactivateMutation.variables ?? null
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
              onClick={() => setView(value)}
              className={cn(
                "rounded-md px-4 py-1.5 text-sm font-medium transition-colors"
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
            </button>
          );
        })}
      </div>

      {view === "history" ? (
        // History view lands in a later task — placeholder for now.
        <div
          className="rounded-lg border p-6 text-sm"
          style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
        >
          Coming in the History task
        </div>
      ) : (
        <div className="flex flex-col gap-6">
          <div className="flex justify-end">
            <Button type="button" onClick={() => setAddOpen((v) => !v)}>
              <Plus className="h-4 w-4" />
              Add internal policy
            </Button>
          </div>

          {addOpen && (
            // The add-policy panel lands in a later task — placeholder for now.
            <div
              className="rounded-lg border p-4 text-sm"
              style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
            >
              Coming in the Add Policy task
            </div>
          )}

          <PolicyFilters
            search={search}
            onSearchChange={setSearch}
            visibility={visibility}
            onVisibilityChange={setVisibility}
            status={status}
            onStatusChange={setStatus}
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
              title={search ? "No matching policies" : "No policies yet"}
              description={
                search
                  ? "Try a different search term or filter."
                  : "Policy documents will appear here once added to the knowledge base."
              }
            />
          ) : (
            <PolicyList
              policies={policies}
              onRetire={(key) => retireMutation.mutate(key)}
              onReactivate={(key) => reactivateMutation.mutate(key)}
              pendingKey={pendingKey}
            />
          )}
        </div>
      )}
    </div>
  );
}
