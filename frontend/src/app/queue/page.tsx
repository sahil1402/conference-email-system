"use client";

import { useRouter } from "next/navigation";

import { EmailWorkspace } from "@/components/email";

/**
 * The review queue. The entire 3-column workspace (filter sidebar, resizable
 * list, detail pane + chair actions) lives in the shared EmailWorkspace, which
 * the ticket route (/tickets/[ticketId]) renders too.
 *
 * Selection is now URL-driven: a row click navigates to /tickets/{ticketId}
 * (every email has a shareable ticket URL), so the queue itself holds no
 * in-memory selection — its detail pane shows the "select an email" empty state
 * until the chair opens a ticket. (Redirect-to-first-ticket / other queue
 * behaviour is out of scope here — see C6.)
 */
export default function QueuePage() {
  const router = useRouter();

  const openTicket = (ticketId: number | null) => {
    if (ticketId != null) router.push(`/tickets/${ticketId}`);
  };

  return (
    <EmailWorkspace
      selectionMode="id"
      selectedEmailId={null}
      onOpenTicket={openTicket}
    />
  );
}
