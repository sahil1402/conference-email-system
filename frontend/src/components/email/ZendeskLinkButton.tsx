import { cn } from "@/lib/utils";

interface ZendeskLinkButtonProps {
  /**
   * The backend-built Zendesk agent-UI deep link (Email.zendesk_ticket_url).
   * Null/undefined for non-Zendesk rows or when the subdomain isn't configured.
   */
  url: string | null | undefined;
  className?: string;
}

/**
 * Opens the corresponding Zendesk ticket in a new tab. Owns its own
 * "render nothing when there's no ticket" logic, so callers can pass the raw
 * `zendesk_ticket_url` without a surrounding conditional.
 *
 * (Z3a: bare functional link — styling/icon/aria come in Z3b/Z3c.)
 */
export function ZendeskLinkButton({ url, className }: ZendeskLinkButtonProps) {
  if (url == null) return null;

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        // Size/shape matched to the header's `Badge size="sm"` pills so it sits
        // in the badge row without looking oversized; a border + hover accent
        // shift set it apart as an action rather than a status.
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-1 text-[11px] font-medium leading-none transition-colors",
        "border-[var(--border)] bg-[var(--surface-raised)] text-[var(--text-secondary)]",
        "hover:border-[var(--accent)] hover:bg-[var(--accent-subtle)] hover:text-[var(--accent)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--background)]",
        className
      )}
    >
      Zendesk
    </a>
  );
}
