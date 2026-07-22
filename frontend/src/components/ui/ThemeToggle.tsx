"use client";

import { Moon, Sun } from "lucide-react";

import { useTheme } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";

export interface ThemeToggleProps {
  /** Optional extra classes on the switch button (layout flexibility for callers). */
  className?: string;
}

/**
 * Pill switch that flips the app between dark (knob left, Moon) and light (knob
 * right, Sun). Self-contained — reads and mutates the theme via `useTheme()`, so
 * callers just drop it in with no props.
 *
 * Styled entirely with the app's CSS-var palette (no hardcoded hex): the active
 * (light) track uses the indigo `--accent`, the inactive (dark) track a neutral
 * raised surface; the knob is `--surface` with a subtle shadow and a
 * `--text-primary` icon, so the control re-colors correctly in either theme.
 */
export function ThemeToggle({ className }: ThemeToggleProps) {
  const { theme, toggleTheme } = useTheme();
  const isLight = theme === "light";

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
