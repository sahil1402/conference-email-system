"use client";

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";

import { cn } from "@/lib/utils";

/**
 * shadcn/ui Dialog, adapted to this app's design tokens.
 *
 * Same rationale as popover.tsx / dropdown-menu.tsx / tooltip.tsx: the stock
 * shadcn classes (bg-background / text-foreground / border-input …) reference
 * CSS vars this project doesn't define, so they'd render colorless. These map
 * to the real --surface-raised / --border / --text-* tokens from globals.css.
 *
 * Radix Dialog gives us focus trap, focus restore on close, Escape-to-close,
 * `aria-modal`, and background-scroll locking natively — none of which needs to
 * be hand-rolled (contrast PolicyDetailModal, which predates this wrapper and
 * implements all of it manually).
 *
 * The overlay is deliberately TRANSLUCENT (60% black), matching
 * PolicyDetailModal, so the ticket stays dimly visible behind the modal.
 */

const Dialog = DialogPrimitive.Root;
const DialogTrigger = DialogPrimitive.Trigger;
const DialogClose = DialogPrimitive.Close;

const DialogOverlay = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    data-testid="dialog-overlay"
    className={cn(
      "fixed inset-0 z-50",
      "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
      className
    )}
    style={{ backgroundColor: "rgba(0, 0, 0, 0.6)" }}
    {...props}
  />
));
DialogOverlay.displayName = DialogPrimitive.Overlay.displayName;

const DialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ className, children, ...props }, ref) => (
  <DialogPrimitive.Portal>
    <DialogOverlay />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        // Centred in the viewport regardless of where the trigger sits.
        "fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2",
        "rounded-xl border p-4 shadow-2xl outline-none",
        "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
        className
      )}
      style={{
        backgroundColor: "var(--surface-raised)",
        borderColor: "var(--border)",
        color: "var(--text-primary)",
      }}
      {...props}
    >
      {children}
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>
));
DialogContent.displayName = DialogPrimitive.Content.displayName;

const DialogTitle = DialogPrimitive.Title;
const DialogDescription = DialogPrimitive.Description;

export {
  Dialog,
  DialogTrigger,
  DialogClose,
  DialogOverlay,
  DialogContent,
  DialogTitle,
  DialogDescription,
};
