import { cn } from "@/lib/utils";

interface DesktopTopBarProps {
  /**
   * Extra classes for the call site — notably z-index, which AppShell owns
   * (BR4) so this component stays layout-position-only.
   */
  className?: string;
}

/**
 * Full-width top bar carrying the app brand text, shown on desktop only. It
 * begins after the nav rail (`left-[var(--rail-width)]`) rather than over it,
 * and is the inverse of AppShell's mobile top bar (`md:hidden`), so exactly one
 * of the two is visible at any width.
 *
 * Title + subtitle sit side by side on one baseline — a 56px bar spanning the
 * wide content area reads better as a horizontal lockup than as two stacked
 * lines (the old sidebar block stacked them only because it was 240px narrow).
 */
export function DesktopTopBar({ className }: DesktopTopBarProps) {
  return (
    <header
      className={cn(
        // left/right set explicitly (not inset-x-0 + left-…, which would be an
        // equal-specificity conflict) so the bar starts flush after the rail.
        "fixed right-0 top-0 left-[var(--rail-width)] hidden h-[var(--topbar-height)] items-center gap-2 px-6 md:flex",
        className
      )}
      style={{
        backgroundColor: "var(--surface)",
        borderBottom: "1px solid var(--border)",
      }}
    >
      <span
        className="text-base font-semibold tracking-tight"
        style={{ color: "var(--text-primary)" }}
      >
        ConfMail
      </span>
      <span aria-hidden style={{ color: "var(--text-muted)" }}>
        ·
      </span>
      <span className="text-xs" style={{ color: "var(--text-muted)" }}>
        Conference Email System
      </span>
    </header>
  );
}
