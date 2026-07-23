"use client";

import { Moon, Sun } from "lucide-react";

import { useTheme } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export interface ThemeToggleProps {
  /** Optional extra classes on the switch button (layout flexibility for callers). */
  className?: string;
  /**
   * Render as a 36x36 icon-only button instead of the horizontal pill — sized
   * to match the nav rail's icon targets. Shows the current theme's icon with a
   * tooltip describing the action.
   */
  compact?: boolean;
}

/**
 * Switches the app between dark and light. Self-contained — reads and mutates
 * the theme via `useTheme()`, so callers just drop it in with no props.
 *
 * Two visual variants over the SAME toggle logic:
 *  - default: a 52x28 pill whose knob slides between Moon (dark) and Sun (light)
 *  - `compact`: a 36x36 icon button matching the nav rail, with a tooltip
 *
 * Styled entirely with the app's CSS-var palette (no hardcoded hex), so the
 * control re-colors correctly in either theme.
 */
export function ThemeToggle({ className, compact = false }: ThemeToggleProps) {
  const { theme, toggleTheme } = useTheme();
  const isLight = theme === "light";
  // The icon shows the CURRENT theme; the label describes what a click does.
  const actionLabel = isLight ? "Switch to dark mode" : "Switch to light mode";

  if (compact) {
    return (
      // Self-providing: the label depends on this component's own useTheme()
      // state (useTheme isn't a shared store, so a parent reading it would go
      // stale after a toggle), and providing here keeps the component free of a
      // hidden "needs a TooltipProvider ancestor" requirement.
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              role="switch"
              aria-checked={isLight}
              aria-label={actionLabel}
              onClick={toggleTheme}
              className={cn(
                "flex h-9 w-9 cursor-pointer items-center justify-center rounded-lg transition-colors duration-150",
                "hover:bg-[var(--surface-raised)]",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
                "focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--background)]",
                className
              )}
              style={{ color: "var(--text-secondary)" }}
            >
              {isLight ? (
                <Sun className="h-4 w-4" aria-hidden />
              ) : (
                <Moon className="h-4 w-4" aria-hidden />
              )}
            </button>
          </TooltipTrigger>
          <TooltipContent side="right">{actionLabel}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  return (
    <button
      type="button"
      role="switch"
      aria-checked={isLight}
      aria-label="Toggle light/dark theme"
      onClick={toggleTheme}
      className={cn(
        "relative inline-flex h-7 w-[52px] shrink-0 cursor-pointer items-center rounded-full transition-colors duration-200 ease-in-out",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        "focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--background)]",
        className
      )}
      style={{
        backgroundColor: isLight ? "var(--accent)" : "var(--surface-raised)",
        border: isLight ? "none" : "1px solid var(--border)",
      }}
    >
      {/* Sliding knob with the current-theme icon inside. */}
      <span
        aria-hidden
        className={cn(
          "absolute flex h-6 w-6 transform items-center justify-center rounded-full transition-transform duration-200 ease-in-out",
          isLight ? "translate-x-[26px]" : "translate-x-[2px]"
        )}
        style={{
          backgroundColor: "var(--surface)",
          boxShadow: "0 1px 3px rgba(0, 0, 0, 0.25)",
          color: "var(--text-primary)",
        }}
      >
        {isLight ? (
          <Sun className="h-3.5 w-3.5" />
        ) : (
          <Moon className="h-3.5 w-3.5" />
        )}
      </span>
    </button>
  );
}
