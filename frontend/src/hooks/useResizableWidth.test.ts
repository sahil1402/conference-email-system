import { describe, expect, it, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";

import { useResizableWidth } from "./useResizableWidth";

const KEY = "test.listWidth";
// Mirrors the queue page: rail 52 + filter column 257 + drag handle 6.
const RESERVED = 315;
const MIN_REMAINING = 440;
const MIN = 240;
const MAX = 640;

const OPTS = { reservedWidth: RESERVED, minRemainingWidth: MIN_REMAINING };

function setViewport(px: number) {
  Object.defineProperty(window, "innerWidth", {
    value: px,
    configurable: true,
    writable: true,
  });
}

/** Drive a full drag from the current width by `deltaX` px. */
function drag(
  handleProps: ReturnType<typeof useResizableWidth>["handleProps"],
  fromX: number,
  toX: number
) {
  const target = {
    setPointerCapture() {},
    releasePointerCapture() {},
  } as unknown as Element;
  const ev = (clientX: number) =>
    ({ clientX, pointerId: 1, currentTarget: target }) as unknown as React.PointerEvent;

  act(() => handleProps.onPointerDown(ev(fromX)));
  act(() => handleProps.onPointerMove(ev(toX)));
  act(() => handleProps.onPointerUp(ev(toX)));
}

const originalWidth = window.innerWidth;

beforeEach(() => {
  window.localStorage.clear();
  setViewport(1920);
});

afterEach(() => {
  setViewport(originalWidth);
});

describe("useResizableWidth — viewport-aware clamping", () => {
  it("clamps a persisted width that no longer fits the current viewport", () => {
    // Saved on a wide monitor…
    window.localStorage.setItem(KEY, JSON.stringify(640));
    // …restored on a 1024px laptop.
    setViewport(1024);

    const { result } = renderHook(() =>
      useResizableWidth(KEY, 320, MIN, MAX, OPTS)
    );

    // 1024 - 315 - 440 = 269
    expect(result.current.width).toBe(269);
    // The detail pane keeps its floor.
    expect(1024 - RESERVED - result.current.width).toBeGreaterThanOrEqual(
      MIN_REMAINING
    );
  });

  it("re-clamps an already-set width when the window shrinks", () => {
    const { result } = renderHook(() =>
      useResizableWidth(KEY, 600, MIN, MAX, OPTS)
    );
    expect(result.current.width).toBe(600); // fits at 1920

    act(() => {
      setViewport(1024);
      window.dispatchEvent(new Event("resize"));
    });

    expect(result.current.width).toBe(269);
  });

  it("leaves a width alone when the viewport still accommodates it", () => {
    const { result } = renderHook(() =>
      useResizableWidth(KEY, 400, MIN, MAX, OPTS)
    );

    act(() => {
      setViewport(1440);
      window.dispatchEvent(new Event("resize"));
    });

    // 1440 - 315 - 440 = 685, above the static max, so 400 is untouched.
    expect(result.current.width).toBe(400);
  });

  it("never drops below `min`, even when the viewport can't satisfy both", () => {
    setViewport(800); // 800 - 315 - 440 = 45, far below min
    const { result } = renderHook(() =>
      useResizableWidth(KEY, 320, MIN, MAX, OPTS)
    );

    expect(result.current.width).toBe(MIN);
  });
});

describe("useResizableWidth — drag behaviour", () => {
  it("resizes normally when dragging within a viewport-valid range", () => {
    const { result } = renderHook(() =>
      useResizableWidth(KEY, 320, MIN, MAX, OPTS)
    );

    drag(result.current.handleProps, 100, 200); // +100px

    expect(result.current.width).toBe(420);
    expect(result.current.isDragging).toBe(false);
  });

  it("still honours the static max on a viewport with room to spare", () => {
    // 1920 - 315 - 440 = 1165, so the static 640 is the binding limit.
    const { result } = renderHook(() =>
      useResizableWidth(KEY, 600, MIN, MAX, OPTS)
    );

    drag(result.current.handleProps, 0, 400); // would be 1000

    expect(result.current.width).toBe(MAX);
  });

  it("still honours the static min when dragging left", () => {
    const { result } = renderHook(() =>
      useResizableWidth(KEY, 320, MIN, MAX, OPTS)
    );

    drag(result.current.handleProps, 400, 0); // would be -80

    expect(result.current.width).toBe(MIN);
  });

  it("applies the viewport ceiling to a drag on a small screen", () => {
    setViewport(1024);
    const { result } = renderHook(() =>
      useResizableWidth(KEY, 260, MIN, MAX, OPTS)
    );

    drag(result.current.handleProps, 0, 500); // would be 760

    expect(result.current.width).toBe(269);
  });

  it("works with no options — static bounds only (back-compat)", () => {
    setViewport(1024);
    const { result } = renderHook(() => useResizableWidth(KEY, 600, MIN, MAX));

    // No reserved/minRemaining, so the narrow viewport imposes nothing.
    expect(result.current.width).toBe(600);
  });
});
