"use client";

import { useMemo, useState } from "react";
import { Inbox, SearchX } from "lucide-react";

import { useEmailQueue } from "@/hooks/useEmailQueue";
import { useEmailQueueStream } from "@/hooks/useEmailQueueStream";
import {
  useApproveEmail,
  useRerouteEmail,
  useReassignChair,
} from "@/hooks/useEmailActions";
import { useChairs } from "@/hooks/useChairs";
import {
  EmailListItem,
  EmailDetail,
  EmailFilters,
} from "@/components/email";
import {
  Badge,
  EmptyState,
  ErrorBanner,
  LiveStatusDot,
  LoadingSpinner,
} from "@/components/ui";

type LaneFilter = "all" | "faq" | "human_review";

export default function QueuePage() {
  const { emails, isLoading, isError, refetch } = useEmailQueue();
  const { status: streamStatus } = useEmailQueueStream();
  const { mutate: approve, isPending: isApproving } = useApproveEmail();
  const { mutate: reroute, isPending: isRerouting } = useRerouteEmail();
  const { mutateAsync: reassignChairAsync, isPending: isReassigning } =
    useReassignChair();
  const { chairs, byId: chairsById } = useChairs();

  const [selectedEmailId, setSelectedEmailId] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [laneFilter, setLaneFilter] = useState<LaneFilter>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [chairFilter, setChairFilter] = useState<string>("all");

  const filteredEmails = useMemo(() => {
    const q = search.trim().toLowerCase();
    return emails.filter((email) => {
      if (q) {
        const haystack = `${email.subject} ${email.sender}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      if (laneFilter !== "all" && email.routing?.lane !== laneFilter) {
        return false;
      }
      if (statusFilter !== "all" && email.status !== statusFilter) {
        return false;
      }
      if (chairFilter === "unassigned") {
        if (email.assigned_chair_id != null) return false;
      } else if (chairFilter !== "all") {
        if (email.assigned_chair_id !== Number(chairFilter)) return false;
      }
      return true;
    });
  }, [emails, search, laneFilter, statusFilter, chairFilter]);

  const selectedEmail =
    selectedEmailId == null
      ? null
      : filteredEmails.find((e) => e.id === selectedEmailId) ??
        emails.find((e) => e.id === selectedEmailId) ??
        null;

  return (
    <div className="flex h-screen overflow-hidden">
      {/* LEFT PANE */}
      <aside
        className="flex w-80 shrink-0 flex-col"
        style={{ borderRight: "1px solid var(--border)" }}
      >
        <div className="space-y-4 p-4" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
          <div className="flex items-center gap-2">
            <h1
              className="text-lg font-semibold tracking-tight"
              style={{ color: "var(--text-primary)" }}
            >
              Email Queue
            </h1>
            <Badge variant="neutral" size="sm">
              {filteredEmails.length}
            </Badge>
            <span className="ml-auto">
              <LiveStatusDot status={streamStatus} />
            </span>
          </div>
          <EmailFilters
            search={search}
            onSearchChange={setSearch}
            laneFilter={laneFilter}
            onLaneChange={setLaneFilter}
            statusFilter={statusFilter as "all" | "PENDING" | "DRAFT_GENERATED" | "APPROVED"}
            onStatusChange={setStatusFilter}
            chairs={chairs}
            chairFilter={chairFilter}
            onChairChange={setChairFilter}
          />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {isLoading ? (
            <div className="flex items-center justify-center py-20">
              <LoadingSpinner size="lg" />
            </div>
          ) : isError ? (
            <div className="p-4">
              <ErrorBanner
                message="Couldn't load the email queue."
                onRetry={() => refetch()}
              />
            </div>
          ) : filteredEmails.length === 0 ? (
            <EmptyState
              icon={<SearchX className="h-5 w-5" />}
              title="No emails match your filters"
              description="Try clearing the search or switching lane / status filters."
            />
          ) : (
            <ul>
              {filteredEmails.map((email) => (
                <li
                  key={email.id}
                  style={{ borderBottom: "1px solid var(--border-subtle)" }}
                >
                  <EmailListItem
                    email={email}
                    isSelected={email.id === selectedEmailId}
                    onClick={() => setSelectedEmailId(email.id)}
                    chairName={
                      email.assigned_chair_id != null
                        ? chairsById.get(email.assigned_chair_id)?.name ?? null
                        : null
                    }
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>

      {/* RIGHT PANE */}
      <section className="min-w-0 flex-1 overflow-hidden">
        {selectedEmail ? (
          <EmailDetail
            key={selectedEmail.id}
            email={selectedEmail}
            isApproving={isApproving}
            isRerouting={isRerouting}
            isReassigning={isReassigning}
            chairs={chairs}
            onApprove={(finalText) =>
              approve({
                id: selectedEmail.id,
                data: { approved_by: "chair", final_text: finalText },
              })
            }
            onReroute={(reason) =>
              reroute({
                id: selectedEmail.id,
                data: { rerouted_by: "chair", reason, new_lane: "faq" },
              })
            }
            onReassignChair={(chairId, reason) =>
              reassignChairAsync({
                id: selectedEmail.id,
                data: { reassigned_by: "chair", new_chair_id: chairId, reason },
              })
            }
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <EmptyState
              icon={<Inbox className="h-6 w-6" />}
              title="Select an email to review"
              description="Choose an email from the queue to see details, policy citations, and the AI-generated draft."
            />
          </div>
        )}
      </section>
    </div>
  );
}
