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
 * Fixed 240px left sidebar: brand mark, primary navigation, footer.
 * Always visible at ≥md; on mobile it slides in from the left, gated by `open`.
 */
export function Sidebar({ open = false, onNavigate }: SidebarProps) {
  const pathname = usePathname();
  const { setSlotEl } = useSidebarSlot();

  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-40 flex w-60 flex-col transition-transform duration-200 md:translate-x-0",
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
        <nav className="space-y-1 px-3 py-2">
          {NAV_ITEMS.map(({ label, href, icon: Icon }) => {
          const isActive =
            pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              onClick={onNavigate}
              className="group flex items-center gap-3 rounded-lg border-l-2 py-2 pr-3 text-sm font-medium transition-colors duration-150"
              style={
                isActive
                  ? {
                      backgroundColor: "var(--accent-subtle)",
                      color: "var(--accent)",
                      borderLeftColor: "var(--accent)",
                      paddingLeft: "calc(0.75rem - 2px)",
                    }
                  : {
                      color: "var(--text-secondary)",
                      borderLeftColor: "transparent",
                      paddingLeft: "calc(0.75rem - 2px)",
                    }
              }
              onMouseEnter={(e) => {
                if (!isActive)
                  e.currentTarget.style.backgroundColor =
                    "var(--surface-raised)";
              }}
              onMouseLeave={(e) => {
                if (!isActive)
                  e.currentTarget.style.backgroundColor = "transparent";
              }}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </Link>
          );
        })}
        </nav>
        {/* Page-provided slot (queue filters): pinned above the footer when
            there's spare height; scrolls with the nav when space is tight. */}
        <div ref={setSlotEl} className="mt-auto" />
      </div>

      {/* Footer */}
      <div
        className="flex shrink-0 items-center justify-between px-5 py-4"
        style={{ borderTop: "1px solid var(--border-subtle)" }}
      >
        <div className="flex flex-col gap-0.5">
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            Melady Lab · USC
          </span>
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            v0.1.0-mvp
          </span>
        </div>
        <ThemeToggle />
      </div>
    </aside>
  );
}
