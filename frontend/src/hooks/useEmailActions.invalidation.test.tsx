/**
 * Query-invalidation on email actions (Piece C2c).
 *
 * Confirms that an action (approve) invalidates the ticket-route's
 * ["emailByTicket", id] query so /tickets/{id} refreshes IMMEDIATELY — not only
 * on its 15s poll — exactly like /queue's ["emailQueue"]. The queue key must
 * stay invalidated too (no regression to /queue's already-working refresh).
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import type { ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { useApproveEmail } from "./useEmailActions";
import { useEmailByTicket } from "./useEmailByTicket";

const state = vi.hoisted(() => ({ approve: vi.fn(), getByTicket: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    approveEmail: state.approve,
    getEmailByTicketId: state.getByTicket,
  };
});

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function wrapperFor(client: QueryClient) {
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  Wrapper.displayName = "TestQueryWrapper";
  return Wrapper;
}

// Drive the ticket detail query + an action from one client, like the page does.
function useTicketPageLike(ticketId: string) {
  return { detail: useEmailByTicket(ticketId), approve: useApproveEmail() };
}

describe("email-action invalidation reaches the ticket route", () => {
  beforeEach(() => {
    state.approve.mockReset();
    state.getByTicket.mockReset();
    state.approve.mockResolvedValue({ id: 7, status: "approved" });
    state.getByTicket.mockResolvedValue({
      email: { id: 7, zendesk_ticket_id: 21567 },
      audit_trail: [],
    });
  });

  it("refetches the emailByTicket query for that ticket after an approve (no poll wait)", async () => {
    const client = makeClient();
    const { result } = renderHook(() => useTicketPageLike("21567"), {
      wrapper: wrapperFor(client),
    });

    // Initial mount fetches the ticket detail exactly once.
    await waitFor(() => expect(state.getByTicket).toHaveBeenCalledTimes(1));

    // Act on the email; on success the shared invalidation runs.
    await result.current.approve.mutateAsync({ id: 7 });

    // The active ["emailByTicket","21567"] query is invalidated → refetches
    // immediately (second call), rather than waiting for the 15s poll.
    await waitFor(() => expect(state.getByTicket).toHaveBeenCalledTimes(2));
  });

  it("still invalidates the queue key too (no regression to /queue)", async () => {
    const client = makeClient();
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useApproveEmail(), {
      wrapper: wrapperFor(client),
    });

    await result.current.mutateAsync({ id: 7 });

    const invalidatedKeys = spy.mock.calls.map((c) => c[0]?.queryKey?.[0]);
    expect(invalidatedKeys).toContain("emailQueue"); // existing behavior intact
    expect(invalidatedKeys).toContain("emailByTicket"); // new: ticket route
  });
});
