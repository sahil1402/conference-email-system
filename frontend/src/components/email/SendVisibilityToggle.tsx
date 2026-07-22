"use client";

import * as React from "react";
import { Lock, Send } from "lucide-react";

import { cn } from "@/lib/utils";

export interface SendVisibilityToggleProps {
  /**
   * Controlled value: `true` = public reply to the requester, `false` = internal
   * note. Pair with `onChange` to let the parent own the state. Omit both to let
   * the component manage its own state internally (default off = internal note).
   */
  value?: boolean;
  /** Called with the new visibility when the switch is toggled. */
  onChange?: (isPublic: boolean) => void;
  /** Disables the switch (matches the surrounding action controls when in flight). */
  disabled?: boolean;
  /** Optional extra classes on the outer wrapper. */
  className?: string;
}

/**
 * A labeled switch for choosing a reply's visibility before sending: OFF (default)
 * = "Internal note" (not shown to the requester), ON = "Send to requester" (a
 * public reply). Both states are always spelled out in words — the icon is only a
 * secondary cue — so the choice is never ambiguous.
 *
 * Styled with the app's dark-mode CSS-var system: the active track uses the indigo
 * `--accent` (same as the primary Button / SplitActionButton), the inactive track a
 * neutral raised surface. Selection is internal state unless a controlled `value`
 * is supplied. NOTE: this component is presentational only — it does not send or
 * approve anything; the caller reads its value at submit time.
 */
export function SendVisibilityToggle({
  value: controlledValue,
  onChange,
  disabled = false,
  className,
}: SendVisibilityToggleProps) {
  const [internalValue, setInternalValue] = React.useState(false);
  const isControlled = controlledValue !== undefined;
  const isPublic = isControlled ? controlledValue : internalValue;

  function toggle() {
    const next = !isPublic;
    if (!isControlled) setInternalValue(next);
    onChange?.(next);
  }

  const label = isPublic ? "Send to requester" : "Internal note";

  return (
    <div className={cn("inline-flex items-center gap-2", className)}>
      <button
        type="button"
        role="switch"
        aria-checked={isPublic}
        aria-label="Reply visibility: internal note or send to requester"
        disabled={disabled}
        onClick={toggle}
        className={cn(
          "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
          "focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--background)]",
          "disabled:cursor-not-allowed disabled:opacity-50"
        )}
        style={{
          backgroundColor: isPublic ? "var(--accent)" : "var(--surface-raised)",
          border: isPublic ? "none" : "1px solid var(--border)",
        }}
      >
        {/* Sliding knob. */}
        <span
          aria-hidden
          className={cn(
            "inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform",
            isPublic ? "translate-x-6" : "translate-x-1"
          )}
        />
      </button>

      {/* Text label (+ secondary icon) — the icon never stands alone. */}
      <span
        className="inline-flex items-center gap-1.5 text-sm font-medium"
        style={{ color: isPublic ? "var(--accent)" : "var(--text-secondary)" }}
      >
        {isPublic ? (
          <Send className="h-3.5 w-3.5" aria-hidden />
        ) : (
          <Lock className="h-3.5 w-3.5" aria-hidden />
        )}
        {label}
      </span>
    </div>
  );
}
