"use client";

/**
 * TEMPORARY visual probe for components/ui/calendar.tsx. NOT part of the app.
 *
 * The calendar was built and verified structurally (contrast math, SSR output,
 * compiled CSS) but never looked at. This route renders it in isolation with
 * every state the styling work covered visible at once, in both themes, so a
 * human can confirm it before the trigger/panel/state wiring goes on top.
 *
 * DELETE THIS DIRECTORY once the calendar has been signed off.
 *
 * Two ways to check the theme, deliberately:
 *  1. SIDE-BY-SIDE — the two panels below re-declare the theme tokens on a
 *     wrapper element, so both palettes are visible simultaneously. This is
 *     necessary because globals.css scopes the light palette to
 *     `:root[data-theme="light"]`, which matches ONLY <html> — a nested div
 *     cannot inherit it. Values are copied from globals.css and could drift
 *     from it; acceptable for a throwaway route, and the toggle below is the
 *     authoritative check.
 *  2. TOGGLE — flips the real `data-theme` on <html> exactly as the app's
 *     ThemeToggle does, so what you see is the genuine mechanism, not a copy.
 */

import * as React from "react";

import { Calendar } from "@/components/ui/calendar";
import { DateRangeFilter } from "@/components/email/DateRangeFilter";
import { useTheme } from "@/hooks/useTheme";

/** Copied from globals.css. Only the tokens the calendar actually consumes. */
const DARK_TOKENS: React.CSSProperties = {
  "--background": "#0f1117",
  "--surface": "#1a1d27",
  "--surface-raised": "#21263a",
  "--border": "#2a2f45",
  "--text-primary": "#f0f2f8",
  "--text-secondary": "#8b91a8",
  "--text-muted": "#555c78",
  "--accent": "#6366f1",
  "--accent-hover": "#4f46e5",
  "--accent-subtle": "#1e1f3a",
} as React.CSSProperties;

const LIGHT_TOKENS: React.CSSProperties = {
  "--background": "#f7f8fb",
  "--surface": "#ffffff",
  "--surface-raised": "#eef0f6",
  "--border": "#e2e5ee",
  "--text-primary": "#14161f",
  "--text-secondary": "#5b6178",
  "--text-muted": "#9aa0b4",
  "--accent": "#6366f1",
  "--accent-hover": "#4f46e5",
  "--accent-subtle": "#eef0fe",
} as React.CSSProperties;

/**
 * Dates are anchored to the CURRENT month on purpose: `today` is one of the
 * states under review, and a hardcoded January would put it off-screen. Range
 * 12th–16th, 20th disabled, outside-month days shown.
 */
function useProbeDates() {
  return React.useMemo(() => {
    const now = new Date();
    const y = now.getFullYear();
    const m = now.getMonth();
    return {
      month: new Date(y, m, 1),
      range: { from: new Date(y, m, 12), to: new Date(y, m, 16) },
      disabled: [new Date(y, m, 20)],
    };
  }, []);
}

const STATES = [
  ["default", "plain day — --text-primary, transparent"],
  ["hover", "hover any unselected day — --surface-raised fill"],
  ["today", "accent RING, normal text (never accent-colored text)"],
  ["selected / range ends", "12th & 16th — --accent-hover fill, white text, 6.29:1"],
  ["range middle", "13th–15th — 25% accent mix band, --text-primary"],
  ["outside month", "greyed leading/trailing days — --text-muted"],
  ["disabled", "the 20th — --text-muted at 50%"],
  ["focus ring", "tab into the grid, then arrow-key — 2px accent ring"],
];

function ThemePanel({
  label,
  tokens,
  note,
}: {
  label: string;
  tokens: React.CSSProperties;
  note: string;
}) {
  const { month, range, disabled } = useProbeDates();
  return (
    <div
      style={{ ...tokens, backgroundColor: "var(--background)" }}
      className="flex-1 rounded-xl p-5"
    >
      <p
        className="mb-1 text-xs font-medium uppercase tracking-wide"
        style={{ color: "var(--text-muted)" }}
      >
        {label}
      </p>
      <p className="mb-4 text-xs" style={{ color: "var(--text-secondary)" }}>
        {note}
      </p>
      {/* Mimics PopoverContent, the calendar's real container: it paints
          --surface-raised, which is why the calendar itself is transparent. */}
      <div
        className="inline-block rounded-lg border p-3 shadow-lg"
        style={{
          backgroundColor: "var(--surface-raised)",
          borderColor: "var(--border)",
        }}
      >
        <Calendar
          mode="range"
          selected={range}
          defaultMonth={month}
          disabled={disabled}
          showOutsideDays
        />
      </div>
    </div>
  );
}

export default function ProbeCalendarPage() {
  const { theme, toggleTheme } = useTheme();
  const { month, range, disabled } = useProbeDates();
  // Local stand-in for the EmailWorkspace filter state this control will own.
  const [probeAfter, setProbeAfter] = React.useState<string | null>(null);
  const [probeBefore, setProbeBefore] = React.useState<string | null>(null);

  return (
    <main
      className="min-h-screen p-8"
      style={{ backgroundColor: "var(--background)", color: "var(--text-primary)" }}
    >
      <div className="mx-auto max-w-5xl space-y-8">
        <header className="space-y-2">
          <h1 className="text-xl font-semibold">Calendar visual probe</h1>
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            Temporary route for reviewing{" "}
            <code>components/ui/calendar.tsx</code> before it is wired into the
            queue. Delete <code>src/app/probe-cal/</code> once signed off.
          </p>
        </header>

        <section className="space-y-3">
          <h2 className="text-sm font-medium">
            1 · Live theme (the real mechanism)
          </h2>
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            This calendar follows the actual <code>data-theme</code> on{" "}
            <code>&lt;html&gt;</code>, same as the rest of the app. Current
            theme: <strong>{theme}</strong>.
          </p>
          <button
            type="button"
            onClick={toggleTheme}
            className="rounded-md border px-3 py-1.5 text-sm transition-colors"
            style={{
              backgroundColor: "var(--surface-raised)",
              borderColor: "var(--border)",
              color: "var(--text-primary)",
            }}
          >
            Switch to {theme === "dark" ? "light" : "dark"}
          </button>
          <div
            className="inline-block rounded-lg border p-3 shadow-lg"
            style={{
              backgroundColor: "var(--surface-raised)",
              borderColor: "var(--border)",
            }}
          >
            <Calendar
              mode="range"
              selected={range}
              defaultMonth={month}
              disabled={disabled}
              showOutsideDays
            />
          </div>
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-medium">2 · Both palettes side by side</h2>
          <div className="flex flex-col gap-4 md:flex-row">
            <ThemePanel
              label="Dark (default)"
              tokens={DARK_TOKENS}
              note="The palette every existing user sees unless they opt into light."
            />
            <ThemePanel
              label="Light (opt-in)"
              tokens={LIGHT_TOKENS}
              note="Where the CHAIR_PALETTE regression happened — check accent-on-white."
            />
          </div>
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-medium">
            3 · DateRangeFilter (the actual queue control)
          </h2>
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            Rendered at the real sidebar width (256px, matching the expanded
            filter column) inside a container painted <code>--surface</code>,
            since that is what it will sit on. Not wired to anything — the state
            below is local to this probe, and the emitted values are echoed live
            so you can confirm the wire format.
          </p>
          <div className="flex flex-col gap-6 md:flex-row">
            <div
              className="w-64 rounded-lg border p-3"
              style={{
                backgroundColor: "var(--surface)",
                borderColor: "var(--border)",
              }}
            >
              <DateRangeFilter
                receivedAfter={probeAfter}
                receivedBefore={probeBefore}
                onChange={(a, b) => {
                  setProbeAfter(a);
                  setProbeBefore(b);
                }}
              />
            </div>
            <div className="space-y-2 text-sm">
              <p style={{ color: "var(--text-secondary)" }}>
                Emitted params (must be bare <code>YYYY-MM-DD</code>, no time
                component):
              </p>
              <pre
                className="rounded-lg border p-3 text-xs"
                style={{
                  backgroundColor: "var(--surface-raised)",
                  borderColor: "var(--border)",
                  color: "var(--text-primary)",
                }}
              >
                {JSON.stringify(
                  { received_after: probeAfter, received_before: probeBefore },
                  null,
                  2
                )}
              </pre>
              <p style={{ color: "var(--text-muted)" }}>
                Check: presets close the popover · a custom range commits after
                the first click (open-ended) then closes on the second · Clear
                only appears when a range is active · trigger shows the accent
                border when active.
              </p>
            </div>
          </div>
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-medium">4 · What to look at</h2>
          <ul className="space-y-1.5 text-sm">
            {STATES.map(([state, what]) => (
              <li key={state} className="flex gap-2">
                <span className="min-w-[10rem] font-medium">{state}</span>
                <span style={{ color: "var(--text-secondary)" }}>{what}</span>
              </li>
            ))}
          </ul>
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            The two judgement calls worth a second opinion: whether the range
            band (a 25% accent mix, replacing an
            <code> --accent-subtle </code> that measured 1.07:1 and was
            effectively invisible on this surface) reads clearly enough, and
            whether the <code>today</code> ring is too subtle next to a filled
            selection.
          </p>
        </section>
      </div>
    </main>
  );
}
