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

describe("ChairNotesPanel — per-row styling untouched by B1", () => {
  it("still renders each note in its own bordered row inside the container", async () => {
    // B1 adds the outer box ONLY. The rows keep their own boxes for now (they
    // will look nested until B2 flattens them) — this pins that B1 did not
    // quietly start that work, so B2's diff stays honest.
    renderTicket(STEPS.slice(0, 3).join("\n"));
    await waitForDetail();

    const box = containers()[0];
    const rows = STEPS.slice(0, 3).map((s) =>
      within(box).getByText(s).closest("div.rounded-md")
    );

    expect(rows).toHaveLength(3);
    for (const row of rows) {
      expect(row).not.toBeNull();
      expect(row?.className).toContain("border-l-[3px]");
      expect(box).toContainElement(row as HTMLElement);
    }
    // Distinct rows, not one merged block.
    expect(new Set(rows).size).toBe(3);
  });
});
