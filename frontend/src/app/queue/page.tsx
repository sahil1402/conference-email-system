"use client";

import { useState } from "react";

import { EmailWorkspace } from "@/components/email";

/**
 * The review queue. The entire 3-column workspace (filter sidebar, resizable
 * list, detail pane + chair actions) lives in the shared EmailWorkspace, which
 * the ticket route (/tickets/[ticketId]) renders too. The queue's only job is
 * to own selection as local state — a row click selects it in place.
 */
export default function QueuePage() {
  const [selectedEmailId, setSelectedEmailId] = useState<number | null>(null);

  return (
    <EmailWorkspace
      selectionMode="id"
      selectedEmailId={selectedEmailId}
      onSelectEmailId={setSelectedEmailId}
    />
  );
}
