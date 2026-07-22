"use client";

import * as React from "react";
import { ChevronDown } from "lucide-react";

import { Button } from "./button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "./dropdown-menu";
import { LoadingSpinner } from "./LoadingSpinner";
import { cn } from "@/lib/utils";
import { zendeskStatusColor } from "@/lib/zendesk-status";

/** The resulting Zendesk status the button submits as. */
export type SplitActionStatus = "open" | "pending" | "solved";

/** The three dropdown options, in Zendesk's native submit order. */
const STATUS_OPTIONS: { value: SplitActionStatus; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "pending", label: "Pending" },
  { value: "solved", label: "Solved" },
];

const LABEL_BY_STATUS: Record<SplitActionStatus, string> = {
  open: "Open",
  pending: "Pending",
  solved: "Solved",
};

/** Default when the caller supplies no selection (mirrors Zendesk's common case). */
const DEFAULT_STATUS: SplitActionStatus = "solved";

export interface SplitActionButtonProps {
  /** Fired on every primary-button click with the currently selected status. */
  onAction: (status: SplitActionStatus) => void;
  /** Disables both the primary button and the dropdown trigger. */
  disabled?: boolean;
  /**
   * Shows a spinner in the primary button and disables both controls. Use for an
   * in-flight action (e.g. an approve/send awaiting the server).
   */
  loading?: boolean;
  /** Optional extra classes on the outer wrapper. */
  className?: string;
  /**
   * Controlled selected status. When provided (paired with `onSelectedChange`),
   * the parent owns the selection — useful when something outside the button
   * (e.g. a keyboard shortcut, or a persisted preference) drives it. Omit both
   * to let the component manage selection internally.
   */
  selected?: SplitActionStatus;
  /** Called when a status is picked from the dropdown (controlled mode). */
  onSelectedChange?: (status: SplitActionStatus) => void;
}

/**
 * A split button mirroring Zendesk's "Submit as …" control: a primary action on
 * the left that submits as the currently selected status, plus a chevron
 * dropdown offering Open / Pending / Solved. The primary label is always
 * "Submit as {Status}" (default Solved) — there is no separate "approve & send"
 * mode; picking a status changes what the primary submits as and persists as the
 * active choice. The primary keeps a fixed width so it doesn't resize between
 * statuses. Generic by design — nothing here is bound to the email context.
 */
export function SplitActionButton({
  onAction,
  disabled = false,
  loading = false,
  className,
  selected: controlledSelected,
  onSelectedChange,
}: SplitActionButtonProps) {
  const [internalSelected, setInternalSelected] =
    React.useState<SplitActionStatus>(DEFAULT_STATUS);
  const isControlled = controlledSelected !== undefined;
  const selected = isControlled ? controlledSelected : internalSelected;

  function selectStatus(status: SplitActionStatus) {
    if (!isControlled) setInternalSelected(status);
    onSelectedChange?.(status);
  }

  const label = `Submit as ${LABEL_BY_STATUS[selected]}`;
  // A loading action is also a disabled one (both controls inert while in flight).
  const isDisabled = disabled || loading;

  return (
    <div className={cn("inline-flex items-stretch", className)}>
      {/* Primary action — indigo accent (Button default variant). Fixed min-width
          + centered so the label swapping between statuses never resizes it. */}
      <Button
        type="button"
        disabled={isDisabled}
        onClick={() => onAction(selected)}
        className="min-w-[10rem] justify-center rounded-r-none"
      >
        {loading && (
          <LoadingSpinner size="sm" className="!text-[var(--text-primary)]" />
        )}
        {label}
      </Button>

      {/* Dropdown trigger — same accent surface, joined to the primary with a
          hairline divider so the pair reads as one control. */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            size="icon"
            disabled={isDisabled}
            aria-label="Choose a resulting status"
            className="w-9 rounded-l-none border-l border-white/25 px-0"
          >
            <ChevronDown className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-[9rem]">
          {STATUS_OPTIONS.map((opt) => (
            <DropdownMenuItem
              key={opt.value}
              onSelect={() => selectStatus(opt.value)}
            >
              <span
                aria-hidden
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: zendeskStatusColor(opt.value) }}
              />
              <span className="flex-1">{opt.label}</span>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
