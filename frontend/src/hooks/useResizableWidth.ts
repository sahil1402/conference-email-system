"use client";

import { useRef, useState } from "react";

import { usePersistedState } from "./usePersistedState";

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
 */
export function useResizableWidth(
  key: string,
  defaultWidth: number,
  min: number,
  max: number
): ResizableWidth {
  const [width, setWidth] = usePersistedState<number>(key, defaultWidth);
  const [isDragging, setIsDragging] = useState(false);
  const drag = useRef<{ startX: number; startWidth: number } | null>(null);

  const clamp = (w: number) => Math.min(max, Math.max(min, w));

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
