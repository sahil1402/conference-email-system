/**
 * The queue fetches one page of QUEUE_PAGE_SIZE (100) rows — captured off the
 * real useEmailQueue call the workspace makes.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import QueuePage from "@/app/queue/page";
import { QUEUE_PAGE_SIZE } from "@/lib/api";
import type { EmailQueueParams } from "@/lib/api";

const captured = vi.hoisted(() => ({ params: null as EmailQueueParams | null }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock("@/hooks/useEmailQueue", () => ({
  useEmailQueue: (params: EmailQueueParams) => {
    captured.params = params;
    return { emails: [], total: 0, isLoading: false, isError: false, refetch: vi.fn() };
  },
}));
vi.mock("@/hooks/useQueueFacets", () => ({
  useQueueFacets: () => ({ byZendeskStatus: {}, bySource: {}, sources: [], isLoading: false, isError: false }),
}));
vi.mock("@/hooks/useChairs", () => ({
  useChairs: () => ({ chairs: [], byId: new Map(), isLoading: false, isError: false }),
}));
vi.mock("@/hooks/useAppConfig", () => ({ useAppConfig: () => ({ allowAutoSend: false }) }));
vi.mock("@/hooks/useEmailQueueStream", () => ({ useEmailQueueStream: () => ({ status: "live" }) }));
vi.mock("@/hooks/useEmailThread", () => ({
  useEmailThread: () => ({ messages: [], isLoading: false, isError: false }),
}));

function renderQueue() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <QueuePage />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  window.localStorage.clear();
  captured.params = null;
});

describe("queue page size", () => {
  it("QUEUE_PAGE_SIZE is 100", () => {
    expect(QUEUE_PAGE_SIZE).toBe(100);
  });

  it("requests limit=100 (QUEUE_PAGE_SIZE), not 200", () => {
    renderQueue();
    expect(captured.params?.limit).toBe(100);
    expect(captured.params?.limit).toBe(QUEUE_PAGE_SIZE);
  });
});
