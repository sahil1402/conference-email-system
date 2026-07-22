"use client";

import { Keyboard } from "lucide-react";

import { Kbd } from "@/components/ui";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

/** One shortcut row: the key(s) on the left, what it does on the right. */
const SHORTCUTS: { keys: string[]; label: string }[] = [
  { keys: ["Ctrl", "Alt", "S"], label: "approve" },
  { keys: ["E"], label: "edit" },
  { keys: ["C"], label: "reassign" },
  { keys: ["R"], label: "reroute" },
];

interface ShortcutsHintPopoverProps {
  /** Optional extra classes on the trigger button (layout flexibility). */
  className?: string;
}

/**
 * Icon-only trigger that pops open the review-pane keyboard shortcuts. Replaces
 * the always-visible hint row, which collided with the action buttons on
 * narrower screens — collapsing it into a popover takes it out of the flex flow.
 *
 * Self-contained: the shortcut list mirrors EmailDetail's keydown handler, so
 * keep the two in sync if a binding changes.
 */
export function ShortcutsHintPopover({ className }: ShortcutsHintPopoverProps) {
  return (
    <Popover>
      <PopoverTrigger
        aria-label="Keyboard shortcuts"
        className={cn(
          "inline-flex h-8 w-8 items-center justify-center rounded-lg border transition-colors",
          "border-[var(--border)] bg-[var(--surface-raised)] text-[var(--text-secondary)]",
          "hover:border-[var(--accent)] hover:bg-[var(--accent-subtle)] hover:text-[var(--accent)]",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--background)]",
          className
        )}
      >
        <Keyboard className="h-4 w-4" aria-hidden />
      </PopoverTrigger>

      <PopoverContent align="end" className="w-64">
        <p
          className="text-xs font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          Keyboard shortcuts
        </p>
        <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
          Work when an email is open and you&apos;re not typing in a field.
        </p>

        <ul className="mt-3 flex flex-col gap-2">
          {SHORTCUTS.map(({ keys, label }) => (
            <li
              key={label}
              className="flex items-center justify-between gap-3 text-xs"
              style={{ color: "var(--text-secondary)" }}
            >
              <span className="inline-flex items-center gap-1">
                {keys.map((k, i) => (
                  <span key={k} className="inline-flex items-center gap-1">
                    {i > 0 && <span aria-hidden>+</span>}
                    <Kbd>{k}</Kbd>
                  </span>
                ))}
              </span>
              {label}
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
