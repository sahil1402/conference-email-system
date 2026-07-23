/**
 * Guards the layout tokens and the 56px coupling introduced across the
 * nav-rail / top-bar tracks (N2, BR2–BR4).
 *
 * jsdom never loads globals.css, so the component tests that reference
 * `var(--rail-width)` / `var(--topbar-height)` inside className strings would
 * still pass even if the tokens were deleted or renamed. And BR4 flagged that
 * four independent declarations — `--topbar-height`, the mobile bar's `h-14`,
 * `<main>`'s `pt-14`, and the split-pane's hand-written `3.5rem` — must all stay
 * at 56px. These file-read assertions cover both: token existence and drift.
 *
 * NOTE (C2b): the split-pane height string moved out of app/queue/page.tsx into
 * the shared components/email/EmailWorkspace.tsx (rendered by BOTH /queue and
 * /tickets/[ticketId]) — the rendered value is unchanged, so this guard now
 * reads the workspace, its new owner.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const SRC = join(process.cwd(), "src");
const read = (...p: string[]) => readFileSync(join(SRC, ...p), "utf8");

const GLOBALS = read("app", "globals.css");
const APPSHELL = read("components", "layout", "AppShell.tsx");
const WORKSPACE = read("components", "email", "EmailWorkspace.tsx");

/** px value of a `--token: <n>px;` declaration in globals.css. */
function tokenPx(name: string): number {
  const m = GLOBALS.match(new RegExp(`--${name}:\\s*(\\d+)px`));
  expect(m, `--${name} should be defined in globals.css`).not.toBeNull();
  return Number(m![1]);
}

describe("layout tokens exist in globals.css", () => {
  it("defines --rail-width and --topbar-height", () => {
    expect(tokenPx("rail-width")).toBe(52);
    expect(tokenPx("topbar-height")).toBe(56);
  });
});

describe("the 56px top-bar coupling stays consistent (BR4)", () => {
  const TAILWIND_UNIT_PX = 4; // Tailwind spacing scale: 1 unit = 0.25rem = 4px
  const REM_PX = 16;

  it("keeps mobile and desktop top-bar heights equal to --topbar-height", () => {
    const topbar = tokenPx("topbar-height"); // desktop bar height (56)

    // Mobile bar: h-14 → 14 * 4 = 56px.
    expect(APPSHELL).toMatch(/\bh-14\b/);
    expect(14 * TAILWIND_UNIT_PX).toBe(topbar);

    // <main> mobile top offset: pt-14 (56px), desktop offset: the token itself.
    expect(APPSHELL).toMatch(/\bpt-14\b/);
    expect(APPSHELL).toContain("md:pt-[var(--topbar-height)]");

    // Split-pane subtracts the bar: mobile 3.5rem must equal the token. Lives
    // in the shared EmailWorkspace (C2b) — rendered by /queue and the ticket route.
    const rem = WORKSPACE.match(/100vh-(\d+(?:\.\d+)?)rem/);
    expect(rem, "workspace should subtract a rem value for the mobile bar")
      .not.toBeNull();
    expect(Number(rem![1]) * REM_PX).toBe(topbar); // 3.5 * 16 === 56
    expect(WORKSPACE).toContain("md:h-[calc(100vh-var(--topbar-height))]");
  });
});
