"""READ-ONLY discovery: find internal notes that should have been public replies.

Incident context
----------------
An agent's Zendesk composer was left toggled to "internal note" mode, so replies
that were meant for requesters were posted as ``public: false`` comments —
written into the ticket, but invisible to the person waiting for an answer. This
script finds every such comment inside a recent time window so a human can
review and confirm the list *before* anything is republished.

What it does NOT do
-------------------
**This script never writes to Zendesk.** It issues ``GET`` requests only; the
HTTP client is installed with an event hook that raises
:class:`WriteAttemptError` on any non-GET request (see :data:`READ_ONLY` and
:func:`_reject_non_get`), so an accidental write cannot leave the process. It
also does not touch ConfMail's own database, classifier, retriever or drafter —
it is a standalone diagnostic that reuses only the Zendesk credential provider.

Turning the findings into real public replies is a *separate*, deliberately
unbuilt script, gated on a human reviewing this report first.

Two-phase safety checkpoint
---------------------------
Resolving "who am I" is not optional and cannot be skipped: the first run only
calls ``GET /api/v2/users/me.json``, prints the identity, and stops. It searches
nothing. To proceed you must re-run passing that user id back explicitly::

    # phase 1 — whoami only, no ticket search
    python scripts/recovery/find_internal_notes.py

    # phase 2 — after confirming the printed identity is the right person
    python scripts/recovery/find_internal_notes.py --confirm-author-id 12345678

If the id passed does not match the id the API resolves, the script aborts
without searching. This makes the checkpoint an affirmative act on a value that
had to be read off the phase-1 output, rather than a prompt that can be
reflexively dismissed.

Ticket enumeration
------------------
Uses the **incremental cursor export**
(``GET /api/v2/incremental/tickets/cursor.json?start_time=…``), the pattern
established in ``docs/ZENDESK_API.md`` §2 and used by the production poller. The
Search API was rejected for this job: its results lag indexing (a ticket updated
minutes ago can be missing — unacceptable when we are trying to find *every*
affected comment) and it hard-caps at 1,000 results. The incremental export has
neither problem, includes archived/closed tickets, and is purpose-built for
"everything changed since T".

Output
------
Both reports are written to ``scripts/recovery/output/`` (gitignored — they quote
real ticket content):

* ``internal_notes_<stamp>.md``   — human-readable, grouped by ticket
* ``internal_notes_<stamp>.json`` — machine-readable, so a future republish
  script can consume the findings without re-scraping Zendesk

Usage
-----
::

    cd backend
    python scripts/recovery/find_internal_notes.py                      # whoami, then stop
    python scripts/recovery/find_internal_notes.py --confirm-author-id 123 --days 4
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx

# ---------------------------------------------------------------------------
# Read-only contract
# ---------------------------------------------------------------------------
# This module is a diagnostic. It is read-only by construction, not by
# convention: every Zendesk call goes through ReadOnlyZendeskClient.get(), and
# the underlying httpx client rejects any non-GET request before it is sent.
# Nothing here may ever POST/PUT/PATCH/DELETE a Zendesk endpoint. If you need to
# write a fix, write a NEW, clearly-named script — do not relax this flag.
READ_ONLY = True

# Endpoints this script is allowed to touch, for the audit trail in the report.
ME_PATH = "/users/me.json"
INCREMENTAL_PATH = "/incremental/tickets/cursor.json"


def _comments_path(ticket_id: int | str) -> str:
    return f"/tickets/{ticket_id}/comments.json"


# Incremental exports are capped at 10 req/min (ZENDESK_API.md §7) — stay under.
INCREMENTAL_SLEEP_SECONDS = 6.5
# Per-ticket comment fetches share the account-wide budget; a small courtesy gap
# keeps a few-hundred-ticket sweep far below any documented limit.
COMMENT_SLEEP_SECONDS = 0.15
DEFAULT_RETRY_AFTER_SECONDS = 30
MAX_ATTEMPTS_PER_REQUEST = 6

DEFAULT_WINDOW_DAYS = 4
DEFAULT_MAX_PAGES = 50
PREVIEW_CHARS = 200

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# Zendesk marks deleted tickets with this status; they are never actionable.
DELETED_STATUS = "deleted"


class WriteAttemptError(RuntimeError):
    """Raised if anything in this script attempts a non-GET Zendesk request.

    A guard rail, not an expected error path: reaching it means the read-only
    contract at the top of this module was violated by a code change.
    """


def _reject_non_get(request: httpx.Request) -> None:
    """httpx request hook: allow GET only, and fail loudly on anything else."""
    if request.method.upper() != "GET":
        raise WriteAttemptError(
            f"find_internal_notes.py is READ-ONLY but attempted "
            f"{request.method} {request.url}. Refusing to send it."
        )


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    """The authenticated Zendesk user, as resolved by ``GET /users/me.json``."""

    id: int
    name: str | None
    email: str | None
    role: str | None


@dataclass(frozen=True)
class Finding:
    """One internal-note comment that may have been meant as a public reply."""

    ticket_id: int
    ticket_subject: str | None
    ticket_status: str | None
    ticket_url: str
    comment_id: int | None
    comment_type: str | None
    created_at: str | None
    author_id: int | None
    public: bool
    via_channel: str | None
    preview: str


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class ReadOnlyZendeskClient:
    """A GET-only Zendesk client wrapping the shared credential provider.

    Auth is delegated entirely to
    ``app.integrations.zendesk.credential_provider`` — this script holds no
    credential logic of its own. The OAuth provider performs its own token
    exchange on its own httpx client; the data client created here is
    GET-only, so no Zendesk data endpoint can be written through it.
    """

    def __init__(self, provider: Any, *, client: httpx.Client | None = None) -> None:
        self._provider = provider
        self._client = client or httpx.Client(
            timeout=60, event_hooks={"request": [_reject_non_get]}
        )

    @property
    def base_url(self) -> str:
        return self._provider.base_url

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET ``{base_url}{path}`` with 401 refresh, 429 backoff and 5xx retry."""
        url = f"{self.base_url}{path}"
        last_status: int | None = None
        for attempt in range(MAX_ATTEMPTS_PER_REQUEST):
            resp = self._client.get(
                url, params=params, headers=self._provider.get_auth_header()
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
            f"GET {path} failed after {MAX_ATTEMPTS_PER_REQUEST} attempts "
            f"(last status {last_status})."
        )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def parse_zendesk_datetime(value: str | None) -> datetime | None:
    """Parse a Zendesk ISO-8601 timestamp into an aware UTC datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def plain_preview(comment: dict, limit: int = PREVIEW_CHARS) -> str:
    """A short single-line plain-text preview of a comment body.

    Prefers Zendesk's read-only ``plain_body``, then ``body``, then strips tags
    out of ``html_body`` as a last resort. Whitespace is collapsed so a preview
    is always one skimmable line.
    """
    raw = comment.get("plain_body") or comment.get("body") or ""
    if not raw:
        html_body = comment.get("html_body") or ""
        raw = _TAG_RE.sub(" ", html_body)
    text = _WS_RE.sub(" ", str(raw)).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def agent_ticket_url(base_url: str, ticket_id: int | str) -> str:
    """Build the Zendesk **agent-UI** deep link for a ticket.

    Derived from the credential provider's ``base_url``
    (``https://{subdomain}.zendesk.com/api/v2``) so the account subdomain stays
    single-sourced in config instead of being hardcoded here.
    """
    account_root = base_url.split("/api/")[0].rstrip("/")
    return f"{account_root}/agent/tickets/{ticket_id}"


def filter_internal_notes(
    comments: Iterable[dict],
    *,
    author_id: int,
    window_start: datetime,
    window_end: datetime,
) -> list[dict]:
    """Select comments authored by ``author_id``, non-public, inside the window.

    All three conditions must hold. ``public`` is matched strictly against
    ``False``: a comment whose ``public`` flag is absent or non-boolean is *not*
    reported, because these findings feed a republish decision and a comment we
    cannot positively confirm was private must not be queued for resending.
    """
    matches: list[dict] = []
    for comment in comments:
        if comment.get("author_id") != author_id:
            continue
        if comment.get("public") is not False:
            continue
        created = parse_zendesk_datetime(comment.get("created_at"))
        if created is None or not (window_start <= created <= window_end):
            continue
        matches.append(comment)
    return matches


def build_findings(
    ticket: dict, comments: Iterable[dict], *, base_url: str
) -> list[Finding]:
    """Turn matched comments on one ticket into report rows."""
    ticket_id = ticket.get("id")
    url = agent_ticket_url(base_url, ticket_id)
    return [
        Finding(
            ticket_id=ticket_id,
            ticket_subject=ticket.get("subject"),
            ticket_status=ticket.get("status"),
            ticket_url=url,
            comment_id=comment.get("id"),
            comment_type=comment.get("type"),
            created_at=comment.get("created_at"),
            author_id=comment.get("author_id"),
            public=False,
            via_channel=(comment.get("via") or {}).get("channel"),
            preview=plain_preview(comment),
        )
        for comment in comments
    ]


# ---------------------------------------------------------------------------
# Zendesk reads
# ---------------------------------------------------------------------------


def resolve_identity(client: ReadOnlyZendeskClient) -> Identity:
    """``GET /users/me.json`` → the authenticated agent's identity."""
    payload = client.get(ME_PATH)
    user = payload.get("user") or {}
    user_id = user.get("id")
    if not isinstance(user_id, int):
        raise RuntimeError(
            "Zendesk /users/me.json did not return a numeric user id; "
            "cannot establish whose comments to look for."
        )
    return Identity(
        id=user_id,
        name=user.get("name"),
        email=user.get("email"),
        role=user.get("role"),
    )


def fetch_updated_tickets(
    client: ReadOnlyZendeskClient,
    *,
    start_time: int,
    max_pages: int = DEFAULT_MAX_PAGES,
    sleep_seconds: float = INCREMENTAL_SLEEP_SECONDS,
) -> list[dict]:
    """Page the incremental cursor export for tickets changed since ``start_time``.

    Deleted tickets are dropped (never actionable). Pages until
    ``end_of_stream``, or until ``max_pages`` as a runaway guard — a truncated
    sweep is reported loudly rather than silently passing for complete.
    """
    tickets: list[dict] = []
    cursor: str | None = None
    truncated = True
    for page in range(1, max_pages + 1):
        params: dict[str, Any] = {"per_page": 1000}
        if cursor:
            params["cursor"] = cursor
        else:
            params["start_time"] = start_time
        data = client.get(INCREMENTAL_PATH, params=params)

        batch = [
            t for t in data.get("tickets", []) if t.get("status") != DELETED_STATUS
        ]
        tickets.extend(batch)
        print(
            f"  export page {page}: +{len(batch)} tickets (total {len(tickets)})",
            flush=True,
        )

        if data.get("end_of_stream"):
            truncated = False
            break
        cursor = data.get("after_cursor")
        if not cursor:
            truncated = False
            break
        if sleep_seconds:
            time.sleep(sleep_seconds)

    if truncated:
        print(
            f"  WARNING: stopped at the --max-pages limit ({max_pages}); "
            "the ticket sweep is INCOMPLETE. Re-run with a higher --max-pages.",
            flush=True,
        )
    return tickets


def fetch_comments(client: ReadOnlyZendeskClient, ticket_id: int | str) -> list[dict]:
    """``GET /tickets/{id}/comments.json`` → the ticket's comments, oldest first."""
    payload = client.get(
        _comments_path(ticket_id), params={"sort": "created_at", "page[size]": 100}
    )
    return payload.get("comments", [])


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def build_report(
    *,
    identity: Identity,
    findings: Sequence[Finding],
    window_start: datetime,
    window_end: datetime,
    days: int,
    base_url: str,
    tickets_scanned: int,
    comments_scanned: int,
    generated_at: datetime,
) -> dict:
    """Assemble the machine-readable report (the JSON file's exact content)."""
    ticket_ids = sorted({f.ticket_id for f in findings})
    return {
        "report": "zendesk_internal_notes_discovery",
        "schema_version": 1,
        "read_only": READ_ONLY,
        "generated_at_utc": _iso(generated_at),
        "account_base_url": base_url,
        "identity": asdict(identity),
        "window": {
            "days": days,
            "start_utc": _iso(window_start),
            "end_utc": _iso(window_end),
        },
        "stats": {
            "tickets_scanned": tickets_scanned,
            "comments_scanned": comments_scanned,
            "tickets_with_matches": len(ticket_ids),
            "matches": len(findings),
        },
        "affected_ticket_ids": ticket_ids,
        "findings": [asdict(f) for f in findings],
    }


def render_markdown(report: dict) -> str:
    """Render the human-readable report Marc skims before approving a fix."""
    identity = report["identity"]
    window = report["window"]
    stats = report["stats"]

    lines: list[str] = [
        "# Zendesk internal notes that may have been meant as public replies",
        "",
        f"- **Generated (UTC):** {report['generated_at_utc']}",
        f"- **Account:** {report['account_base_url']}",
        f"- **Agent:** {identity.get('name')} <{identity.get('email')}> "
        f"(id `{identity['id']}`, role `{identity.get('role')}`)",
        f"- **Window:** last {window['days']} day(s) — "
        f"{window['start_utc']} → {window['end_utc']}",
        f"- **Tickets scanned:** {stats['tickets_scanned']} "
        f"({stats['comments_scanned']} comments read)",
        f"- **Matches:** {stats['matches']} internal note(s) across "
        f"{stats['tickets_with_matches']} ticket(s)",
        "",
        "> Read-only discovery report. Nothing was written to Zendesk. Each entry "
        "below is a comment this agent posted with `public: false` — saved on the "
        "ticket but never delivered to the requester.",
        "",
    ]

    if not report["findings"]:
        lines += [
            "## No matches",
            "",
            "No non-public comments by this agent were found in the window. If that "
            "is unexpected, widen `--days`, or re-check that the identity above is "
            "the right person.",
            "",
        ]
        return "\n".join(lines)

    lines += ["---", ""]
    by_ticket: dict[int, list[dict]] = {}
    for finding in report["findings"]:
        by_ticket.setdefault(finding["ticket_id"], []).append(finding)

    for ticket_id, group in by_ticket.items():
        first = group[0]
        subject = first.get("ticket_subject") or "(no subject)"
        lines += [
            f"## Ticket #{ticket_id} — {subject}",
            "",
            f"- Status: `{first.get('ticket_status')}`",
            f"- Link: {first['ticket_url']}",
            f"- Internal notes in window: {len(group)}",
            "",
        ]
        for finding in group:
            lines += [
                f"### Comment `{finding.get('comment_id')}` — {finding.get('created_at')}",
                "",
                f"- Channel: `{finding.get('via_channel')}` · "
                f"type: `{finding.get('comment_type')}` · public: `false`",
                "",
                "> " + (finding["preview"] or "(empty body)"),
                "",
            ]
        lines += ["---", ""]

    lines += [
        "## Next step",
        "",
        "Review the list above. The republish/write-fix script is intentionally "
        "not built yet — it is gated on this report being confirmed first. The "
        "companion `.json` file carries the same findings in machine-readable "
        "form so that script will not need to re-scrape Zendesk.",
        "",
    ]
    return "\n".join(lines)


def write_reports(
    report: dict, *, output_dir: Path = OUTPUT_DIR, stamp: str | None = None
) -> tuple[Path, Path]:
    """Write the Markdown + JSON reports; return ``(markdown_path, json_path)``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or _stamp(report["generated_at_utc"])
    md_path = output_dir / f"internal_notes_{stamp}.md"
    json_path = output_dir / f"internal_notes_{stamp}.json"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return md_path, json_path


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(iso_value: str) -> str:
    return iso_value.replace("-", "").replace(":", "").replace("Z", "").replace("T", "_")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "READ-ONLY: find Zendesk internal notes that may have been meant as "
            "public replies. Run with no arguments first to resolve and confirm "
            "the acting identity."
        )
    )
    parser.add_argument(
        "--confirm-author-id",
        type=int,
        default=None,
        metavar="USER_ID",
        help=(
            "The Zendesk user id printed by a previous whoami-only run. Required "
            "to search tickets; must match the id the API resolves."
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help=(
            f"Size of the look-back window in days (default {DEFAULT_WINDOW_DAYS}, "
            "a safety margin beyond the ~2-day estimate)."
        ),
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"Incremental-export page cap (default {DEFAULT_MAX_PAGES}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Where to write the .md/.json reports (default scripts/recovery/output/).",
    )
    return parser


def _print_identity(identity: Identity) -> None:
    print("Resolved Zendesk identity (GET /api/v2/users/me.json):")
    print(f"  id    : {identity.id}")
    print(f"  name  : {identity.name}")
    print(f"  email : {identity.email}")
    print(f"  role  : {identity.role}")


def main(
    argv: Sequence[str] | None = None,
    *,
    client: ReadOnlyZendeskClient | None = None,
    now: datetime | None = None,
) -> int:
    """Run the discovery sweep. Returns a process exit code.

    ``client`` and ``now`` are injection seams for tests; in normal operation
    both are built here.
    """
    assert READ_ONLY, (
        "find_internal_notes.py must stay read-only. Build a separate, "
        "clearly-named script for any Zendesk write."
    )
    args = _build_parser().parse_args(argv)

    if args.days < 1:
        print("--days must be at least 1.", file=sys.stderr)
        return 2

    if client is None:
        from app.core.config import settings
        from app.integrations.zendesk.credential_provider import (
            get_zendesk_credential_provider,
        )

        client = ReadOnlyZendeskClient(get_zendesk_credential_provider(settings))

    # ---- Safety checkpoint: resolve identity, and stop unless confirmed ----
    identity = resolve_identity(client)
    _print_identity(identity)

    if args.confirm_author_id is None:
        print()
        print("STOP — identity not confirmed, so no tickets were searched.")
        print("If the identity above is the right person, re-run with:")
        print(
            f"  python scripts/recovery/find_internal_notes.py "
            f"--confirm-author-id {identity.id} --days {args.days}"
        )
        return 0

    if args.confirm_author_id != identity.id:
        print()
        print(
            f"ABORT — --confirm-author-id {args.confirm_author_id} does not match "
            f"the resolved id {identity.id}. No tickets were searched.",
            file=sys.stderr,
        )
        return 3

    print(f"\nIdentity confirmed ({identity.id}). Starting read-only sweep.\n")

    # ---- Sweep ----
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    window_start = now - timedelta(days=args.days)
    window_end = now
    print(f"Window: {_iso(window_start)} → {_iso(window_end)} ({args.days}d)")

    tickets = fetch_updated_tickets(
        client,
        start_time=int(window_start.timestamp()),
        max_pages=args.max_pages,
    )
    print(f"Tickets updated in window: {len(tickets)}")

    findings: list[Finding] = []
    comments_scanned = 0
    for index, ticket in enumerate(tickets, start=1):
        ticket_id = ticket.get("id")
        if ticket_id is None:
            continue
        comments = fetch_comments(client, ticket_id)
        comments_scanned += len(comments)
        matches = filter_internal_notes(
            comments,
            author_id=identity.id,
            window_start=window_start,
            window_end=window_end,
        )
        if matches:
            findings.extend(
                build_findings(ticket, matches, base_url=client.base_url)
            )
            print(f"  [{index}/{len(tickets)}] #{ticket_id}: {len(matches)} match(es)")
        if COMMENT_SLEEP_SECONDS:
            time.sleep(COMMENT_SLEEP_SECONDS)

    report = build_report(
        identity=identity,
        findings=findings,
        window_start=window_start,
        window_end=window_end,
        days=args.days,
        base_url=client.base_url,
        tickets_scanned=len(tickets),
        comments_scanned=comments_scanned,
        generated_at=now,
    )
    md_path, json_path = write_reports(report, output_dir=args.output_dir)

    print()
    print(
        f"Found {len(findings)} internal note(s) across "
        f"{len(report['affected_ticket_ids'])} ticket(s)."
    )
    print(f"  Markdown : {md_path}")
    print(f"  JSON     : {json_path}")
    print("Nothing was written to Zendesk.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
