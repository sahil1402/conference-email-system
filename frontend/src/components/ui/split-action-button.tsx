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
import { cn } from "@/lib/utils";
import { zendeskStatusColor } from "@/lib/zendesk-status";

/**
 * The status a split-action can resolve to. `null` = no status chosen yet
 * (the default "Approve & Send" mode → a plain action with no status change).
 */
export type SplitActionStatus = "open" | "pending" | "solved";

/**
 * The three dropdown options, in Zendesk's native submit order. Dot colors come
 * from the shared ZENDESK_STATUS_COLORS source of truth (app-harmonized palette,
 * same as ZendeskStatusBar) — see zendeskStatusColor in the render below.
 */
const STATUS_OPTIONS: {
  value: SplitActionStatus;
  label: string;
}[] = [
  { value: "open", label: "Open" },
  { value: "pending", label: "Pending" },
  { value: "solved", label: "Solved" },
];

const LABEL_BY_STATUS: Record<SplitActionStatus, string> = {
  open: "Open",
  pending: "Pending",
  solved: "Solved",
};

export interface SplitActionButtonProps {
  /**
   * Fired on every primary-button click with the currently selected status,
   * or `null` when none has been picked (the default "Approve & Send" mode).
   */
  onAction: (status: SplitActionStatus | null) => void;
  /** Primary label shown before any status is selected. */
  defaultLabel?: string;
  /** Disables both the primary button and the dropdown trigger. */
  disabled?: boolean;
  /** Optional extra classes on the outer wrapper. */
  className?: string;
}

/**
 * A split button: a primary action on the left + a chevron dropdown on the
 * right offering three resulting statuses (Open / Pending / Solved), mirroring
 * Zendesk's native "Submit as …" control.
 *
 * Selecting a status persists it as the active mode — the primary label becomes
 * "Submit as {Status}" and stays there until another is chosen — and clicking
 * the primary button always fires `onAction(selectedStatus)` (or `onAction(null)`
 * in the untouched default state). Selection is internal state; the caller only
 * needs the value at click time. Generic by design — nothing here is bound to
 * the email-review context.
 */
export function SplitActionButton({
  onAction,
  defaultLabel = "Approve & Send",
  disabled = false,
  className,
}: SplitActionButtonProps) {
  const [selected, setSelected] = React.useState<SplitActionStatus | null>(null);

  const label = selected ? `Submit as ${LABEL_BY_STATUS[selected]}` : defaultLabel;

  return (
    <div className={cn("inline-flex items-stretch", className)}>
      {/* Primary action — indigo accent (Button default variant). */}
      <Button
        type="button"
        disabled={disabled}
        onClick={() => onAction(selected)}
        className="rounded-r-none"
      >
        {label}
      </Button>

      {/* Dropdown trigger — same accent surface, joined to the primary with a
          hairline divider so the pair reads as one control. */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            size="icon"
            disabled={disabled}
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
              onSelect={() => setSelected(opt.value)}
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
