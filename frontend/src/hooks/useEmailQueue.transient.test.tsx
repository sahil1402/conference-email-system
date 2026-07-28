/**
 * Behavior of the REAL useEmailQueue hook (no hook mock) across a key change.
 *
 * The transient window matters because EmailWorkspace derives
 * `pageCount = ceil(total / PAGE_SIZE)` and clamps `page > pageCount`. A `total`
 * that briefly reports 0 reads as "1 page" and silently resets the page.
 *
 * Only the network layer is mocked, so `data`/`status` transitions are produced
 * by React Query itself — which is the point: these assertions are evidence
 * about the library's real behavior, not about our mock's.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { useEmailQueue } from "./useEmailQueue";
import type { EmailQueueParams } from "@/lib/api";

const api = vi.hoisted(() => ({ getEmailQueue: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getEmailQueue: api.getEmailQueue,
}));

/** A never-settling fetch, so the pending window can be observed deliberately. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  };
}

function newClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}

const page = (total: number, n = 2) => ({
  emails: Array.from({ length: n }, (_, i) => ({ id: i + 1 })),
  total,
});

beforeEach(() => {
  api.getEmailQueue.mockReset();
});

describe("useEmailQueue — transient window on a key change", () => {
  it("keeps the previous total while an uncached page is in flight", async () => {
    const client = newClient();
    api.getEmailQueue.mockResolvedValueOnce(page(1727));

    const { result, rerender } = renderHook(
      ({ params }: { params: EmailQueueParams }) => useEmailQueue(params),
      {
        wrapper: wrapper(client),
        initialProps: { params: { limit: 100, offset: 0 } },
      }
    );

    await waitFor(() => expect(result.current.total).toBe(1727));

    // Switch to a page that has never been fetched — the fetch hangs, so the
    // in-flight window stays observable.
    const pending = deferred<ReturnType<typeof page>>();
    api.getEmailQueue.mockReturnValueOnce(pending.promise);
    rerender({ params: { limit: 100, offset: 500 } });

    // THE FIX: total holds the previous value instead of collapsing to 0.
    expect(result.current.total).toBe(1727);
    expect(result.current.emails.length).toBeGreaterThan(0);

    pending.resolve(page(1727));
    await waitFor(() => expect(result.current.total).toBe(1727));
  });

  it("still reports total 0 on the very first load (no previous data to keep)", async () => {
    const client = newClient();
    const pending = deferred<ReturnType<typeof page>>();
    api.getEmailQueue.mockReturnValueOnce(pending.promise);

    const { result } = renderHook(() => useEmailQueue({ limit: 100, offset: 0 }), {
      wrapper: wrapper(client),
    });

    // Nothing to fall back to — the initial empty state is unchanged.
    expect(result.current.total).toBe(0);
    expect(result.current.isLoading).toBe(true);

    pending.resolve(page(5));
    await waitFor(() => expect(result.current.total).toBe(5));
  });

  it("EVIDENCE: an ERRORED page fetch still drops total to 0", async () => {
    // placeholderData applies while the query is PENDING. Once it errors, v5
    // moves to status "error" with data undefined, so `?? 0` takes over again.
    // Documented here because it is the residual hole the clamp must survive.
    const client = newClient();
    api.getEmailQueue.mockResolvedValueOnce(page(1727));

    const { result, rerender } = renderHook(
      ({ params }: { params: EmailQueueParams }) => useEmailQueue(params),
      {
        wrapper: wrapper(client),
        initialProps: { params: { limit: 100, offset: 0 } },
      }
    );
    await waitFor(() => expect(result.current.total).toBe(1727));

    api.getEmailQueue.mockRejectedValueOnce(new Error("network down"));
    rerender({ params: { limit: 100, offset: 500 } });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.total).toBe(0);
  });
});
