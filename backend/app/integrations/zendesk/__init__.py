"""Zendesk integration.

Exposes the credential layer: the provider interface, the two concrete
implementations (API-token and client_credentials OAuth), and the config-driven
factory. No HTTP or ticket-fetching logic lives here yet (that is a later
piece) — the ticket export in ``scripts/pull_zendesk_tickets.py`` remains a
standalone research tool.
"""

from app.integrations.zendesk.credential_provider import (
    OAuthCredentialProvider,
    OAuthNotImplementedError,
    TokenCredentialProvider,
    ZendeskAuthError,
    ZendeskCredentialError,
    ZendeskCredentialProvider,
    get_zendesk_credential_provider,
)

__all__ = [
    "ZendeskCredentialProvider",
    "TokenCredentialProvider",
    "OAuthCredentialProvider",
    "ZendeskCredentialError",
    "ZendeskAuthError",
    "OAuthNotImplementedError",
    "get_zendesk_credential_provider",
]
