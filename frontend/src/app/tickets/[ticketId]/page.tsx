"use client";

import { EmailWorkspace } from "@/components/email";
import { useEmailByTicket } from "@/hooks/useEmailByTicket";

import { TicketAuditTrail } from "./TicketAuditTrail";

/**
 * Standalone ticket view at /tickets/{ticketId}. Renders the SAME shared
 * EmailWorkspace as /queue (filter sidebar, resizable list, detail pane), so
 * the two never drift. The only difference is selection: the detail email is
 * resolved by the dedicated GET /emails/by-ticket/{id} fetch (so a ticket URL
 * works even when the row isn't on the current queue page) rather than local
 * click state.
 *
 * C2b scope: layout extraction only. Row clicks stay inert (C4 wires
 * navigation); loading/error polish + not-found is C3.
 */
export default function TicketPage({
  params,
}: {
  params: { ticketId: string };
}) {
  const { ticketId } = params;
  const { email, auditTrail, isLoading, isError } = useEmailByTicket(ticketId);

  return (
    <EmailWorkspace
      selectionMode="email"
      selectedEmail={email}
      // Row clicks are inert for now — C4 wires click-to-navigate.
      onSelectEmailId={() => {}}
      detailLoading={isLoading}
      detailError={isError ? `Couldn't load ticket ${ticketId}.` : null}
      detailFooter={<TicketAuditTrail entries={auditTrail} />}
    />
  );
}
