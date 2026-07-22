"use client";

import { useEffect, useState, type Dispatch, type SetStateAction } from "react";

/**
 * Like `useState`, but the value is persisted to `localStorage` under `key` and
 * restored on mount. A chosen preference (e.g. the submit-as status or the reply
 * visibility) therefore survives remounts — selecting a different email remounts
 * the detail pane — and page reloads, staying put until changed again.
 *
 * SSR-safe: the store is only touched in the browser (guards `window`), and any
 * read/write error (private mode, quota, disabled storage) falls back silently.
 */
export function usePersistedState<T>(
  key: string,
  initial: T
): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    if (typeof window === "undefined") return initial;
    try {
      const raw = window.localStorage.getItem(key);
      return raw !== null ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* ignore quota / disabled storage */
    }
  }, [key, value]);

  return [value, setValue];
}
