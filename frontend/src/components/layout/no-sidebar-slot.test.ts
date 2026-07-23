/**
 * Regression guard for the filter-column extraction (N3c/N3d).
 *
 * The queue filters used to render through a React portal into a slot inside
 * the Sidebar (SidebarSlot's context + provider + hook, and the empty <div ref>
 * that received the portal). All of it was deleted once the filters moved into
 * their own page-owned column. N3d's grep proved it was gone at the time; this
 * makes that permanent, so the indirection can't quietly reappear.
 */
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = join(process.cwd(), "src");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.(ts|tsx)$/.test(entry) ? [full] : [];
  });
}

describe("SidebarSlot is gone for good", () => {
  it("actually scans the source tree (guards against a vacuous pass)", () => {
    // If the walk ever returned nothing, the assertions below would pass for
    // the wrong reason.
    const files = sourceFiles(SRC);
    expect(files.length).toBeGreaterThan(20);
    expect(files.some((f) => f.endsWith("Sidebar.tsx"))).toBe(true);
  });

  it("has no SidebarSlot.tsx file", () => {
    const files = sourceFiles(SRC);
    expect(files.filter((f) => f.endsWith("SidebarSlot.tsx"))).toEqual([]);
  });

  it("has no references to the slot machinery anywhere in src", () => {
    // This guard file necessarily mentions the names, so exclude it.
    const files = sourceFiles(SRC).filter(
      (f) => !f.endsWith("no-sidebar-slot.test.ts")
    );

    const offenders = files.filter((f) =>
      /SidebarSlot|useSidebarSlot|setSlotEl|slotEl/.test(readFileSync(f, "utf8"))
    );

    expect(offenders).toEqual([]);
  });

  it("does not re-introduce a portal from the queue page", () => {
    const page = readFileSync(join(SRC, "app", "queue", "page.tsx"), "utf8");

    expect(page).not.toMatch(/createPortal/);
    expect(page).not.toMatch(/react-dom/);
  });
});
