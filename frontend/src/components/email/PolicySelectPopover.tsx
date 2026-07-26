"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Search } from "lucide-react";

import { Badge, Button, ErrorBanner, LoadingSpinner } from "@/components/ui";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { usePolicies, useRetryEmail } from "@/hooks";
import { cn } from "@/lib/utils";
import type { ApiError, PolicyDocument } from "@/types";

/** Matches the queue search debounce in EmailWorkspace — same feel, same delay. */
const DEBOUNCE_MS = 250;

interface PolicySelectPopoverProps {
  /** Rendered as the popover trigger (the caller owns the button's look). */
  children: React.ReactNode;
  /** Email whose draft will be re-generated with the chosen policy forced in. */
  emailId: number;
  /**
   * Fired AFTER the chair approves and the redraft has been requested — NOT on
   * mere selection. Lets the caller surface which policy is being forced.
   */
  onSelect?: (policyKey: string) => void;
  /** Optional extra classes on the popover panel. */
  className?: string;
}

/**
 * Searchable policy picker in a popover — type, arrow through results, Enter to
 * pick. Purely a selector: it reads the KB and reports a `policy_key` upward;
 * redrafting/wiring lives with the caller (5b/5c).
 *
 * Built on the app's Radix `popover.tsx` (precedent: ShortcutsHintPopover)
 * rather than the hand-rolled PolicyDetailModal, which is a viewer and not
 * reusable as a picker. Radix owns the outside-click/Escape dismissal and
 * focus return; the combobox semantics below are layered on top of it.
 *
 * A11y follows the WAI-ARIA combobox pattern, matching how the app's other
 * Radix wrappers behave: the input keeps DOM focus at all times and the active
 * option is communicated via `aria-activedescendant`, so arrow keys never move
 * focus off the text field. Roles: input `combobox` + `aria-controls`/
 * `aria-expanded`, list `listbox`, rows `option` with `aria-selected`.
 */
export function PolicySelectPopover({
  children,
  emailId,
  onSelect,
  className,
}: PolicySelectPopoverProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  // The two-view state machine. `selected` non-null ⇒ the detail/confirm view.
  // Search state above is deliberately NOT cleared when moving to detail, so
  // "Back" restores the previous query and its (cached) results without a refetch.
  const [selected, setSelected] = useState<PolicyDocument | null>(null);
  const retry = useRetryEmail();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);
  const listboxId = "policy-select-listbox";

  useEffect(() => {
    const t = setTimeout(() => setDebounced(search.trim()), DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [search]);

  // `status: "active"` is applied SERVER-side (GET /policies supports it), so a
  // retired policy is never fetched in the first place. The defensive filter
  // below is a second line only — a chair must never be able to force a
  // withdrawn policy, and the backend rejects it anyway (resolve_forced_chunk).
  const query = useMemo(
    () => ({ status: "active" as const, search: debounced || undefined }),
    [debounced]
  );
  // Disabled until something is typed: an empty box must not fetch the whole KB.
  const { policies, isLoading } = usePolicies(query, { enabled: Boolean(debounced) });

  const results: PolicyDocument[] = useMemo(
    () => (debounced ? policies.filter((p) => p.status === "active") : []),
    [debounced, policies]
  );

  // Any change to the result set resets the highlight to the top.
  useEffect(() => setActiveIndex(0), [debounced, results.length]);

  // Reset everything when the popover closes so it never reopens mid-search.
  useEffect(() => {
    if (!open) {
      setSearch("");
      setDebounced("");
      setActiveIndex(0);
      setSelected(null);
      retry.reset();
    }
    // `retry` is a stable mutation object; depending on it would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  /**
   * Selecting a result opens the confirm step. It deliberately does NOT call the
   * redraft endpoint or `onSelect` — nothing leaves this component until the
   * chair approves, so browsing policies is free of side effects.
   */
  function choose(policy: PolicyDocument | undefined) {
    if (!policy) return;
    setSelected(policy);
  }

  /** The only place a write happens. */
  function approve() {
    if (!selected) return;
    retry.mutate(
      { id: emailId, forcedPolicyKey: selected.policy_key },
      {
        onSuccess: () => {
          onSelect?.(selected.policy_key);
          setOpen(false);
        },
      }
    );
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (results.length === 0) return; // let Escape fall through to Radix
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % results.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (i - 1 + results.length) % results.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(results[activeIndex]);
    } else if (e.key === "Home") {
      e.preventDefault();
      setActiveIndex(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActiveIndex(results.length - 1);
    }
  }

  // Keep the highlighted row in view when arrowing past the scroll edge.
  // Optional-called: scrollIntoView is a purely cosmetic nicety and is absent in
  // some non-browser DOM implementations, where a hard call would throw during
  // render and take the whole picker down.
  useEffect(() => {
    const el = listRef.current?.children[activeIndex] as HTMLElement | undefined;
    el?.scrollIntoView?.({ block: "nearest" });
  }, [activeIndex]);

  let body: React.ReactNode;
  if (!debounced) {
    body = (
      <p className="px-1 py-6 text-center text-xs" style={{ color: "var(--text-muted)" }}>
        Type to search the knowledge base.
      </p>
    );
  } else if (isLoading) {
    body = (
      <div className="flex items-center justify-center gap-2 py-6 text-xs"
           style={{ color: "var(--text-muted)" }}>
        <LoadingSpinner size="sm" />
        Searching…
      </div>
    );
  } else if (results.length === 0) {
    body = (
      <p className="px-1 py-6 text-center text-xs" style={{ color: "var(--text-muted)" }}>
        No active policies match “{debounced}”.
      </p>
    );
  } else {
    body = (
      <ul
        ref={listRef}
        id={listboxId}
        role="listbox"
        aria-label="Matching policies"
        className="max-h-64 space-y-1 overflow-y-auto"
      >
        {results.map((p, i) => (
          <li
            key={p.policy_key}
            id={`policy-option-${p.policy_key}`}
            role="option"
            aria-selected={i === activeIndex}
            // Pointer selection: mousedown (not click) so the input never loses
            // focus first, which would close the popover before selecting.
            onMouseDown={(e) => {
              e.preventDefault();
              choose(p);
            }}
            onMouseEnter={() => setActiveIndex(i)}
            className={cn(
              "cursor-pointer rounded-md border px-2 py-1.5 transition-colors",
              i === activeIndex ? "border-[var(--accent)]" : "border-transparent"
            )}
            style={{
              backgroundColor:
                i === activeIndex ? "var(--accent-subtle)" : "transparent",
            }}
          >
            <p className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
              {p.title || p.policy_key}
            </p>
            <p className="line-clamp-2 text-[11px] leading-relaxed"
               style={{ color: "var(--text-secondary)" }}>
              {p.content}
            </p>
          </li>
        ))}
      </ul>
    );
  }

  // --- Detail / confirm view -------------------------------------------------
  // Visual treatment mirrors PolicyDetailModal's body: monospace key, category
  // Badge, and the full text with pre-wrap/break-word. Deliberately NOT clamped —
  // this is the step where the chair decides, so they must see everything.
  const error = retry.error as ApiError | null;
  const detailView = selected && (
    <div className="space-y-3">
      <div className="flex items-start gap-2">
        <button
          type="button"
          onClick={() => setSelected(null)}
          disabled={retry.isPending}
          aria-label="Back to search"
          className="mt-0.5 shrink-0 rounded-md p-1 transition-colors hover:bg-[var(--surface)] disabled:opacity-50"
          style={{ color: "var(--text-secondary)" }}
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="min-w-0 flex-1">
          <p className="font-mono text-[11px]" style={{ color: "var(--text-muted)" }}>
            {selected.policy_key}
          </p>
          <p className="text-xs font-semibold leading-snug"
             style={{ color: "var(--text-primary)" }}>
            {selected.title || selected.policy_key}
          </p>
        </div>
      </div>

      {selected.category && (
        <Badge variant="neutral" size="sm">
          {selected.category}
        </Badge>
      )}

      <div className="max-h-56 overflow-y-auto rounded-md border p-2"
           style={{ borderColor: "var(--border-subtle)", backgroundColor: "var(--surface)" }}>
        <p
          className="text-[11px] leading-relaxed"
          style={{
            color: "var(--text-primary)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {selected.content}
        </p>
      </div>

      {error && (
        <ErrorBanner message={error.detail || "Couldn't start the re-draft."} />
      )}

      <div className="flex items-center gap-2">
        <Button type="button" size="sm" onClick={approve} disabled={retry.isPending}>
          {retry.isPending ? <LoadingSpinner size="sm" /> : null}
          {retry.isPending ? "Re-drafting…" : "Approve"}
        </Button>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => setSelected(null)}
          disabled={retry.isPending}
        >
          Change
        </Button>
      </div>
    </div>
  );

  const searchView = (
    <>
      <div className="mb-2 flex items-center gap-2 rounded-lg border px-2 py-1.5"
           style={{ borderColor: "var(--border)", backgroundColor: "var(--surface)" }}>
        <Search className="h-3.5 w-3.5 shrink-0" style={{ color: "var(--text-muted)" }} aria-hidden />
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded={results.length > 0}
          aria-controls={listboxId}
          aria-autocomplete="list"
          aria-activedescendant={
            results.length > 0
              ? `policy-option-${results[activeIndex]?.policy_key}`
              : undefined
          }
          aria-label="Search policies"
          placeholder="Search policies…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={onKeyDown}
          className="w-full bg-transparent text-xs outline-none"
          style={{ color: "var(--text-primary)" }}
        />
      </div>
      {body}
    </>
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent
        align="end"
        className={cn("w-80 p-2", className)}
        // Radix would focus the panel; keep the caret in the search box instead.
        // Only meaningful for the search view — detail has no text entry.
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          inputRef.current?.focus();
        }}
      >
        {selected ? detailView : searchView}
      </PopoverContent>
    </Popover>
  );
}
