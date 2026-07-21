import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getEmailThread } from "@/lib/api";
import apiClient from "@/lib/api/client";
import type { EmailThreadResponse } from "@/types";

import { useEmailThread } from "./useEmailThread";

// Mock ONLY the axios client so both getEmailThread and the hook run their real
// code (URL building, unwrapping, React Query wiring) against a fake transport.
vi.mock("@/lib/api/client", () => ({
  default: { get: vi.fn() },
}));

const mockedGet = vi.mocked(apiClient.get);

// A representative payload matching the T3b response shape exactly.
const THREAD: EmailThreadResponse = {
  email_id: 42,
  messages: [
    {
      id: 1,
      zendesk_comment_id: 9001,
      public: true,
      author_role: "end-user",
      author_id: 500,
      plain_body: "initial inquiry",
      html_body: "<p>initial inquiry</p>",
      created_at: "2026-07-15T09:00:00",
      via_channel: "email",
      processing_results: [],
      latest_processing_result_id: null,
    },
    {
      id: 3,
      zendesk_comment_id: 9003,
      public: true,
      author_role: "end-user",
      author_id: 500,
      plain_body: "any update?",
      html_body: "<p>any update?</p>",
      created_at: "2026-07-15T09:20:00",
      via_channel: "email",
      processing_results: [
        {
          id: 7,
          thread_message_id: 3,
          classification: {
            intent: "author_list_change",
            confidence: 0.61,
            reasoning: "",
            secondary_intents: [],
          },
          routing: {
            lane: "human_review",
            reason: "",
            confidence_used: 0.61,
            threshold_applied: 0.65,
            override_reason: null,
          },
          draft: {
            draft_text: "draft",
            citations: ["policy_101"],
            model_used: "fallback",
            generation_metadata: {},
          },
          retrieval_context: {
            query: "q",
            intent: "",
            retrieved_ids: ["policy_101"],
          },
          lane: "human_review",
          confidence: 0.61,
          created_at: "2026-07-15T09:21:00",
        },
      ],
      latest_processing_result_id: 7,
    },
  ],
};

function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  mockedGet.mockReset();
});

describe("getEmailThread (API client)", () => {
  it("calls GET /emails/{id}/thread and returns the response body", async () => {
    mockedGet.mockResolvedValueOnce({ data: THREAD });

    const result = await getEmailThread(42);

    expect(mockedGet).toHaveBeenCalledWith("/emails/42/thread");
    expect(result).toEqual(THREAD);
    // Shape spot-checks — the piece that matters for downstream rendering (T5).
    expect(result.messages[1].latest_processing_result_id).toBe(7);
    expect(result.messages[1].processing_results[0].lane).toBe("human_review");
    expect(result.messages[0].processing_results).toEqual([]);
  });
});

describe("useEmailThread (hook)", () => {
  it("fetches and exposes the thread for a valid id", async () => {
    mockedGet.mockResolvedValueOnce({ data: THREAD });

    const { result } = renderHook(() => useEmailThread(42), {
      wrapper: wrapper(),
    });

    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect(mockedGet).toHaveBeenCalledWith("/emails/42/thread");
    expect(result.current.emailId).toBe(42);
    expect(result.current.isError).toBe(false);
  });

  it("is disabled (no request) when emailId is null", () => {
    renderHook(() => useEmailThread(null), { wrapper: wrapper() });

    expect(mockedGet).not.toHaveBeenCalled();
  });

  it("defaults to an empty messages list before data arrives", () => {
    mockedGet.mockResolvedValueOnce({ data: THREAD });

    const { result } = renderHook(() => useEmailThread(42), {
      wrapper: wrapper(),
    });

    // Synchronously, before the query resolves.
    expect(result.current.messages).toEqual([]);
    expect(result.current.isLoading).toBe(true);
  });
});
