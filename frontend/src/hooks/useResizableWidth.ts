"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { usePersistedState } from "./usePersistedState";

interface ResizableWidthOptions {
  /**
   * Fixed chrome (px) that is never available to this column or to the pane
   * beside it — e.g. the nav rail, a sibling filter column, the drag handle.
   */
  reservedWidth?: number;
  /** Minimum width (px) that must remain for the pane beside this column. */
  minRemainingWidth?: number;
}

interface ResizableWidth {
  /** Current width in px (persisted across reloads). */
  width: number;
  /** True while the handle is actively being dragged. */
  isDragging: boolean;
  /** Spread onto the drag handle element. */
  handleProps: {
    onPointerDown: (e: React.PointerEvent) => void;
    onPointerMove: (e: React.PointerEvent) => void;
    onPointerUp: (e: React.PointerEvent) => void;
    onLostPointerCapture: (e: React.PointerEvent) => void;
  };
}

/**
 * Drag-to-resize a column, persisting the chosen width under `key`.
 *
 * Uses pointer capture so the drag keeps tracking even when the cursor leaves
 * the thin handle; the width is clamped to [min, max]. Body `user-select` /
 * `cursor` are pinned during the drag so text isn't selected and the resize
 * cursor holds across the whole window.
 *
 * The static [min, max] range is viewport-blind, so a width persisted on a wide
 * monitor could be restored on a small one and squeeze the neighbouring pane to
 * nothing. Passing `reservedWidth` / `minRemainingWidth` adds a second, dynamic
 * ceiling — whichever bound is more restrictive wins — re-applied on mount and
 * on resize (never mid-drag).
 */
export function useResizableWidth(
  key: string,
  defaultWidth: number,
  min: number,
  max: number,
  options: ResizableWidthOptions = {}
): ResizableWidth {
  const { reservedWidth = 0, minRemainingWidth = 0 } = options;

  const [width, setWidth] = usePersistedState<number>(key, defaultWidth);
  const [isDragging, setIsDragging] = useState(false);
  const drag = useRef<{ startX: number; startWidth: number } | null>(null);

  /**
   * Largest width that still leaves `minRemainingWidth` for the neighbouring
   * pane on the CURRENT viewport, never exceeding the static `max`. Floored at
   * `min`: when a viewport can't satisfy both minimums, keep this column usable
   * and let the flex-1 neighbour absorb the shortfall rather than invert the
   * range.
   */
  const effectiveMax = useCallback(() => {
    if (typeof window === "undefined") return max;
    const fits = window.innerWidth - reservedWidth - minRemainingWidth;
    return Math.max(min, Math.min(max, fits));
  }, [max, min, reservedWidth, minRemainingWidth]);

  const clamp = useCallback(
    (w: number) => Math.min(effectiveMax(), Math.max(min, w)),
    [effectiveMax, min]
  );

  // Re-clamp the restored value on mount, and whenever the viewport changes.
  // Skipped while a drag is in flight so a resize can't yank the column out
  // from under the pointer.
  useEffect(() => {
    const apply = () => {
      if (drag.current) return;
      setWidth((w) => {
        const next = clamp(w);
        return next === w ? w : next;
      });
    };
    apply();
    window.addEventListener("resize", apply);
    return () => window.removeEventListener("resize", apply);
  }, [clamp, setWidth]);

  const onPointerDown = (e: React.PointerEvent) => {
    drag.current = { startX: e.clientX, startWidth: width };
    setIsDragging(true);
    e.currentTarget.setPointerCapture(e.pointerId);
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    setWidth(clamp(drag.current.startWidth + (e.clientX - drag.current.startX)));
  };

  const endDrag = (e: React.PointerEvent) => {
    if (!drag.current) return;
    drag.current = null;
    setIsDragging(false);
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* pointer already released */
    }
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
  };

  return {
    width,
    isDragging,
    handleProps: {
      onPointerDown,
      onPointerMove,
      onPointerUp: endDrag,
      onLostPointerCapture: endDrag,
    },
  };
}
