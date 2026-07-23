"use client";

import { useState, type ReactNode } from "react";
import { Menu, Mail } from "lucide-react";

import { Sidebar } from "./Sidebar";

/**
 * App chrome: fixed sidebar (desktop) / slide-in drawer (mobile) + a mobile top
 * bar with a hamburger toggle, plus the scrollable main content region.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <>
      {/* Mobile top bar (hidden ≥md) */}
      <header
        className="fixed inset-x-0 top-0 z-30 flex h-14 items-center gap-3 px-4 md:hidden"
        style={{
          backgroundColor: "var(--surface)",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <button
          type="button"
          onClick={() => setMobileOpen(true)}
          aria-label="Open navigation"
          className="rounded-md p-1.5 transition-colors hover:bg-[var(--surface-raised)]"
          style={{ color: "var(--text-secondary)" }}
        >
          <Menu className="h-5 w-5" />
        </button>
        <div className="flex items-center gap-2">
          <div
            className="flex h-6 w-6 items-center justify-center rounded-md"
            style={{ backgroundColor: "var(--accent)", color: "var(--text-primary)" }}
          >
            <Mail className="h-3.5 w-3.5" />
          </div>
          <span
            className="text-sm font-semibold tracking-tight"
            style={{ color: "var(--text-primary)" }}
          >
            ConfMail
          </span>
        </div>
      </header>

      {/* Backdrop (mobile, when drawer open) */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-30 md:hidden"
          style={{ backgroundColor: "rgba(0, 0, 0, 0.5)" }}
          onClick={() => setMobileOpen(false)}
          aria-hidden
        />
      )}

      <Sidebar open={mobileOpen} onNavigate={() => setMobileOpen(false)} />

      {/* Main content — offset past the sidebar on desktop, past the top bar on mobile */}
      <main className="min-h-screen pt-14 md:ml-[var(--rail-width)] md:pt-0">
        {children}
      </main>
    </>
  );
}
