import "@testing-library/jest-dom/vitest";

import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Unmount React trees between tests so queries never leak across cases.
afterEach(() => cleanup());

// Radix's popper positioning (Tooltip / Popover) needs ResizeObserver, which
// jsdom doesn't implement. Shared here now that more than one suite renders
// popper-based components.
if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
