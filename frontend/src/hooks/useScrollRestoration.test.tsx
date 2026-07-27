/**
 * useScrollRestoration — save/restore/key-branching logic.
 *
 * jsdom does no layout, so real scrollTop/scrollHeight clamping can't be
 * exercised here. Instead a callback ref installs a working `scrollTop` backing
 * property on the element (before the hook's layout effect runs), so these
 * tests reliably prove the HOOK's contract: it saves on scroll, restores the
 * saved value on remount for the same key, and starts a never-seen key at the
 * top. Real browser scroll clamping is out of scope (manual/e2e).
 *
 * Keys are unique per test because the store is module-level (survives across
 * tests by design), so distinct keys keep the cases independent.
 */
import { describe, it, expect } from "vitest";
import { fireEvent, render } from "@testing-library/react";

import { useScrollRestoration } from "./useScrollRestoration";

/** Give an element a real, settable scrollTop (jsdom's is a no-op). */
function installScrollTop(el: HTMLElement) {
  let v = 0;
  Object.defineProperty(el, "scrollTop", {
    configurable: true,
    get: () => v,
    set: (n: number) => {
      v = n;
    },
  });
}

function Harness({ viewKey, ready }: { viewKey: string; ready: boolean }) {
  const { ref, onScroll } = useScrollRestoration<HTMLDivElement>(viewKey, ready);
  return (
    <div
      data-testid="scroller"
      ref={(el) => {
        if (el) installScrollTop(el);
        // Callback ref runs before layout effects (so scrollTop is installed in
        // time); forward it to the hook's ref. Cast: the hook exposes a readonly
        // RefObject, but React itself writes .current the same way here.
        (ref as { current: HTMLDivElement | null }).current = el;
      }}
      onScroll={onScroll}
    />
  );
}

const scroller = (r: ReturnType<typeof render>) =>
  r.getByTestId("scroller") as HTMLDivElement;

describe("useScrollRestoration", () => {
  it("restores the saved position when remounted with the same key", () => {
    const KEY = "restore-same-key";

    // Mount, scroll to 320, unmount (as a route navigation would).
    const first = render(<Harness viewKey={KEY} ready />);
    const el1 = scroller(first);
    el1.scrollTop = 320;
    fireEvent.scroll(el1);
    first.unmount();

    // Remount the same view → lands back at 320, not the top.
    const second = render(<Harness viewKey={KEY} ready />);
    expect(scroller(second).scrollTop).toBe(320);
  });

  it("starts at the top for a key that was never scrolled (filter change)", () => {
    const r = render(<Harness viewKey="never-seen-key" ready />);
    expect(scroller(r).scrollTop).toBe(0);
  });

  it("keeps each key's position independent", () => {
    const A = "key-A";
    const B = "key-B";

    const a = render(<Harness viewKey={A} ready />);
    const elA = scroller(a);
    elA.scrollTop = 150;
    fireEvent.scroll(elA);
    a.unmount();

    // A different view (e.g. after changing filters) is at the top…
    const b = render(<Harness viewKey={B} ready />);
    expect(scroller(b).scrollTop).toBe(0);
    b.unmount();

    // …and returning to A restores A's position.
    const a2 = render(<Harness viewKey={A} ready />);
    expect(scroller(a2).scrollTop).toBe(150);
  });

  it("does not restore until the content is ready", () => {
    const KEY = "gated-on-ready";

    const primed = render(<Harness viewKey={KEY} ready />);
    const el = scroller(primed);
    el.scrollTop = 200;
    fireEvent.scroll(el);
    primed.unmount();

    // ready=false → the container stays at the top (restoring against a
    // still-loading list would clamp to 0 anyway).
    const notReady = render(<Harness viewKey={KEY} ready={false} />);
    expect(scroller(notReady).scrollTop).toBe(0);
    notReady.unmount();

    // Once ready, it restores.
    const nowReady = render(<Harness viewKey={KEY} ready />);
    expect(scroller(nowReady).scrollTop).toBe(200);
  });
});
