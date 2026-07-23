"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { FileQuestion } from "lucide-react";

import { EmailWorkspace } from "@/components/email";
import { Button, EmptyState, ErrorBanner } from "@/components/ui";
import { useEmailByTicket } from "@/hooks/useEmailByTicket";

import { TicketAuditTrail } from "./TicketAuditTrail";

/**
 * Standalone ticket view at /tickets/{ticketId}. Renders the SAME shared
 * EmailWorkspace as /queue; the only difference is that the detail email is
 * resolved by the dedicated GET /emails/by-ticket/{id} fetch (so a ticket URL
 * works even when the row isn't on the current queue page).
 *
 * This module owns the non-happy-path detail states (loading / not-found /
 * error) and feeds them into the workspace's detail slot — the workspace's
 * happy-path layout is untouched. Row clicks stay inert (C4 wires navigation).
 */
export default function TicketPage({
  params,
}: {
  params: { ticketId: string };
}) {
  const { ticketId } = params;
  const { email, auditTrail, isLoading, isError, error, refetch } =
    useEmailByTicket(ticketId);

  // 404 (no such ticket) and 422 (non-numeric id in the URL) both mean "this
  // ticket doesn't exist" to the chair — fold into one not-found state. Any
  // other failure (5xx, network) is an unexpected error shown distinctly.
  const isNotFound =
    isError && (error?.status === 404 || error?.status === 422);

  const detailError: ReactNode = !isError ? null : isNotFound ? (
    <EmptyState
      icon={<FileQuestion className="h-6 w-6" />}
      title="Ticket not found"
      description={`We couldn't find a ticket with ID ${ticketId}.`}
      action={
        <Button asChild variant="secondary" size="sm">
          <Link href="/queue" prefetch={false}>
            Back to queue
          </Link>
        </Button>
      }
    />
  ) : (
    <div className="w-full max-w-md">
      <ErrorBanner
        message="Something went wrong loading this ticket. Please try again."
        onRetry={() => refetch()}
      />
    </div>
  );

  return (
    <EmailWorkspace
      selectionMode="email"
      selectedEmail={email}
      // Row clicks are inert for now — C4 wires click-to-navigate.
      onSelectEmailId={() => {}}
      detailLoading={isLoading}
      detailError={detailError}
      detailFooter={<TicketAuditTrail entries={auditTrail} />}
    />
  );
}
