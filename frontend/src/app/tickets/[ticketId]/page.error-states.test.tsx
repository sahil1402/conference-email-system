/**
 * Not-found / error state rendering for /tickets/[ticketId] (Piece C3).
 *
 * These drive the page's error branches deterministically by mocking
 * useEmailByTicket to return the hook state a given HTTP outcome produces
 * (404 / 422 / 5xx / network). That keeps the test on the PAGE's job — mapping
 * an error status to the right UI — without react-query surfacing the
 * deliberately-triggered query rejection as an unhandled rejection. The real
 * hook↔fetch integration (incl. loading + success) is covered in page.test.tsx.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { ApiError } from "@/types";
import TicketPage from "./page";

const hookState = vi.hoisted(() => ({
  value: {
    email: null as unknown,
    auditTrail: [] as unknown[],
    isLoading: false,
    isError: false,
    error: null as ApiError | null,
    refetch: vi.fn(),
  },
}));

// TicketPage calls useRouter (navigation on row click / advance).
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

// Drive the page's error branches directly via the hook's return value.
vi.mock("@/hooks/useEmailByTicket", () => ({
  useEmailByTicket: () => hookState.value,
}));

// The shared workspace's ambient hooks → static values (chrome renders; the
// detail slot is what these tests assert on).
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
vi.mock("@/hooks/useEmailQueueStream", () => ({
  useEmailQueueStream: () => ({ status: "live" }),
}));
vi.mock("@/hooks/useChairs", () => ({
  useChairs: () => ({ chairs: [], byId: new Map(), isLoading: false, isError: false }),
}));
vi.mock("@/hooks/useAppConfig", () => ({
  useAppConfig: () => ({ allowAutoSend: false }),
}));

function setError(status: number, detail = "boom") {
  hookState.value = {
    email: null,
    auditTrail: [],
    isLoading: false,
    isError: true,
    error: { detail, status },
    refetch: vi.fn(),
  };
}

function renderPage(ticketId: string) {
  // The shared workspace's action hooks call useQueryClient (no action fires in
  // these tests), so a provider must be present even though the detail data is
  // driven by the mocked useEmailByTicket.
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <TicketPage params={{ ticketId }} />
    </QueryClientProvider>
  );
}

describe("TicketPage error/not-found states (C3)", () => {
  beforeEach(() => {
    hookState.value = {
      email: null,
      auditTrail: [],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    };
  });

  it("404 → not-found state naming the ticket id, with a link back to /queue", () => {
    setError(404, "No email found for ticket id 999");
    renderPage("999");

    expect(screen.getByText("Ticket not found")).toBeInTheDocument();
    expect(
      screen.getByText(/We couldn't find a ticket with ID 999/)
    ).toBeInTheDocument();
    const backLink = screen.getByRole("link", { name: /back to queue/i });
    expect(backLink).toHaveAttribute("href", "/queue");
    // Raw backend detail is not dumped to the user.
    expect(
      screen.queryByText(/No email found for ticket id/)
    ).not.toBeInTheDocument();
  });

  it("422 (malformed id) → same handled not-found state, no crash", () => {
    setError(422, "unprocessable");
    renderPage("abc");

    expect(screen.getByText("Ticket not found")).toBeInTheDocument();
    expect(
      screen.getByText(/We couldn't find a ticket with ID abc/)
    ).toBeInTheDocument();
  });

  it("500 → generic error state, distinct from not-found", () => {
    setError(500, "Internal Server Error");
    renderPage("21567");

    expect(
      screen.getByText(/Something went wrong loading this ticket/)
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("Ticket not found")).not.toBeInTheDocument();
  });

  it("network failure (status 0) → generic error, not not-found", () => {
    setError(0, "Network Error");
    renderPage("21567");

    expect(
      screen.getByText(/Something went wrong loading this ticket/)
    ).toBeInTheDocument();
    expect(screen.queryByText("Ticket not found")).not.toBeInTheDocument();
  });

  it("no failure path renders a raw error object to the DOM", () => {
    for (const status of [404, 422, 500, 0]) {
      setError(status);
      const { unmount } = renderPage(status === 422 ? "abc" : "999");
      expect(screen.queryByText(/\[object Object\]/)).not.toBeInTheDocument();
      unmount();
    }
  });
});
