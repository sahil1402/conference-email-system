"use client";

import { useCallback, useLayoutEffect, useRef } from "react";

/**
 * Saved scroll positions, keyed by view. MODULE-LEVEL on purpose: it must
 * outlive the component, because /queue and /tickets/[id] are separate route
 * segments — navigating between them unmounts and remounts the workspace, so a
 * component-scoped ref (or the DOM node's own scrollTop) is gone on return. A
 * plain object/Map at module scope survives for the whole SPA session.
 */
const scrollPositions = new Map<string, number>();

/**
 * Preserve a scroll container's position across unmount/remount, keyed by
 * `key` — the identity of the *view* (e.g. the active filter set). Attach the
 * returned `ref` and `onScroll` to the scrollable element.
 *
 * - Same `key` on remount (returning to the same view) → restores where the
 *   user left off.
 * - Different `key` (a genuinely different result set — new filters/search) →
 *   restores that key's saved position, or the TOP if it was never visited, so
 *   changing filters resets to the top as expected.
 *
 * `ready` gates restoration until the content is actually present; restoring
 * against an empty/still-loading container would clamp scrollTop to 0.
 *
 * NOTE: restoration is deliberately keyed ONLY on `[key, ready]` — content-only
 * re-renders (e.g. a live SSE row arriving under the same filters) must NOT
 * re-run it and yank the scroll out from under the user.
 */
export function useScrollRestoration<T extends HTMLElement>(
  key: string,
  ready: boolean
): { ref: React.RefObject<T>; onScroll: () => void } {
  const ref = useRef<T>(null);

  const onScroll = useCallback(() => {
    const el = ref.current;
    if (el) scrollPositions.set(key, el.scrollTop);
  }, [key]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !ready) return;
    el.scrollTop = scrollPositions.get(key) ?? 0;
  }, [key, ready]);

  return { ref, onScroll };
}
