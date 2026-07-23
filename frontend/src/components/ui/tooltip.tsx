"use client";

import * as React from "react";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";

import { cn } from "@/lib/utils";

/**
 * shadcn/ui Tooltip, adapted to this app's design tokens.
 *
 * Like popover.tsx and dropdown-menu.tsx, the stock shadcn classes (bg-popover /
 * text-popover-foreground / border-input …) reference CSS vars this project
 * doesn't define, so they'd render colorless. These map directly to the real
 * --surface-raised / --border / --text-* tokens from globals.css instead.
 *
 * Styled like the popover but lighter — a tooltip is a short label, not a
 * content panel, so it uses tighter padding and smaller text.
 *
 * NOTE: Radix requires a `TooltipProvider` ancestor. Wrap a *group* of tooltips
 * (e.g. the whole nav rail) in ONE provider rather than each trigger — a shared
 * provider is what enables "skip delay", so moving between adjacent icons shows
 * the next tooltip instantly instead of re-waiting the full delay.
 */

/** Provider with a snappier default than Radix's 700ms (too slow for a nav rail). */
function TooltipProvider({
  delayDuration = 300,
  ...props
}: React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Provider>) {
  return <TooltipPrimitive.Provider delayDuration={delayDuration} {...props} />;
}

const Tooltip = TooltipPrimitive.Root;
const TooltipTrigger = TooltipPrimitive.Trigger;

const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 6, ...props }, ref) => (
  // Portalled so the tooltip escapes scrolling/overflow-hidden ancestors (the
  // sidebar nav scrolls, which would otherwise clip it).
  <TooltipPrimitive.Portal>
    <TooltipPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      className={cn(
        "z-50 overflow-hidden rounded-lg border px-2 py-1 text-xs shadow-lg",
        "data-[state=delayed-open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=delayed-open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=delayed-open]:zoom-in-95",
        className
      )}
      style={{
        backgroundColor: "var(--surface-raised)",
        borderColor: "var(--border)",
        color: "var(--text-primary)",
      }}
      {...props}
    />
  </TooltipPrimitive.Portal>
));
TooltipContent.displayName = TooltipPrimitive.Content.displayName;

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider };
