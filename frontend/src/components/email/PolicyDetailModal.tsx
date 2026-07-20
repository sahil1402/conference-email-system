"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FileText, X } from "lucide-react";

import { Badge, ErrorBanner, LoadingSpinner } from "@/components/ui";
import { usePolicy } from "@/hooks";

interface PolicyDetailModalProps {
  /** The cited policy key to show, or null when the modal is closed. */
  policyKey: string | null;
  onClose: () => void;
}

/**
 * Citation-detail popup: given a cited policy key, fetches and shows the full
 * chunk (source document, id, tags, full text). A lightweight custom dialog
 * (no new dependency) matching the app's CSS-var design system.
 *
 * Accessibility: role="dialog" + aria-modal, focus moved in on open and
 * restored on close, focus trapped to the dialog while open, and three ways to
 * dismiss — Escape, backdrop click, and the explicit ✕ button.
 */
export function PolicyDetailModal({ policyKey, onClose }: PolicyDetailModalProps) {
  const open = policyKey != null;
  const { policy, isLoading, isError } = usePolicy(policyKey);

  // Portal target only exists on the client; gate to avoid SSR `document` refs.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);
  // The element focused before the modal opened, to restore on close.
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;

    restoreFocusRef.current = document.activeElement as HTMLElement | null;
    // Move focus into the dialog once it paints.
    requestAnimationFrame(() => closeBtnRef.current?.focus());

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      // Trap focus within the dialog.
      const focusables = dialogRef.current?.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (!focusables || focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    // Prevent the page behind from scrolling while the modal is open.
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = prevOverflow;
      restoreFocusRef.current?.focus?.();
    };
  }, [open, onClose]);

  if (!mounted || !open) return null;

  const titleId = "policy-detail-title";

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: "rgba(0, 0, 0, 0.6)" }}
      onMouseDown={(e) => {
        // Backdrop click closes; clicks inside the dialog do not bubble here.
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl border shadow-2xl"
        style={{
          backgroundColor: "var(--surface)",
          borderColor: "var(--border)",
        }}
      >
        {/* Header */}
        <div
          className="flex items-start justify-between gap-3 border-b p-4"
          style={{ borderColor: "var(--border-subtle)" }}
        >
          <div className="min-w-0 space-y-1">
            <div
              className="flex items-center gap-2 text-xs font-medium"
              style={{ color: "var(--text-muted)" }}
            >
              <FileText className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">
                {policy?.source ?? "Policy corpus"}
              </span>
              <span aria-hidden>·</span>
              <span className="font-mono">{policyKey}</span>
            </div>
            <h2
              id={titleId}
              className="text-lg font-semibold leading-snug"
              style={{ color: "var(--text-primary)" }}
            >
              {policy?.title ?? "Policy detail"}
            </h2>
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            onClick={onClose}
            aria-label="Close policy detail"
            className="shrink-0 rounded-md p-1 transition-colors hover:bg-[var(--surface-raised)]"
            style={{ color: "var(--text-secondary)" }}
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Body (scrolls if the policy text is long) */}
        <div className="flex-1 overflow-y-auto p-4">
          {isLoading && (
            <div className="flex items-center gap-2 py-6 text-sm" style={{ color: "var(--text-muted)" }}>
              <LoadingSpinner size="sm" />
              Loading policy…
            </div>
          )}

          {isError && (
            <ErrorBanner message={`Could not load ${policyKey}. It may not be in the current corpus.`} />
          )}

          {policy && !isLoading && !isError && (
            <div className="space-y-4">
              {/* [tags-dropped E007] tags removed; category badge retained. */}
              {policy.category && (
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="neutral" size="sm">
                    {policy.category}
                  </Badge>
                  {/* [tags-dropped E007] policy.tags.map(...) badges removed */}
                </div>
              )}
              <p
                className="text-sm leading-relaxed"
                style={{
                  color: "var(--text-primary)",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {policy.content}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}
