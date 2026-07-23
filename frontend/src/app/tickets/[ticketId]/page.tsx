"use client";

import { useEmailQueue } from "@/hooks/useEmailQueue";
import { useEmailByTicket } from "@/hooks/useEmailByTicket";
import { useChairs } from "@/hooks/useChairs";
import { useAppConfig } from "@/hooks/useAppConfig";
import {
  useApproveEmail,
  useRerouteEmail,
  useReassignChair,
  useRetryEmail,
  useSendEmail,
} from "@/hooks/useEmailActions";
import { EmailListItem, EmailDetail } from "@/components/email";
import { ErrorBanner, LoadingSpinner } from "@/components/ui";

import { TicketAuditTrail } from "./TicketAuditTrail";

/**
 * Standalone ticket view at /tickets/{ticketId}. Mirrors the queue's list +
 * detail panes (reusing EmailListItem + EmailDetail as-is), but resolves the
 * detail email via the dedicated GET /emails/by-ticket/{id} fetch rather than
 * hunting the queue array — so a ticket URL works even when the row isn't on
 * the current queue page.
 *
 * C2 scope: standalone rendering only. Row clicks are intentionally inert
 * (C4 wires navigation); loading/error/not-found polish is C3; the collapsible
 * filter column + drag-resize from the queue are deferred (not needed to render
 * the ticket's email in the detail pane).
 */
export default function TicketPage({
  params,
}: {
  params: { ticketId: string };
}) {
  const { ticketId } = params;

  const { emails } = useEmailQueue({ limit: 200 });
  const { email, auditTrail, isLoading, isError } = useEmailByTicket(ticketId);
  const { chairs, byId: chairsById } = useChairs();
  const { allowAutoSend } = useAppConfig();

  const { mutate: approve, isPending: isApproving } = useApproveEmail();
  const { mutate: send } = useSendEmail();
  const { mutate: reroute, isPending: isRerouting } = useRerouteEmail();
  const { mutateAsync: reassignChairAsync, isPending: isReassigning } =
    useReassignChair();
  const { mutate: retry } = useRetryEmail();

  const selectedTicketId = Number(ticketId);

  return (
    <div className="flex h-[calc(100vh-3.5rem)] overflow-hidden md:h-[calc(100vh-var(--topbar-height))]">
      {/* LEFT PANE — the queue list (row clicks inert until C4). */}
      <aside
        className="flex w-80 shrink-0 flex-col"
        style={{ borderRight: "1px solid var(--border)" }}
      >
        <div
          className="p-4"
          style={{ borderBottom: "1px solid var(--border-subtle)" }}
        >
          <h1
            className="text-lg font-semibold tracking-tight"
            style={{ color: "var(--text-primary)" }}
          >
            Email Queue
          </h1>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <ul>
            {emails.map((e) => (
              <li
                key={e.id}
                style={{ borderBottom: "1px solid var(--border-subtle)" }}
              >
                <EmailListItem
                  email={e}
                  isSelected={e.zendesk_ticket_id === selectedTicketId}
                  onClick={() => {
                    /* C4 wires navigation to /tickets/{ticketId}. */
                  }}
                  chairName={
                    e.assigned_chair_id != null
                      ? chairsById.get(e.assigned_chair_id)?.name ?? null
                      : null
                  }
                />
              </li>
            ))}
          </ul>
        </div>
      </aside>

      {/* RIGHT PANE — detail resolved by ticket id, plus the activity trail. */}
      <section className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {isLoading ? (
          <div className="flex h-full items-center justify-center">
            <LoadingSpinner size="lg" />
          </div>
        ) : isError || email == null ? (
          <div className="flex h-full items-center justify-center p-6">
            <ErrorBanner message={`Couldn't load ticket ${ticketId}.`} />
          </div>
        ) : (
          <>
            <div className="min-h-0 flex-1">
              <EmailDetail
                key={email.id}
                email={email}
                isApproving={isApproving}
                isRerouting={isRerouting}
                isReassigning={isReassigning}
                chairs={chairs}
                allowAutoSend={allowAutoSend}
                onApprove={(finalText, targetStatus, isPublic) =>
                  approve(
                    {
                      id: email.id,
                      data: {
                        approved_by: "chair",
                        final_text: finalText,
                        target_status: targetStatus,
                      },
                    },
                    {
                      onSuccess: () =>
                        send({
                          id: email.id,
                          data: { public: isPublic, target_status: targetStatus },
                        }),
                    }
                  )
                }
                onReroute={(reason) =>
                  reroute({
                    id: email.id,
                    data: { rerouted_by: "chair", reason, new_lane: "faq" },
                  })
                }
                onReassignChair={(chairId, reason) =>
                  reassignChairAsync({
                    id: email.id,
                    data: {
                      reassigned_by: "chair",
                      new_chair_id: chairId,
                      reason,
                    },
                  })
                }
                onRetry={() => retry(email.id)}
                sendError={null}
              />
            </div>
            <TicketAuditTrail entries={auditTrail} />
          </>
        )}
      </section>
    </div>
  );
}
