"use client";

import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

/** localStorage key holding the persisted theme choice. */
const STORAGE_KEY = "confmail-theme";

/**
 * Reads and toggles the app color theme.
 *
 * The active theme lives on `<html data-theme="...">`, which the inline
 * anti-flash script in `layout.tsx` sets from localStorage *before* React
 * hydrates. On mount we therefore read the attribute the script already
 * applied rather than re-deriving from localStorage — that keeps the client's
 * first render in agreement with the server-rendered markup (no hydration
 * mismatch) and avoids a flash.
 *
 * Default/fallback is "dark" (the bare `:root` block), so every existing user
 * sees zero change until they explicitly pick light.
 */
export function useTheme(): { theme: Theme; toggleTheme: () => void } {
  // SSR + first client render must agree: start from "dark" (the default,
  // matching server output where no attribute is set), then reconcile to the
  // real DOM attribute in an effect.
  const [theme, setTheme] = useState<Theme>("dark");

  useEffect(() => {
    const current = document.documentElement.getAttribute("data-theme");
    setTheme(current === "light" ? "light" : "dark");
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => {
      const next: Theme = prev === "light" ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", next);
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // localStorage can throw (private mode / disabled) — persistence is
        // best-effort; the in-DOM attribute still reflects the choice.
      }
      return next;
    });
  }, []);

  return { theme, toggleTheme };
}
