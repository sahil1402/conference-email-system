"use client";

import { ChevronDown } from "lucide-react";

import { Button } from "@/components/ui";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import { cn } from "@/lib/utils";
import { zendeskStatusColor } from "@/lib/zendesk-status";

/** The Zendesk statuses this control can set WITHOUT sending a reply. */
export type NoReplyStatus = "new" | "open" | "solved";

/** Dropdown options, in Zendesk's native order. */
const STATUS_OPTIONS: { value: NoReplyStatus; label: string }[] = [
  { value: "new", label: "Mark as new" },
  { value: "open", label: "Mark as open" },
  { value: "solved", label: "Mark as solved" },
];

/**
 * A secondary split button for resolving a ticket's Zendesk status WITHOUT
 * sending any reply. The primary button always marks the ticket **solved** (the
 * common "no response warranted" case — the one wired to the Ctrl+Alt+X
 * shortcut); the chevron dropdown offers the other no-reply states (new / open /
 * solved), each firing immediately.
 *
 * Styled `outline` so it reads as secondary next to the indigo "Submit as …"
 * reply button. Mirrors SplitActionButton's structure, but its primary action is
 * fixed (solved) rather than following a selected status.
 */
export function SetTicketStatusButton({
  onSetStatus,
  disabled = false,
  loading = false,
  className,
}: {
  onSetStatus: (status: NoReplyStatus) => void;
  disabled?: boolean;
  loading?: boolean;
  className?: string;
}) {
  const isDisabled = disabled || loading;

  return (
    <div className={cn("inline-flex items-stretch", className)}>
      <Button
        type="button"
        variant="outline"
        disabled={isDisabled}
        onClick={() => onSetStatus("solved")}
        className="justify-center rounded-r-none"
      >
        {loading && (
          <LoadingSpinner size="sm" className="!text-[var(--text-primary)]" />
        )}
        Mark solved · no reply
      </Button>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="icon"
            disabled={isDisabled}
            aria-label="Set another status without replying"
            className="w-9 rounded-l-none border-l-0 px-0"
          >
            <ChevronDown className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-[10rem]">
          {STATUS_OPTIONS.map((opt) => (
            <DropdownMenuItem
              key={opt.value}
              onSelect={() => onSetStatus(opt.value)}
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
