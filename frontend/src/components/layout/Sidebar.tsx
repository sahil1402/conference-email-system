"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Inbox,
  BarChart2,
  Zap,
  ClipboardList,
  Mail,
  Library,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useSidebarSlot } from "./SidebarSlot";

type NavItem = {
  label: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
};

const NAV_ITEMS: NavItem[] = [
  { label: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
  { label: "Email Queue", href: "/queue", icon: Inbox },
  { label: "Knowledge Base", href: "/knowledge-base", icon: Library },
  { label: "Analytics", href: "/analytics", icon: BarChart2 },
  { label: "Auto-Replies", href: "/auto-replies", icon: Zap },
  { label: "Audit Log", href: "/audit", icon: ClipboardList },
];

interface SidebarProps {
  /** Mobile open state (ignored at ≥md, where the sidebar is always shown). */
  open?: boolean;
  /** Called when a nav link is clicked — lets the parent close the mobile drawer. */
  onNavigate?: () => void;
}

/**
 * Fixed left nav rail (width from the --rail-width token): brand mark, primary
 * navigation, footer.
 * Always visible at ≥md; on mobile it slides in from the left, gated by `open`.
 */
export function Sidebar({ open = false, onNavigate }: SidebarProps) {
  const pathname = usePathname();
  const { setSlotEl } = useSidebarSlot();

  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-40 flex w-[var(--rail-width)] flex-col transition-transform duration-200 md:translate-x-0",
        open ? "translate-x-0" : "-translate-x-full"
      )}
      style={{
        backgroundColor: "var(--surface)",
        borderRight: "1px solid var(--border)",
      }}
    >
      {/* Brand */}
      <div className="flex flex-col gap-0.5 px-5 py-5">
        <div className="flex items-center gap-2.5">
          <div
            className="flex h-7 w-7 items-center justify-center rounded-lg"
            style={{ backgroundColor: "var(--accent)", color: "var(--text-primary)" }}
          >
            <Mail className="h-4 w-4" />
          </div>
          <span
            className="text-base font-semibold tracking-tight"
            style={{ color: "var(--text-primary)" }}
          >
            ConfMail
          </span>
        </div>
        <span
          className="pl-[2.375rem] text-xs"
          style={{ color: "var(--text-muted)" }}
        >
          Conference Email System
        </span>
      </div>

      {/* Navigation + page slot (queue filters) — scroll together when tall */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        {/* ONE provider for the whole rail: sharing it enables Radix's skip
            delay, so moving between adjacent icons shows the next label
            instantly instead of re-waiting the full delay. Renders no DOM. */}
        <TooltipProvider>
        <nav className="space-y-1 px-2 py-2">
          {NAV_ITEMS.map(({ label, href, icon: Icon }) => {
          const isActive =
            pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Tooltip key={href}>
            <TooltipTrigger asChild>
            <Link
              href={href}
              onClick={onNavigate}
              // Icon-only: the visible label is gone, so the name comes from
              // aria-label. The tooltip is additive on top of it, never a
              // replacement — screen readers rely on the aria-label.
              aria-label={label}
              className={cn(
                // No left-border indicator: at 36x36 a single-side border is
                // mostly corner-radius arc, sits 8px inboard of the rail edge
                // (so it no longer reads as an edge marker), and — because
                // preflight sets border-box — steals 2px from the width,
                // nudging the icon 1px off-centre on active items only. The
                // filled --accent-subtle square + --accent glyph carries it.
                "group flex h-9 w-9 items-center justify-center rounded-lg transition-colors duration-150",
                // Hover feedback only on inactive items — an active item keeps
                // its --accent-subtle background (matches the previous
                // imperative `if (!isActive)` guard). Reuses the
                // transition-colors above rather than adding a second one.
                !isActive && "hover:bg-[var(--surface-raised)]"
              )}
              style={
                isActive
                  ? {
                      backgroundColor: "var(--accent-subtle)",
                      color: "var(--accent)",
                    }
                  : {
                      color: "var(--text-secondary)",
                    }
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
            </Link>
            </TooltipTrigger>
            {/* Portalled (see tooltip.tsx) so it escapes this rail's
                overflow-y-auto instead of being clipped by it. */}
            <TooltipContent side="right">{label}</TooltipContent>
            </Tooltip>
          );
        })}
        </nav>
        </TooltipProvider>
        {/* Page-provided slot (queue filters): pinned above the footer when
            there's spare height; scrolls with the nav when space is tight. */}
        <div ref={setSlotEl} className="mt-auto" />
      </div>

      {/* Footer — centred in the rail. No horizontal padding: ThemeToggle is
          still 52px wide, exactly the rail width, so any side padding would
          overflow. Padding can return once N2h makes it icon-sized. */}
      <div
        className="flex shrink-0 items-center justify-center py-4"
        style={{ borderTop: "1px solid var(--border-subtle)" }}
      >
        <ThemeToggle />
      </div>
    </aside>
  );
}
