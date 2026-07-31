import { Lightbulb } from "lucide-react";

import { Badge, LoadingSpinner } from "@/components/ui";
import { cn } from "@/lib/utils";
import type { PolicySuggestion } from "@/types";

interface SuggestionListProps {
  suggestions: PolicySuggestion[];
  /** id of the suggestion currently under review, for the selected-row style. */
  selectedId?: number | null;
  onSelect: (suggestion: PolicySuggestion) => void;
  onReject: (suggestion: PolicySuggestion) => void;
  /** id of the suggestion currently being rejected, if any (disables its
   *  Reject button + shows a spinner instead of a second click going through). */
  rejectingId?: number | null;
}

/**
 * Chair-review queue for Continual-Experience-Learning policy suggestions
 * (Task 6). Each row is a candidate internal policy learned from a
 * `[CHAIR:]`-gap edit — clicking it seeds the existing add-internal-policy
 * flow (via the knowledge-base page's lifted draft state); Reject is a
 * sibling control, never nested inside the row's own button (mirrors the
 * policy-exclusion "X is a sibling, not nested" convention).
 */
export function SuggestionList({
  suggestions,
  selectedId = null,
  onSelect,
  onReject,
  rejectingId = null,
}: SuggestionListProps) {
  return (
    <ul className="flex flex-col gap-3">
      {suggestions.map((s) => {
        const isSelected = selectedId === s.id;
        const isRejecting = rejectingId === s.id;
        return (
          <li
            key={s.id}
            className="overflow-hidden rounded-lg border"
            style={{
              borderColor: isSelected ? "var(--accent)" : "var(--border)",
              backgroundColor: isSelected ? "var(--accent-subtle)" : "var(--surface-raised)",
            }}
          >
            <button
              type="button"
              onClick={() => onSelect(s)}
              aria-current={isSelected}
              className="w-full px-4 py-3 text-left transition-colors hover:bg-[var(--surface)]"
            >
              <div className="flex items-start justify-between gap-3">
                <p className="min-w-0 break-words text-sm font-medium" style={{ color: "var(--text-primary)" }}>
                  {s.title}
                </p>
                <span className="flex shrink-0 items-center gap-1.5">
                  <Lightbulb className="h-3.5 w-3.5" style={{ color: "var(--accent)" }} aria-hidden />
                  {s.category && (
                    <Badge variant="neutral" size="sm">
                      {s.category}
                    </Badge>
                  )}
                </span>
              </div>
              {/* Candidate policy body — clamped to two lines. pre-wrap because
                  the CEL-condensed content carries literal newlines (list items
                  separated by a single \n, not a blank line), which the CSS
                  default collapses into one run-on paragraph. Same fix as
                  PolicyList / AddPolicyPanel / PolicySelectPopover; this
                  component landed after that pass and was missed. */}
              <p
                className={cn("mt-1 text-xs leading-relaxed line-clamp-2")}
                style={{ color: "var(--text-secondary)", whiteSpace: "pre-wrap" }}
              >
                {s.content}
              </p>
              <p className="mt-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
                Seen {s.seen_count}×
                {s.confidence != null ? ` · confidence ${Math.round(s.confidence * 100)}%` : ""}
                {s.generalizable === false ? " · one-off" : ""}
              </p>
            </button>
            <div className="flex justify-end px-4 pb-2.5">
              <button
                type="button"
                onClick={() => onReject(s)}
                disabled={isRejecting}
                className="rounded-md px-2 py-1 text-xs font-medium transition-colors hover:bg-[var(--danger-subtle)] disabled:opacity-50"
                style={{ color: "var(--danger)" }}
              >
                {isRejecting ? <LoadingSpinner size="sm" /> : "Reject"}
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
