/**
 * Fork guard (Piece C2b): /queue and /tickets/[ticketId] must render the SAME
 * shared component (components/email/EmailWorkspace), never diverging copies.
 *
 * EmailWorkspace is mocked to a single sentinel; both page components are then
 * rendered. Because both import EmailWorkspace from the same module, both must
 * render the sentinel — if a future change reintroduces a bespoke layout in
 * either page, that page stops rendering the sentinel and this test fails. The
 * captured `selectionMode` also confirms each route drives selection its own
 * way (id vs email) through the one shared component.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const seen = vi.hoisted(() => ({ props: [] as Record<string, unknown>[] }));

// One sentinel for the shared component, shared by both page imports below.
vi.mock("@/components/email", () => ({
  EmailWorkspace: (props: Record<string, unknown>) => {
    seen.props.push(props);
    return (
      <div
        data-testid="shared-workspace"
        data-mode={String(props.selectionMode)}
      />
    );
  },
}));

// The ticket page also pulls these; stub so the identity check needs no network.
vi.mock("@/hooks/useEmailByTicket", () => ({
  useEmailByTicket: () => ({
    email: null,
    auditTrail: [],
    isLoading: false,
    isError: false,
  }),
}));

import QueuePage from "./queue/page";
import TicketPage from "./tickets/[ticketId]/page";

describe("shared workspace identity", () => {
  beforeEach(() => {
    seen.props = [];
  });

  it("/queue renders the shared EmailWorkspace in id-selection mode", () => {
    render(<QueuePage />);
    const ws = screen.getByTestId("shared-workspace");
    expect(ws).toBeInTheDocument();
    expect(ws).toHaveAttribute("data-mode", "id");
  });

  it("/tickets/[ticketId] renders the SAME shared EmailWorkspace in email-selection mode", () => {
    render(<TicketPage params={{ ticketId: "21567" }} />);
    const ws = screen.getByTestId("shared-workspace");
    expect(ws).toBeInTheDocument();
    expect(ws).toHaveAttribute("data-mode", "email");
  });

  it("both routes drive the one shared component (a detailFooter only on the ticket route)", () => {
    render(<QueuePage />);
    render(<TicketPage params={{ ticketId: "21567" }} />);
    // Two renders → two prop sets captured from the SAME mocked component.
    expect(seen.props).toHaveLength(2);
    const [queueProps, ticketProps] = seen.props;
    expect(queueProps.selectionMode).toBe("id");
    expect(queueProps.detailFooter).toBeUndefined();
    expect(ticketProps.selectionMode).toBe("email");
    // The ticket route supplies the activity trail as the detail footer.
    expect(ticketProps.detailFooter).toBeDefined();
  });
});
