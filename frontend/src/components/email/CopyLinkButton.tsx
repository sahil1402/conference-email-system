"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy, X } from "lucide-react";

import { cn } from "@/lib/utils";

interface CopyLinkButtonProps {
  /**
   * The Zendesk ticket id the shareable URL points at (Email.zendesk_ticket_id).
   * Null/undefined for non-Zendesk rows → the button self-suppresses, mirroring
   * ZendeskLinkButton.
   */
  ticketId: number | null | undefined;
  className?: string;
}

type CopyState = "idle" | "copied" | "error";

/**
 * Copies the shareable ticket URL (`${origin}/tickets/${ticketId}`) to the
 * clipboard and briefly swaps its own label to confirm — the app has no toast
 * infrastructure (inline confirmations only), so the confirmation lives on the
 * button. Owns its "render nothing when there's no ticket" logic, so callers can
 * pass the raw (possibly null) id without a surrounding conditional.
 */
export function CopyLinkButton({ ticketId, className }: CopyLinkButtonProps) {
  const [copyState, setCopyState] = useState<CopyState>("idle");
  // Clear the revert timer on unmount so we never setState on an unmounted node.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    []
  );

  if (ticketId == null) return null;

  const handleCopy = async () => {
    // Origin (not a hardcoded host), so the link is correct in every
    // environment (localhost, staging, prod).
    const url = `${window.location.origin}/tickets/${ticketId}`;
    try {
      // navigator.clipboard is undefined in insecure/older contexts; accessing
      // writeText there throws synchronously and is caught here too.
      await navigator.clipboard.writeText(url);
      setCopyState("copied");
    } catch {
      // Restricted clipboard (permissions / non-secure context) — fail quietly
      // with a brief notice rather than throwing out of the handler.
      setCopyState("error");
    }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopyState("idle"), 2000);
  };

  const label =
    copyState === "copied"
      ? "Copied!"
      : copyState === "error"
        ? "Copy failed"
        : "Copy link";
  const Icon = copyState === "copied" ? Check : copyState === "error" ? X : Copy;

  return (
    <button
      type="button"
      onClick={handleCopy}
      aria-label="Copy shareable ticket link"
      className={cn(
        // Matches ZendeskLinkButton's pill so the two sit together in the badge
        // row; a border + hover accent shift mark it as an action.
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-1 text-[11px] font-medium leading-none transition-colors",
        copyState === "copied"
          ? "border-[var(--success)] bg-[var(--success-subtle)] text-[var(--success)]"
          : copyState === "error"
            ? "border-[var(--danger)] bg-[var(--danger-subtle)] text-[var(--danger)]"
            : "border-[var(--border)] bg-[var(--surface-raised)] text-[var(--text-secondary)] hover:border-[var(--accent)] hover:bg-[var(--accent-subtle)] hover:text-[var(--accent)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--background)]",
        className
      )}
    >
      <Icon className="h-3 w-3" aria-hidden />
      {label}
    </button>
  );
}
