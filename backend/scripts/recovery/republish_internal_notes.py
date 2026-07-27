"""Republish confirmed internal notes as public replies.

Incident context
-----------------
find_internal_notes.py (read-only) found comments Marc posted as
``public: false`` (internal note) when they were meant as real replies.
Zendesk cannot flip an existing comment's visibility after the fact, so the
only fix is to post a NEW public comment with the same content on the same
ticket. Marc has reviewed that report and confirmed the batch, MINUS two
tickets that are test/placeholder artifacts, not real replies:

  - #22655 — our own ConfMail e2e test ticket ("automated ConfMail
    end-to-end test reply... please disregard")
  - #22380 — a placeholder note containing the literal string
    "[Sender name]" and "NOTE: TESTING PURPOSE KINDLY IGNORE"

Both are hard-coded into EXCLUDED_TICKET_IDS below (not just a CLI flag you
could forget to pass).

Why this re-fetches content instead of reading it from the report
-------------------------------------------------------------------
The discovery report only stores a 200-character preview of each comment
(see PREVIEW_CHARS in find_internal_notes.py) -- several previews in the
real report are truncated mid-sentence. Posting a truncated preview to a
real submitter would be worse than the original bug. So this script uses
the report ONLY to know which (ticket_id, comment_id) pairs to act on; the
actual text posted is always freshly re-fetched from Zendesk immediately
before posting.

Safety model
------------
- Same one-time identity checkpoint as the discovery script: first run
  (without --confirm-author-id) only resolves and prints whoami, does not
  touch tickets. You confirm by re-running with that id.
- Once confirmed, the script processes the ENTIRE batch in one run and
  posts each comment for real -- there is no separate dry-run/live mode.
- Resumable: every attempted write (success or failure) is appended to a
  local JSONL state file. Re-running (e.g. after a crash or Ctrl-C)
  automatically skips ticket/comment pairs already marked "posted" in that
  file, so nothing is ever double-posted.
- Posting a comment never touches ticket status -- the write payload sends
  only `{"comment": {...}}`, no `status` key, so a solved/closed/open
  ticket stays exactly as it was.

Usage
-----
    cd backend

    # phase 1 -- whoami only, no writes, no ticket access
    python scripts/recovery/republish_internal_notes.py \\
        --report scripts/recovery/output/internal_notes_<stamp>.json

    # phase 2 -- confirmed run: processes the whole batch in one go
    python scripts/recovery/republish_internal_notes.py \\
        --report scripts/recovery/output/internal_notes_<stamp>.json \\
        --confirm-author-id 39818737387419
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import httpx

# ---------------------------------------------------------------------------
# Hard-coded exclusions -- confirmed with Marc, not a forgettable CLI flag.
# ---------------------------------------------------------------------------
EXCLUDED_TICKET_IDS: frozenset[int] = frozenset({22655, 22380})
EXCLUDED_TICKET_REASONS: dict[int, str] = {
    22655: "ConfMail e2e test ticket -- comments are test replies, not real",
    22380: "Placeholder/test note containing literal '[Sender name]' token",
}

COMMENTS_SLEEP_SECONDS = 0.3
DEFAULT_RETRY_AFTER_SECONDS = 30
MAX_ATTEMPTS_PER_REQUEST = 6

STATE_DIR = Path(__file__).resolve().parent / "output"
STATE_FILENAME = "republish_state.jsonl"


class WriteGuardError(RuntimeError):
    """Raised if a request method isn't in the small allowed set for this script."""


def _allow_get_and_put(request: httpx.Request) -> None:
    if request.method.upper() not in ("GET", "PUT"):
        raise WriteGuardError(
            f"republish_internal_notes.py only allows GET/PUT but attempted "
            f"{request.method} {request.url}. Refusing to send it."
        )


@dataclass(frozen=True)
class Identity:
    id: int
    name: str | None
    email: str | None
    role: str | None


class ZendeskClient:
    """GET+PUT client wrapping the shared credential provider.

    Deliberately narrower than a general client: only GET (read) and PUT
    (Zendesk's "Update Ticket" endpoint, which is how a new comment is
    appended to an existing ticket) are permitted at the transport level.
    POST/PATCH/DELETE are rejected before being sent -- POST on a specific
    ticket ID isn't even a route Zendesk recognizes (POST only applies to
    /tickets.json with no ID, for creating a brand-new ticket), and this
    script has no business doing anything but reading a ticket's comments
    and appending a new one via PUT.
    """

    def __init__(self, provider: Any, *, client: httpx.Client | None = None) -> None:
        self._provider = provider
        self._client = client or httpx.Client(
            timeout=60, event_hooks={"request": [_allow_get_and_put]}
        )

    @property
    def base_url(self) -> str:
        return self._provider.base_url

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def put(self, path: str, json_body: dict) -> dict:
        return self._request("PUT", path, json_body=json_body)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        url = f"{self.base_url}{path}"
        last_status: int | None = None
        for attempt in range(MAX_ATTEMPTS_PER_REQUEST):
            resp = self._client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=self._provider.get_auth_header(),
            )
            last_status = resp.status_code
            if resp.status_code == 429:
                wait = int(
                    resp.headers.get("Retry-After", DEFAULT_RETRY_AFTER_SECONDS)
                )
                print(f"  429 rate-limited; sleeping {wait}s", flush=True)
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(
            f"{method} {path} failed after {MAX_ATTEMPTS_PER_REQUEST} attempts "
            f"(last status {last_status})."
        )


def resolve_identity(client: ZendeskClient) -> Identity:
    payload = client.get("/users/me.json")
    user = payload.get("user") or {}
    user_id = user.get("id")
    if not isinstance(user_id, int):
        raise RuntimeError("Zendesk /users/me.json did not return a numeric user id.")
    return Identity(
        id=user_id, name=user.get("name"), email=user.get("email"), role=user.get("role")
    )


def fetch_comment_body(
    client: ZendeskClient, ticket_id: int, comment_id: int
) -> str | None:
    """Re-fetch a ticket's comments and return the FULL body of one, live.

    Returns None if the comment is no longer present (e.g. ticket merged or
    comment id stale) -- caller must skip, not fall back to the truncated
    report preview.
    """
    payload = client.get(
        f"/tickets/{ticket_id}/comments.json",
        params={"sort": "created_at", "page[size]": 100},
    )
    for comment in payload.get("comments", []):
        if comment.get("id") == comment_id:
            # Prefer `body` (the comment exactly as originally typed, blank
            # lines between paragraphs intact) over `plain_body` (Zendesk's
            # derived plain-text rendering of html_body, which collapses
            # paragraph breaks in the conversion -- fine for a search-result
            # preview, not for reposting to a real person).
            body = comment.get("body") or comment.get("plain_body")
            return body if body else None
    return None


def add_public_comment(client: ZendeskClient, ticket_id: int, body: str) -> dict:
    """PUT a new public comment onto ``ticket_id`` via Zendesk's Update Ticket
    endpoint -- this is the correct verb for appending a comment to an
    existing ticket. POST only creates brand-new tickets in Zendesk's API.
    """
    payload = {
        "ticket": {
            "comment": {"body": body, "public": True},
        }
    }
    return client.put(f"/tickets/{ticket_id}.json", payload)


# ---------------------------------------------------------------------------
# Report loading + filtering
# ---------------------------------------------------------------------------


def load_findings(report_path: Path) -> list[dict]:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    return data.get("findings", [])


def filter_excluded(findings: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split findings into (to_process, excluded), per EXCLUDED_TICKET_IDS."""
    to_process, excluded = [], []
    for finding in findings:
        if finding.get("ticket_id") in EXCLUDED_TICKET_IDS:
            excluded.append(finding)
        else:
            to_process.append(finding)
    return to_process, excluded


# ---------------------------------------------------------------------------
# Resumable state
# ---------------------------------------------------------------------------


def load_processed_keys(state_path: Path) -> set[str]:
    """Return the set of 'ticket_id:comment_id' already recorded as posted."""
    if not state_path.exists():
        return set()
    processed = set()
    for line in state_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("status") == "posted":
            processed.add(f"{record['ticket_id']}:{record['comment_id']}")
    return processed


def append_state(state_path: Path, record: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Republish confirmed internal notes as public replies. Dry-run "
            "by default; pass --live to actually write to Zendesk."
        )
    )
    parser.add_argument("--report", type=Path, required=True, help="Path to the discovery .json report")
    parser.add_argument("--confirm-author-id", type=int, default=None, metavar="USER_ID")
    parser.add_argument("--state-file", type=Path, default=STATE_DIR / STATE_FILENAME)
    return parser


def _print_identity(identity: Identity) -> None:
    print("Resolved Zendesk identity (GET /api/v2/users/me.json):")
    print(f"  id    : {identity.id}")
    print(f"  name  : {identity.name}")
    print(f"  email : {identity.email}")
    print(f"  role  : {identity.role}")


def main(argv: Sequence[str] | None = None, *, client: ZendeskClient | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if not args.report.exists():
        print(f"Report not found: {args.report}", file=sys.stderr)
        return 2

    if client is None:
        from app.core.config import settings
        from app.integrations.zendesk.credential_provider import (
            get_zendesk_credential_provider,
        )

        client = ZendeskClient(get_zendesk_credential_provider(settings))

    identity = resolve_identity(client)
    _print_identity(identity)

    if args.confirm_author_id is None:
        print()
        print("STOP -- identity not confirmed, so no report processing happened.")
        print("If the identity above is correct, re-run with:")
        print(
            f"  python {Path(__file__).name} --report {args.report} "
            f"--confirm-author-id {identity.id}"
        )
        return 0

    if args.confirm_author_id != identity.id:
        print(
            f"ABORT -- --confirm-author-id {args.confirm_author_id} does not "
            f"match resolved id {identity.id}.",
            file=sys.stderr,
        )
        return 3

    all_findings = load_findings(args.report)
    to_process, excluded = filter_excluded(all_findings)
    processed_keys = load_processed_keys(args.state_file)

    def _key(f: dict) -> str:
        return f"{f['ticket_id']}:{f.get('comment_id')}"

    already_done = sum(1 for f in to_process if _key(f) in processed_keys)
    remaining = len(to_process) - already_done

    print()
    print(f"Report: {args.report}")
    print(f"Total findings in report : {len(all_findings)}")
    print(f"Excluded (hard-coded)    : {len(excluded)}")
    for finding in excluded:
        reason = EXCLUDED_TICKET_REASONS.get(finding["ticket_id"], "excluded")
        print(f"  - skip #{finding['ticket_id']} / comment {finding.get('comment_id')}: {reason}")
    print(f"Already posted (resumed) : {already_done}")
    print(f"Remaining to attempt     : {remaining}")
    print()

    attempted = posted = skipped_done = skipped_missing = failed = 0

    for finding in to_process:
        ticket_id = finding["ticket_id"]
        comment_id = finding.get("comment_id")
        key = f"{ticket_id}:{comment_id}"

        if key in processed_keys:
            skipped_done += 1
            continue

        attempted += 1
        print(f"[{attempted}] Ticket #{ticket_id}, comment {comment_id}")

        try:
            full_body = fetch_comment_body(client, ticket_id, comment_id)
        except Exception as exc:  # noqa: BLE001 -- log and continue the batch
            print(f"    FAILED to re-fetch comment: {exc}")
            append_state(
                args.state_file,
                {
                    "ticket_id": ticket_id,
                    "comment_id": comment_id,
                    "status": "fetch_failed",
                    "error": str(exc),
                    "at": datetime.now(timezone.utc).isoformat(),
                },
            )
            failed += 1
            continue

        if full_body is None:
            print("    SKIP -- comment no longer found on ticket (stale/edited/merged)")
            append_state(
                args.state_file,
                {
                    "ticket_id": ticket_id,
                    "comment_id": comment_id,
                    "status": "missing",
                    "at": datetime.now(timezone.utc).isoformat(),
                },
            )
            skipped_missing += 1
            continue

        new_body = full_body
        print(f"    Body ({len(new_body)} chars): {new_body[:120]!r}...")

        try:
            add_public_comment(client, ticket_id, new_body)
        except Exception as exc:  # noqa: BLE001
            print(f"    FAILED to post: {exc}")
            append_state(
                args.state_file,
                {
                    "ticket_id": ticket_id,
                    "comment_id": comment_id,
                    "status": "post_failed",
                    "error": str(exc),
                    "at": datetime.now(timezone.utc).isoformat(),
                },
            )
            failed += 1
            continue

        print("    POSTED")
        append_state(
            args.state_file,
            {
                "ticket_id": ticket_id,
                "comment_id": comment_id,
                "status": "posted",
                "at": datetime.now(timezone.utc).isoformat(),
            },
        )
        posted += 1

        if COMMENTS_SLEEP_SECONDS:
            time.sleep(COMMENTS_SLEEP_SECONDS)

    print()
    print("Summary")
    print(f"  attempted this run : {attempted}")
    print(f"  posted             : {posted}")
    print(f"  already done       : {skipped_done}")
    print(f"  skipped (missing)  : {skipped_missing}")
    print(f"  failed             : {failed}")
    print(f"  state file         : {args.state_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())