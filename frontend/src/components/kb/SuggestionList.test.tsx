import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { SuggestionList } from "./SuggestionList";
import type { PolicySuggestion } from "@/types";

function makeSuggestion(overrides: Partial<PolicySuggestion> = {}): PolicySuggestion {
  return {
    id: 1,
    source_email_id: 42,
    source_zendesk_ticket_id: 123,
    experience_summary: "Chair clarified the late-registration grace period.",
    title: "T",
    content: "C",
    category: null,
    intents: [],
    generalizable: true,
    reason: null,
    confidence: 0.8,
    conflict_report: null,
    seen_count: 1,
    status: "pending",
    created_at: "2026-07-29T00:00:00Z",
    ...overrides,
  };
}

describe("SuggestionList", () => {
  it("renders pending suggestions and fires select", () => {
    const onSelect = vi.fn();
    render(
      <SuggestionList
        suggestions={[makeSuggestion({ id: 1, title: "T", content: "C" }) as unknown as PolicySuggestion]}
        onSelect={onSelect}
        onReject={vi.fn()}
      />,
    );
    expect(screen.getByText("T")).toBeInTheDocument();
    fireEvent.click(screen.getByText("T"));
    expect(onSelect).toHaveBeenCalled();
  });

  it("passes the exact suggestion object to onSelect", () => {
    const onSelect = vi.fn();
    const suggestion = makeSuggestion({ id: 7, title: "Grace period" });
    render(<SuggestionList suggestions={[suggestion]} onSelect={onSelect} onReject={vi.fn()} />);

    fireEvent.click(screen.getByText("Grace period"));

    expect(onSelect).toHaveBeenCalledWith(suggestion);
  });

  it("fires onReject (not onSelect) when the Reject button is clicked", () => {
    const onSelect = vi.fn();
    const onReject = vi.fn();
    const suggestion = makeSuggestion({ id: 3, title: "Reject me" });
    render(<SuggestionList suggestions={[suggestion]} onSelect={onSelect} onReject={onReject} />);

    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    expect(onReject).toHaveBeenCalledWith(suggestion);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("renders one row per suggestion, independently clickable", () => {
    const onSelect = vi.fn();
    const a = makeSuggestion({ id: 1, title: "First" });
    const b = makeSuggestion({ id: 2, title: "Second" });
    render(<SuggestionList suggestions={[a, b]} onSelect={onSelect} onReject={vi.fn()} />);

    expect(screen.getByText("First")).toBeInTheDocument();
    expect(screen.getByText("Second")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Second"));
    expect(onSelect).toHaveBeenCalledWith(b);
    expect(onSelect).not.toHaveBeenCalledWith(a);
  });

  it("renders nothing for an empty list (no crash)", () => {
    const { container } = render(
      <SuggestionList suggestions={[]} onSelect={vi.fn()} onReject={vi.fn()} />,
    );
    expect(container.querySelectorAll("li").length).toBe(0);
  });
});
