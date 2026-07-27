"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Which page tokens to render: always the first + last page and the current
 * page ± 1 sibling, with `"ellipsis"` standing in for a run of hidden pages.
 * A gap of exactly ONE hidden page is shown as the number itself (a lone "…"
 * replacing a single page is pointless). e.g. page 4 of 18 → 1 2 3 4 5 … 18.
 *
 * Exported for unit testing the truncation independently of rendering.
 */
export function getPageItems(
  page: number,
  pageCount: number
): (number | "ellipsis")[] {
  if (pageCount <= 1) return pageCount === 1 ? [1] : [];
  // Few enough pages to show every one — no truncation needed.
  if (pageCount <= 7) {
    return Array.from({ length: pageCount }, (_, i) => i + 1);
  }

  const SIBLINGS = 1;
  const shown = new Set<number>([1, pageCount]);
  for (let p = page - SIBLINGS; p <= page + SIBLINGS; p++) {
    if (p >= 1 && p <= pageCount) shown.add(p);
  }
  const sorted = Array.from(shown).sort((a, b) => a - b);

  const items: (number | "ellipsis")[] = [];
  let prev = 0;
  for (const cur of sorted) {
    if (prev) {
      const gap = cur - prev;
      if (gap === 2) items.push(prev + 1); // collapse a single hidden page
      else if (gap > 2) items.push("ellipsis");
    }
    items.push(cur);
    prev = cur;
  }
  return items;
}

interface PaginationProps {
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
}

const btnBase =
  "inline-flex h-6 min-w-6 items-center justify-center rounded-md px-1 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40";

/**
 * Numbered page controls with prev/next chevrons and "…" truncation. The
 * current page is outlined in the accent colour. Renders nothing for a single
 * page. Purely presentational — `onPageChange` owns the fetch.
 */
export function Pagination({ page, pageCount, onPageChange }: PaginationProps) {
  if (pageCount <= 1) return null;
  const items = getPageItems(page, pageCount);

  return (
    <nav
      aria-label="Pagination"
      className="flex flex-wrap items-center gap-1"
    >
      <button
        type="button"
        aria-label="Previous page"
        disabled={page <= 1}
        onClick={() => onPageChange(page - 1)}
        className={cn(btnBase, "hover:bg-[var(--surface-raised)]")}
        style={{ color: "var(--text-secondary)" }}
      >
        <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
      </button>

      {items.map((item, i) =>
        item === "ellipsis" ? (
          <span
            key={`ellipsis-${i}`}
            aria-hidden
            className="px-0.5 text-xs"
            style={{ color: "var(--text-muted)" }}
          >
            …
          </span>
        ) : (
          <button
            key={item}
            type="button"
            aria-label={`Page ${item}`}
            aria-current={item === page ? "page" : undefined}
            onClick={() => onPageChange(item)}
            className={cn(
              btnBase,
              "border",
              item === page
                ? "border-[var(--accent)] font-medium"
                : "border-transparent hover:bg-[var(--surface-raised)]"
            )}
            style={{
              color:
                item === page ? "var(--accent)" : "var(--text-secondary)",
              backgroundColor:
                item === page ? "var(--accent-subtle)" : undefined,
            }}
          >
            {item}
          </button>
        )
      )}

      <button
        type="button"
        aria-label="Next page"
        disabled={page >= pageCount}
        onClick={() => onPageChange(page + 1)}
        className={cn(btnBase, "hover:bg-[var(--surface-raised)]")}
        style={{ color: "var(--text-secondary)" }}
      >
        <ChevronRight className="h-3.5 w-3.5" aria-hidden />
      </button>
    </nav>
  );
}
