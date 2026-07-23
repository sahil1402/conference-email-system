"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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
 * happy-path layout is untouched. Clicking a list row (or advancing after a
 * send) navigates to that ticket's URL.
 */
export default function TicketPage({
  params,
}: {
  params: { ticketId: string };
}) {
  const { ticketId } = params;
  const router = useRouter();
  const { email, auditTrail, isLoading, isError, error, refetch } =
    useEmailByTicket(ticketId);

  // Selection is URL-driven: opening a list row (or advancing after a send) is a
  // navigation to that ticket. Navigating to the ticket already in view is
  // harmless (React Query serves the cached detail).
  const openTicket = (nextTicketId: number | null) => {
    if (nextTicketId != null) router.push(`/tickets/${nextTicketId}`);
  };

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
      onOpenTicket={openTicket}
      detailLoading={isLoading}
      detailError={detailError}
      detailFooter={<TicketAuditTrail entries={auditTrail} />}
    />
  );
}
