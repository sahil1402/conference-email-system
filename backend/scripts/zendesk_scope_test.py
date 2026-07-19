"""Diagnostic: does the ConfMail OAuth client have WRITE access, not just read?

Read access is already proven (the incremental ticket pull). This one-off
script answers the write question definitively, as safely and reversibly as
possible, in two stages:

  Stage A (always, non-mutating): request a token with scope "read write". If
  the token endpoint rejects the wider scope, write access does not exist and we
  stop here having touched nothing.

  Stage B (only with --confirm-write): perform ONE minimal, reversible write —
  add a single INTERNAL note (public:false) with clearly identifiable test
  content to one ticket, then report the ticket id so it can be removed by hand.

Safety rules baked in:
  * Internal note ONLY (comment.public is always False). Never a public reply.
  * No status change, no tags, no other field touched.
  * Exactly ONE write attempt per run, gated behind --confirm-write.
  * The write target is a *solved* ticket by default, NOT a closed one: Zendesk
    blocks updates to closed tickets platform-wide (422 "Closed tickets cannot
    be updated"), which would confound the scope signal — a 403 on a solved
    ticket cleanly means "scope is read-only".
  * Nothing about a real ticket is hardcoded; the target is discovered per run.

Credentials come from Settings/.env (ZENDESK_OAUTH_CLIENT_ID / _SECRET /
SUBDOMAIN) — the project convention — never from a checked-in secrets file.

Usage (run from backend/):
    python scripts/zendesk_scope_test.py                  # Stage A only (safe)
    python scripts/zendesk_scope_test.py --confirm-write   # Stage A + one write
    python scripts/zendesk_scope_test.py --confirm-write --status pending
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

# Make the app package importable so we read credentials from Settings/.env
# (the established convention), rather than re-parsing secrets by hand.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402

WRITE_SCOPE = "read write"
# A solved ticket is resolved (least active) yet still updatable — unlike a
# closed one. See the module docstring for why closed tickets are unsuitable.
DEFAULT_TARGET_STATUS = "solved"


def base_url() -> str:
    return f"https://{settings.ZENDESK_SUBDOMAIN}.zendesk.com"


def request_token(client: httpx.Client, scope: str) -> tuple[bool, dict]:
    """Attempt a client_credentials token for `scope`. Returns (ok, detail)."""
    resp = client.post(
        f"{base_url()}/oauth/tokens",
        json={
            "grant_type": "client_credentials",
            "client_id": settings.ZENDESK_OAUTH_CLIENT_ID,
            "client_secret": settings.ZENDESK_OAUTH_CLIENT_SECRET,
            "scope": scope,
        },
    )
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}
    return resp.is_success, {"status": resp.status_code, "body": body}


def find_target_ticket(client: httpx.Client, token: str, status: str) -> int | None:
    """Discover ONE updatable ticket id via search (nothing hardcoded)."""
    resp = client.get(
        f"{base_url()}/api/v2/search.json",
        params={
            "query": f"type:ticket status:{status}",
            "sort_by": "created_at",
            "sort_order": "desc",
            "per_page": 1,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0]["id"] if results else None


def attempt_internal_note(
    client: httpx.Client, token: str, ticket_id: int, note: str
) -> httpx.Response:
    """PUT a single INTERNAL (public:false) note. No other field is touched."""
    return client.put(
        f"{base_url()}/api/v2/tickets/{ticket_id}.json",
        json={"ticket": {"comment": {"body": note, "public": False}}},
        headers={"Authorization": f"Bearer {token}"},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--confirm-write",
        action="store_true",
        help="Perform the ONE internal-note write test (Stage B). Omitted = "
        "Stage A token check only (no mutation).",
    )
    parser.add_argument(
        "--status",
        default=DEFAULT_TARGET_STATUS,
        help=f"Ticket status to target for the write (default {DEFAULT_TARGET_STATUS!r}). "
        "'closed' is intentionally a poor choice — closed tickets are immutable.",
    )
    args = parser.parse_args()

    if not settings.ZENDESK_OAUTH_CLIENT_ID or not settings.ZENDESK_OAUTH_CLIENT_SECRET:
        sys.exit(
            "Missing ZENDESK_OAUTH_CLIENT_ID / ZENDESK_OAUTH_CLIENT_SECRET in "
            ".env — cannot run the scope test."
        )

    client = httpx.Client(timeout=60)

    # --- Stage A: does the client get a 'read write' token at all? -----------
    print(f"[Stage A] Requesting token with scope={WRITE_SCOPE!r} ...")
    ok, detail = request_token(client, WRITE_SCOPE)
    print(f"  HTTP {detail['status']}")
    print("  body:", json.dumps(detail["body"], indent=2)[:1500])
    if not ok:
        print(
            "\nRESULT: the token endpoint REJECTED the wider scope. This alone "
            "indicates WRITE access is not available to this client "
            "(commonly an 'invalid_scope' error). No ticket was touched."
        )
        return
    token = detail["body"].get("access_token")
    granted = detail["body"].get("scope")
    print(f"  Token acquired. Granted scope: {granted!r}")
    print(
        "  NOTE: a granted token is necessary but not sufficient — Zendesk may "
        "still 403 the actual write. Stage B is the definitive check."
    )

    if not args.confirm_write:
        print(
            "\n[Stage B skipped] Re-run with --confirm-write to perform the "
            f"single internal-note write test against one '{args.status}' ticket."
        )
        return

    # --- Stage B: one safe, reversible, INTERNAL-note write ------------------
    print(f"\n[Stage B] Discovering one '{args.status}' ticket to test against ...")
    try:
        ticket_id = find_target_ticket(client, token, args.status)
    except httpx.HTTPError as exc:
        sys.exit(f"  Could not search for a target ticket: {exc}")
    if ticket_id is None:
        sys.exit(f"  No '{args.status}' ticket found to test against.")
    print(f"  Target ticket id: {ticket_id}")

    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    note = f"[ConfMail integration test — safe to ignore/delete, {stamp}]"
    print(f"  Posting INTERNAL note (public=false): {note!r}")

    try:
        resp = attempt_internal_note(client, token, ticket_id, note)
    except httpx.HTTPError as exc:
        sys.exit(f"  WRITE FAILED (transport error, no scope signal): {exc}")

    print(f"\n  HTTP {resp.status_code}")
    print("  body:", resp.text[:2000])

    if resp.is_success:
        print(
            f"\nRESULT: WRITE SUCCEEDED. The client HAS write access.\n"
            f"  ACTION: an internal test note was added to ticket {ticket_id}. "
            f"Consider deleting it manually in Zendesk to keep the ticket clean."
        )
    elif resp.status_code == 403:
        print(
            "\nRESULT: 403 Forbidden on the write — SCOPE CONFIRMED READ-ONLY. "
            "The client can read but cannot write, even though a token was issued."
        )
    elif resp.status_code == 422:
        print(
            "\nRESULT: 422 Unprocessable — the write was permitted by scope but "
            "rejected for a ticket-state reason (e.g. the ticket became "
            "immutable). This is NOT a scope denial; re-run against a different "
            f"'--status' to get a clean write. Ticket tried: {ticket_id}."
        )
    else:
        print(
            f"\nRESULT: unexpected HTTP {resp.status_code} — inspect the body "
            f"above. Ticket tried: {ticket_id}."
        )


if __name__ == "__main__":
    main()
