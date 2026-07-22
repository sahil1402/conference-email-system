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
      className={className}
    >
      Zendesk
    </a>
  );
}
