/**
 * EmailDetail keyboard shortcuts — Ctrl+Alt+S (approve) + the E/R/C typing guard.
 *
 * Renders the REAL ticket route (TicketPage → EmailWorkspace → EmailDetail), the
 * same harness as approve-send-chain.test.tsx, so the window-level keydown
 * handler is mounted exactly as in production. The keystrokes are dispatched as
 * native KeyboardEvents on the intended target; `getModifierState` is mocked per
 * event to stand in for AltGr.
 *
 * ⚠️ jsdom-mocked — these tests exercise the handler's BRANCHING on
 * getModifierState("AltGraph"), NOT real keyboard/OS behaviour. They do NOT
 * prove that a physical AltGr key on a non-US layout reports AltGraph the way
 * this code assumes; that still needs live cross-platform validation.
 */
import { describe, it, expect, beforeAll, beforeEach, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import TicketPage from "@/app/tickets/[ticketId]/page";
import type { Email } from "@/types";

const state = vi.hoisted(() => ({
  current: null as unknown,
  approve: vi.fn(),
  send: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: state.push }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, approveEmail: state.approve, sendEmail: state.send };
});

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

function makeEmail(overrides: Partial<Email> = {}): Email {
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
    draft: { draft_text: DRAFT_TEXT } as never,
    created_at: "2026-07-20T09:00:00Z",
    updated_at: "2026-07-20T09:00:00Z",
    ...overrides,
  } as Email;
}

function renderTicket() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <TicketPage
        params={{ ticketId: String((state.current as Email).zendesk_ticket_id) }}
      />
    </QueryClientProvider>
  );
}

/** Wait for EmailDetail to be mounted (its submit control is present). */
async function waitForDetail() {
  await screen.findByRole("button", { name: "Submit as Solved" });
}

/** The editable draft textarea. */
const draftTextarea = () =>
  screen.getByDisplayValue(DRAFT_TEXT) as HTMLTextAreaElement;

/**
 * Dispatch a native keydown on `target`, mocking getModifierState so
 * "AltGraph" reports `altGraph`. Returns the event so callers can read
 * `defaultPrevented`.
 */
function fireKey(
  target: EventTarget,
  init: {
    key?: string;
    code?: string;
    ctrl?: boolean;
    alt?: boolean;
    altGraph?: boolean;
  }
): KeyboardEvent {
  const event = new KeyboardEvent("keydown", {
    key: init.key ?? "",
    code: init.code ?? "",
    ctrlKey: !!init.ctrl,
    altKey: !!init.alt,
    bubbles: true,
    cancelable: true,
  });
  Object.defineProperty(event, "getModifierState", {
    value: (k: string) => (k === "AltGraph" ? !!init.altGraph : false),
  });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}

const ctrlAltS = (
  target: EventTarget,
  altGraph: boolean
): KeyboardEvent =>
  fireKey(target, { code: "KeyS", key: "s", ctrl: true, alt: true, altGraph });

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
  state.current = makeEmail();
  state.approve.mockReset();
  state.approve.mockResolvedValue(makeEmail({ status: "approved" }));
});

// ⚠️ jsdom-mocked — proves the handler branches on getModifierState("AltGraph"),
// NOT that real AltGr hardware reports it this way.
describe("EmailDetail — Ctrl+Alt+S vs AltGr+S (jsdom-mocked, not real hardware)", () => {
  it("approves while typing in the draft when AltGraph is false", async () => {
    renderTicket();
    await waitForDetail();

    const event = ctrlAltS(draftTextarea(), /* altGraph */ false);

    expect(event.defaultPrevented).toBe(true);
    await waitFor(() => expect(state.approve).toHaveBeenCalledTimes(1));
  });

  it("does nothing while typing when AltGraph is true (real AltGr+S)", async () => {
    renderTicket();
    await waitForDetail();

    const event = ctrlAltS(draftTextarea(), /* altGraph */ true);

    // No approve, and the keystroke is NOT swallowed — it types through.
    expect(event.defaultPrevented).toBe(false);
    await new Promise((r) => setTimeout(r, 0));
    expect(state.approve).not.toHaveBeenCalled();
  });
});

describe("EmailDetail — E/R/C typing guard unchanged", () => {
  it("E focuses the draft when pressed outside a field, no-ops inside", async () => {
    renderTicket();
    await waitForDetail();

    // Outside a field → the handler acts (preventDefault).
    expect(fireKey(document.body, { key: "e", code: "KeyE" }).defaultPrevented).toBe(
      true
    );
    // Focus in the textarea → typing guard blocks it (no preventDefault).
    expect(
      fireKey(draftTextarea(), { key: "e", code: "KeyE" }).defaultPrevented
    ).toBe(false);
  });

  it("R opens reroute when pressed outside a field, no-ops inside", async () => {
    renderTicket();
    await waitForDetail();

    expect(fireKey(document.body, { key: "r", code: "KeyR" }).defaultPrevented).toBe(
      true
    );
    expect(
      fireKey(draftTextarea(), { key: "r", code: "KeyR" }).defaultPrevented
    ).toBe(false);
  });

  it("C opens reassign when pressed outside a field, no-ops inside", async () => {
    renderTicket();
    await waitForDetail();

    expect(fireKey(document.body, { key: "c", code: "KeyC" }).defaultPrevented).toBe(
      true
    );
    expect(
      fireKey(draftTextarea(), { key: "c", code: "KeyC" }).defaultPrevented
    ).toBe(false);
  });
});
