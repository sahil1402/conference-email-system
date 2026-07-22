"use client";

import { createContext, useContext, useState, type ReactNode } from "react";

/**
 * A single "slot" in the sidebar that a page can fill via a React portal.
 *
 * The sidebar is global (rendered once by {@link AppShell}), but some page-level
 * chrome — the queue's filters — belongs visually *inside* it, below the nav and
 * above the footer. Rather than lift that page state up, the sidebar exposes a
 * DOM node here (via a callback ref) and the page portals its own JSX into it.
 * The portalled content stays in the page's React tree, so it keeps its state
 * and handlers with no extra plumbing.
 *
 * The default value is a safe no-op, so a page using {@link useSidebarSlot}
 * outside a provider (e.g. an isolated component test) renders fine — it simply
 * has nowhere to portal to (`slotEl` stays `null`).
 */
interface SidebarSlotValue {
  slotEl: HTMLElement | null;
  setSlotEl: (el: HTMLElement | null) => void;
}

const SidebarSlotContext = createContext<SidebarSlotValue>({
  slotEl: null,
  setSlotEl: () => {},
});

export function SidebarSlotProvider({ children }: { children: ReactNode }) {
  const [slotEl, setSlotEl] = useState<HTMLElement | null>(null);
  return (
    <SidebarSlotContext.Provider value={{ slotEl, setSlotEl }}>
      {children}
    </SidebarSlotContext.Provider>
  );
}

export function useSidebarSlot(): SidebarSlotValue {
  return useContext(SidebarSlotContext);
}
