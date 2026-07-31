/**
 * Chair Suggestions panel — ONE outer container around the whole note set
 * (Piece B1).
 *
 * Renders the REAL ticket route (TicketPage → EmailWorkspace → EmailDetail),
 * the same harness as EmailDetail.test.tsx, because ChairNotesPanel and
 * ChairNoteRow are module-private to EmailDetail.tsx and cannot be imported
 * directly. That also means these tests exercise the real parseChairNotes
 * splitting, not a hand-built note array.
 *
 * SCOPE LIMIT — jsdom performs NO layout, so nothing here proves the box looks
 * right. These pin STRUCTURE: that exactly one container exists regardless of
 * note count, and that every note still renders inside it. Visual confirmation
 * (and the nested-box appearance this piece knowingly leaves behind for B2)
 * needs a human; there is no browser driver on this host.
 */
import { describe, it, expect, beforeAll, beforeEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import TicketPage from "@/app/tickets/[ticketId]/page";
import type { Email } from "@/types";

const state = vi.hoisted(() => ({
  current: null as unknown,
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: state.push }),
}));

vi.mock("@/hooks/useEmailByTicket", () => ({
  useEmailByTicket: () => ({
    email: state.current,
    auditTrail: [],
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
}));
vi.mock("@/hooks/useEmailQueue", () => ({
  useEmailQueue: () => ({
    emails: [],
    total: 0,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
vi.mock("@/hooks/useQueueFacets", () => ({
  useQueueFacets: () => ({
    byZendeskStatus: {},
    bySource: {},
    sources: [],
    isLoading: false,
    isError: false,
  }),
}));
vi.mock("@/hooks/useChairs", () => ({
  useChairs: () => ({ chairs: [], byId: new Map(), isLoading: false, isError: false }),
}));
vi.mock("@/hooks/useAppConfig", () => ({
  useAppConfig: () => ({ allowAutoSend: false }),
}));
vi.mock("@/hooks/useEmailQueueStream", () => ({
  useEmailQueueStream: () => ({ status: "live" }),
}));
vi.mock("@/hooks/useEmailThread", () => ({
  useEmailThread: () => ({ messages: [], isLoading: false, isError: false }),
}));

const DRAFT_TEXT = "Dear Author, the deadline is in the CFP.";

/** The drafter contract: one imperative step per line, newline-delimited. */
const STEPS = [
  "Check whether the author already has an approved extension.",
  "Confirm the current submission deadline in the CFP.",
  "Ask the program chair if a second extension is allowed.",
  "Decide whether to grant or decline.",
  "Reply with the agreed date.",
];

function makeEmail(notesForChair: string, overrides: Partial<Email> = {}): Email {
  return {
    id: 1,
    sender: "author@university.edu",
    sender_name: "Author",
    subject: "Deadline question",
    body: "When is the deadline?",
    status: "DRAFT_GENERATED",
    received_at: "2026-07-20T09:00:00Z",
    assigned_chair_id: null,
    source: "zendesk",
    zendesk_ticket_id: 21567,
    zendesk_status: "open",
    classification: { intent: "deadline_extension", confidence: 0.9 } as never,
    routing: { lane: "human_review", rationale: "needs review" } as never,
    draft: { draft_text: DRAFT_TEXT, notes_for_chair: notesForChair } as never,
    created_at: "2026-07-20T09:00:00Z",
    updated_at: "2026-07-20T09:00:00Z",
    ...overrides,
  } as Email;
}

function renderTicket(notesForChair: string) {
  state.current = makeEmail(notesForChair);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <TicketPage params={{ ticketId: "21567" }} />
    </QueryClientProvider>
  );
}

/** Wait for EmailDetail to be mounted (its submit control is present). */
async function waitForDetail() {
  await screen.findByRole("button", { name: "Submit as Solved" });
}

/** Every chair-notes container currently in the DOM (expected: exactly one). */
const containers = () =>
  screen.queryAllByRole("group", { name: "Chair suggestions" });

beforeAll(() => {
  window.HTMLElement.prototype.hasPointerCapture = vi.fn();
  window.HTMLElement.prototype.releasePointerCapture = vi.fn();
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

beforeEach(() => {
  window.localStorage?.clear?.();
});

describe("ChairNotesPanel — exactly one outer container, whatever the note count", () => {
  // The bug this guards: the container is rendered inside the .map, so it
  // appears once PER NOTE. With a single note that mistake is invisible —
  // hence the 3- and 5-note cases.
  it.each([
    ["1 note", 1],
    ["3 notes", 3],
    ["5 notes", 5],
  ])("renders one container for %s", async (_label, count) => {
    renderTicket(STEPS.slice(0, count).join("\n"));
    await waitForDetail();

    expect(containers()).toHaveLength(1);
  });

  it("renders no container at all when there are no notes", async () => {
    // Empty notes → the whole section is omitted upstream, so the container
    // must not appear as an empty box. Pins that the container did not escape
    // the `notes.length === 0` early return.
    renderTicket("");
    await waitForDetail();

    expect(containers()).toHaveLength(0);
    expect(screen.queryByText("Chair Suggestions")).toBeNull();
  });
});

describe("ChairNotesPanel — no data loss: every note renders inside the container", () => {
  it("keeps all 5 steps, in source order, inside the single container", async () => {
    renderTicket(STEPS.join("\n"));
    await waitForDetail();

    const box = containers()[0];
    for (const step of STEPS) {
      expect(within(box).getByText(step)).toBeInTheDocument();
    }

    // Order is load-bearing — the drafter emits these as a sequence to work
    // through, so a container that reversed or re-sorted them would be wrong.
    const rendered = STEPS.map((s) => within(box).getByText(s));
    const positions = rendered.map((el) => box.textContent?.indexOf(el.textContent ?? ""));
    expect(positions).toEqual([...positions].sort((a, b) => (a ?? 0) - (b ?? 0)));
  });

  it("keeps the urgent (leak-check) note and its label inside the container", async () => {
    // The urgent branch renders extra markup; this proves it still lands inside
    // the new container rather than being hoisted out of it.
    renderTicket(
      [STEPS[0], "WARNING (automated check): draft may leak chair-facing text."].join("\n")
    );
    await waitForDetail();

    const box = containers()[0];
    expect(within(box).getByText("Automated leak check")).toBeInTheDocument();
    expect(
      within(box).getByText("draft may leak chair-facing text.")
    ).toBeInTheDocument();
    expect(within(box).getByText(STEPS[0])).toBeInTheDocument();
  });

  it("leaves the 'Internal' disclaimer OUTSIDE the container", async () => {
    // Deliberate: it is a caption about the section, not one of the steps.
    // Pinned so a future edit has to make that choice consciously.
    renderTicket(STEPS.slice(0, 2).join("\n"));
    await waitForDetail();

    const disclaimer = screen.getByText("Internal — not sent to the requester.");
    expect(disclaimer).toBeInTheDocument();
    expect(containers()[0]).not.toContainElement(disclaimer);
  });
});

/**
 * Class fragments that make an element read as its own BOX. Matched on the raw
 * `class` attribute, not `className`: SVG elements expose className as an
 * SVGAnimatedString, which would silently stringify to "[object ...]" and never
 * match — the icon would then be exempt from the sweep without anyone noticing.
 */
const BOXY_CLASS_RE = /(^|\s)(rounded|border|bg-)/;

/**
 * Inline-style equivalent: any background, or any border declaration OTHER than
 * `border-left` (which Piece D legitimately uses for the urgent accent rule).
 * Matched on cssText because jsdom cannot expand a shorthand containing
 * `var()` into longhands — see the sweep below.
 */
const BOXY_INLINE_RE = /(^|;)\s*(background|border(?!-left))/;

/** Direct children of the container — one per note. */
const rowsIn = (box: HTMLElement) => Array.from(box.children) as HTMLElement[];

describe("ChairNotesPanel — rows are plain, the container is the only box (B2)", () => {
  it("renders one plain row per note, with no box classes of its own", async () => {
    // B2 strips the per-row `rounded-md border-l-[3px]` + tint. Before B2 these
    // rows each carried their own box, which read as boxes nested in a box once
    // B1 landed.
    renderTicket(STEPS.slice(0, 3).join("\n"));
    await waitForDetail();

    const box = containers()[0];
    const rows = rowsIn(box);

    expect(rows).toHaveLength(3);
    rows.forEach((row, i) => {
      // Still a distinct row holding the right note — flattening the styling
      // must not have merged or reordered them.
      expect(row).toHaveTextContent(STEPS[i]);
      expect(row.getAttribute("class") ?? "").not.toMatch(BOXY_CLASS_RE);
    });
  });

  it("gives rows no background, and no border on the box-forming edges", async () => {
    // The tint and accent were inline styles, not classes, so the class sweep
    // above cannot see them — this is the half that catches a partial revert.
    //
    // Asserted against cssText rather than the longhand getters, which are NOT
    // reliable here: these styles use `var(--token)`, and jsdom cannot expand a
    // shorthand containing var() into longhands, so `style.borderLeftColor`
    // reads "" even though `border-left` is set. A longhand sweep would
    // therefore pass vacuously against a `border: 1px solid var(--x)` full box
    // — exactly the regression it is meant to catch.
    //
    // SCOPE: background and every border edge EXCEPT left are forbidden — that
    // is the anti-nested-box invariant. The left edge is deliberately exempt:
    // Piece D puts a 2px rule there, asserted in its own block below.
    renderTicket(STEPS.slice(0, 3).join("\n"));
    await waitForDetail();

    for (const row of rowsIn(containers()[0])) {
      expect(row.style.cssText).not.toMatch(BOXY_INLINE_RE);
    }
  });

  it("leaves the container as the ONLY box-level element in the panel", async () => {
    // Sweeps every descendant, so a box re-appearing on an inner wrapper (not
    // just the row root) is caught too. Includes an urgent note because that
    // branch renders extra markup.
    renderTicket(
      [STEPS[0], STEPS[1], "WARNING (automated check): possible leak."].join("\n")
    );
    await waitForDetail();

    const box = containers()[0];
    // The container itself is legitimately boxy — everything inside must not be.
    expect(box.getAttribute("class") ?? "").toMatch(BOXY_CLASS_RE);

    const boxy = Array.from(box.querySelectorAll("*")).filter((el) =>
      BOXY_CLASS_RE.test(el.getAttribute("class") ?? "")
    );
    expect(boxy).toEqual([]);
  });

  it("keeps pre-wrap on the note text", async () => {
    // Unchanged by B2, but it lives on an element whose ancestors were just
    // restyled — pin it so a future flatten cannot take it along.
    renderTicket(STEPS[0]);
    await waitForDetail();

    expect(within(containers()[0]).getByText(STEPS[0])).toHaveStyle({
      whiteSpace: "pre-wrap",
    });
  });

  it("still renders the severity icon (untouched by B2, revisited in D)", async () => {
    // The icon is the remaining non-color severity signal now that the tint is
    // gone, so its disappearance would be a real regression, not a cleanup.
    renderTicket(STEPS[0]);
    await waitForDetail();

    expect(containers()[0].querySelector("svg")).not.toBeNull();
  });
});

/**
 * The bullet glyph. Matched on content rather than a test id so the assertion
 * describes what a chair actually sees; `aria-hidden` does not hide an element
 * from a DOM query, only from the accessibility tree.
 */
const bulletsIn = (root: HTMLElement) =>
  Array.from(root.querySelectorAll("span")).filter(
    (el) => el.textContent?.trim() === "•"
  );

describe("ChairNoteRow — bullet marker per step (C1)", () => {
  it("renders exactly one bullet per row, on every severity", async () => {
    // Mixed advisory + urgent: the bullet marks "discrete step", which is true
    // of every note, so it must NOT be conditional on severity the way the
    // icon glyph and the leak-check label are.
    renderTicket(
      [STEPS[0], "WARNING (automated check): possible leak.", STEPS[1]].join("\n")
    );
    await waitForDetail();

    const box = containers()[0];
    const rows = rowsIn(box);

    expect(rows).toHaveLength(3);
    expect(bulletsIn(box)).toHaveLength(3);
    for (const row of rows) {
      expect(bulletsIn(row)).toHaveLength(1);
    }
  });

  it("scales one-per-row with the note count, not one per panel", async () => {
    // Guards the mirror image of B1's bug: a marker hoisted out of the row and
    // rendered once for the whole set would still "show a bullet".
    renderTicket(STEPS.join("\n"));
    await waitForDetail();

    expect(bulletsIn(containers()[0])).toHaveLength(STEPS.length);
  });

  it("sits ALONGSIDE the severity icon, not in place of it", async () => {
    // The two carry different information (sequence vs severity). A change that
    // swapped one for the other would look fine and lose a signal.
    renderTicket(STEPS[0]);
    await waitForDetail();

    const row = rowsIn(containers()[0])[0];

    expect(bulletsIn(row)).toHaveLength(1);
    expect(row.querySelector("svg")).not.toBeNull();
    // Bullet leads the row, so the left edge reads as a list.
    expect(row.firstElementChild).toBe(bulletsIn(row)[0]);
  });

  it("keeps the bullet out of the accessibility tree", async () => {
    // Decorative: these rows are divs, not <li>, so an exposed glyph would be
    // announced as content ("bullet Check whether…") with no list semantics.
    renderTicket(STEPS[0]);
    await waitForDetail();

    expect(bulletsIn(containers()[0])[0]).toHaveAttribute("aria-hidden", "true");
  });

  it("does not disturb the note text or its pre-wrap", async () => {
    // The bullet is a SIBLING of the text, not a prefix concatenated into it —
    // otherwise C2's prefix-stripping would later have to parse around it.
    renderTicket(STEPS[0]);
    await waitForDetail();

    const text = within(containers()[0]).getByText(STEPS[0]);
    expect(text).toHaveStyle({ whiteSpace: "pre-wrap" });
    expect(text.textContent).toBe(STEPS[0]);
  });
});

/**
 * ⚠️ These tests pin a PRECAUTIONARY guard, not a reproduction of a real bug.
 * No prefixed multi-step note has ever been observed; two live probes against
 * the current drafter model produced no evidence either way. If one of these
 * strip-cases ever fires on real traffic, that is the first observation of the
 * behaviour — see the comment on `stripLeadingListMarker` in EmailDetail.tsx.
 *
 * The KEEP cases are the load-bearing half: they are what stops the guard from
 * silently eating legitimate note text.
 */
describe("ChairNoteRow — strips a stray leading list marker (C2)", () => {
  /** Rendered text of the single note in a one-note panel. */
  async function renderedNote(raw: string): Promise<string> {
    renderTicket(raw);
    await waitForDetail();
    const row = rowsIn(containers()[0])[0];
    // The <p> is the row's last element; the bullet + icon are its siblings.
    return row.querySelector("p")?.textContent ?? "";
  }

  // Expected output written out in full, never derived from the input — a
  // computed expectation can agree with a broken implementation.
  it.each([
    [
      "numeric dot",
      "1. Check whether the author already has an approved extension.",
      "Check whether the author already has an approved extension.",
    ],
    [
      "numeric paren",
      "2) Confirm the current submission deadline in the CFP.",
      "Confirm the current submission deadline in the CFP.",
    ],
    [
      "hyphen",
      "- Ask the program chair if a second extension is allowed.",
      "Ask the program chair if a second extension is allowed.",
    ],
    ["asterisk", "* Decide whether to grant or decline.", "Decide whether to grant or decline."],
    ["bullet glyph", "• Reply with the agreed date.", "Reply with the agreed date."],
    ["en-dash", "– Verify the co-author's affiliation.", "Verify the co-author's affiliation."],
    ["em-dash", "— Escalate to the program chair.", "Escalate to the program chair."],
    ["two-digit", "10. Final step in the workflow.", "Final step in the workflow."],
  ])("strips a leading %s marker", async (_label, raw, expected) => {
    expect(await renderedNote(raw)).toBe(expected);
  });

  it.each([
    // The mandatory \s+ after the marker is what saves these.
    "3-day deadline applies here.",
    "-5 degrees is out of scope.",
    "1.5x the page limit is not permitted.",
    "*emphasis* matters in the reply.",
    // Digits with no marker separator at all.
    "2026 deadline is firm.",
    // 4 digits — beyond \d{1,2}. With \d+ this would lose the year entirely,
    // silently rewriting the note to "Deadline moved to March.".
    "2026. Deadline moved to March.",
    // Degenerate: stripping would leave nothing, so the guard keeps the original
    // rather than rendering an empty row.
    "1)",
    // The ACTUAL string the live probe returned — the only real drafter output
    // we possess. Pinned as an explicit no-op so the guard provably does not
    // touch the one behaviour we have genuinely observed.
    "Determine whether withdrawal and resubmission under the AI Alignment track is permitted for paper #4127.",
  ])("leaves %j untouched", async (raw) => {
    expect(await renderedNote(raw)).toBe(raw);
  });

  it("renders a prefixed note as ONE bullet plus clean text, not doubled", async () => {
    // The end-to-end point of the piece: the visible row must not read
    // "• 1. Check…". Asserts on the row's full text, so a marker surviving
    // anywhere in it fails.
    renderTicket("1. Check whether the author already has an approved extension.");
    await waitForDetail();

    const row = rowsIn(containers()[0])[0];

    expect(bulletsIn(row)).toHaveLength(1);
    expect(row.querySelector("p")?.textContent).toBe(
      "Check whether the author already has an approved extension."
    );
    expect(row.textContent).not.toContain("1.");
  });

  it("leaves an UNPREFIXED note completely unchanged (the common case)", async () => {
    // Regression guard. Every note we have actually seen looks like this, so if
    // the guard ever touches this path it is doing net harm.
    renderTicket(STEPS.join("\n"));
    await waitForDetail();

    const box = containers()[0];
    const rows = rowsIn(box);
    expect(rows).toHaveLength(STEPS.length);
    rows.forEach((row, i) => {
      expect(row.querySelector("p")?.textContent).toBe(STEPS[i]);
    });
  });
});

const URGENT_NOTE = "WARNING (automated check): possible chair-facing leak.";

describe("ChairNoteRow — urgent rows carry a left accent rule (D)", () => {
  it("gives ONLY the urgent row a --danger left rule", async () => {
    // The scanning signal. Urgent already had a distinct glyph, colour and
    // label; what it lacked was anything breaking the panel's uniform left
    // edge, which is what a chair scans down.
    renderTicket([STEPS[0], URGENT_NOTE, STEPS[1]].join("\n"));
    await waitForDetail();

    const [advisoryA, urgent, advisoryB] = rowsIn(containers()[0]);

    // Asserted on the `border-left` SHORTHAND: jsdom keeps it unexpanded
    // because of the var(), so `style.borderLeftColor` reads "".
    expect(urgent.style.borderLeft).toBe("2px solid var(--danger)");
    // Advisory keeps the same border at transparent — alignment only, so the
    // list does not jitter by 2px between severities.
    expect(advisoryA.style.borderLeft).toBe("2px solid transparent");
    expect(advisoryB.style.borderLeft).toBe("2px solid transparent");
  });

  it("uses a rule, not a box: same width and style on every row", async () => {
    // Pins that D restored a MARGIN MARKER, not the per-row box B2 removed.
    // Only the colour may differ between severities.
    renderTicket([STEPS[0], URGENT_NOTE].join("\n"));
    await waitForDetail();

    for (const row of rowsIn(containers()[0])) {
      expect(row.style.borderLeft).toMatch(/^2px solid /);
      expect(row.style.cssText).not.toMatch(BOXY_INLINE_RE);
      expect(row.getAttribute("class") ?? "").not.toMatch(/rounded/);
    }
  });

  it("keeps advisory rows visually identical to pre-D", async () => {
    // A transparent border paints nothing, so an advisory-only panel must look
    // exactly as it did before this piece.
    renderTicket(STEPS.slice(0, 3).join("\n"));
    await waitForDetail();

    for (const row of rowsIn(containers()[0])) {
      expect(row.style.borderLeft).toBe("2px solid transparent");
      expect(row.style.cssText).not.toMatch(BOXY_INLINE_RE);
    }
  });

  it("still distinguishes urgent by glyph and label, not colour alone", async () => {
    // The rule is an ADDITION. If it ever became the only signal, a colourblind
    // chair would lose the escalation entirely.
    renderTicket([STEPS[0], URGENT_NOTE].join("\n"));
    await waitForDetail();

    const box = containers()[0];
    const [advisory, urgent] = rowsIn(box);

    expect(within(urgent).getByText("Automated leak check")).toBeInTheDocument();
    expect(within(box).getByText("possible chair-facing leak.")).toBeInTheDocument();
    expect(within(advisory).queryByText("Automated leak check")).toBeNull();
    // Both severities still render an icon.
    expect(urgent.querySelector("svg")).not.toBeNull();
    expect(advisory.querySelector("svg")).not.toBeNull();
  });
});

describe("ChairNoteRow — bullet sits tight to its icon (D)", () => {
  it("pulls the bullet toward the icon without touching icon→text spacing", async () => {
    // Uniform gap-2.5 made the bullet read as a third free-floating element.
    // -mr-1 closes bullet→icon to ~6px; the row keeps gap-2.5 for icon→text.
    renderTicket(STEPS[0]);
    await waitForDetail();

    const row = rowsIn(containers()[0])[0];
    const bullet = bulletsIn(row)[0];

    expect(bullet.getAttribute("class") ?? "").toContain("-mr-1");
    expect(row.getAttribute("class") ?? "").toContain("gap-2.5");
  });
});
