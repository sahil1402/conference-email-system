import axios, { AxiosError, type AxiosInstance } from "axios";

import type { ApiError } from "@/types";

/**
 * Shared axios instance for all backend calls.
 * baseURL comes from NEXT_PUBLIC_API_URL (see frontend/.env.local).
 */
const apiClient: AxiosInstance = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL,
  timeout: 30_000,
  headers: {
    "Content-Type": "application/json",
  },
});

/**
 * Normalize every rejected request into a consistent {@link ApiError} shape so
 * callers (and React Query) never have to dig through the raw axios error.
 */
apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status ?? 0;

    let detail = error.message || "An unexpected error occurred";
    // Kept alongside the flattened string: an endpoint that returns a
    // structured detail (`{message, error_type, ...}`) encodes a distinction
    // the caller is meant to branch on, and JSON.stringify leaves it only
    // recoverable by pattern-matching prose. `detail` is untouched, so no
    // existing consumer changes.
    let raw: unknown;
    const data = error.response?.data as unknown;
    if (data && typeof data === "object" && "detail" in data) {
      const rawDetail = (data as { detail: unknown }).detail;
      raw = rawDetail;
      detail =
        typeof rawDetail === "string" ? rawDetail : JSON.stringify(rawDetail);
    }

    const normalized: ApiError = { detail, status, data: raw };
    return Promise.reject(normalized);
  }
);

export default apiClient;
