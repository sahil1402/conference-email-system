import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { TicketAuditTrail } from "./TicketAuditTrail";
import type { EmailAuditTrailEntry } from "@/types";

// Given oldest-first (as the endpoint returns), the component must render
// newest-first.
const entries: EmailAuditTrailEntry[] = [
  {
    id: 1,
    email_id: "5",
    action: "classified",
    actor: "pipeline",
    timestamp: "2026-07-20T09:00:00Z",
    metadata: null,
  },
  {
    id: 2,
    email_id: "5",
    action: "approved",
    actor: "chair",
    timestamp: "2026-07-22T15:00:00Z",
    metadata: null,
  },
];

describe("TicketAuditTrail", () => {
  it("is collapsed by default and expands on click", async () => {
    render(<TicketAuditTrail entries={entries} />);
    // Header (with count) shows; entries are hidden until expanded.
    expect(screen.getByRole("button", { name: /Activity \(2\)/ })).toBeInTheDocument();
    expect(screen.queryByText("classified")).toBeNull();

    await userEvent.setup().click(screen.getByRole("button", { name: /Activity \(2\)/ }));
    expect(screen.getByText("classified")).toBeInTheDocument();
  });

  it("orders entries latest-first regardless of input order", async () => {
    render(<TicketAuditTrail entries={entries} />);
    await userEvent.setup().click(screen.getByRole("button", { name: /Activity \(2\)/ }));

    const items = screen.getAllByRole("listitem");
    // Input was [classified (07-20), approved (07-22)]; rendered newest-first.
    expect(items[0]).toHaveTextContent("approved");
    expect(items[1]).toHaveTextContent("classified");
  });
});
