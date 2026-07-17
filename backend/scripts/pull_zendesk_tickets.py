"""Pull the full AAAI Zendesk ticket corpus into data/tickets/ (research tool).

Two phases, both cursor/time-based incremental exports (the only endpoints that
include archived/closed tickets):

1. Tickets:  GET /api/v2/incremental/tickets/cursor.json  (+ users side-load)
   -> data/tickets/tickets.jsonl   (one ticket per line)
   -> data/tickets/users.jsonl    (deduplicated side-loaded users)
2. Comments: GET /api/v2/incremental/ticket_events.json?include=comment_events
   -> data/tickets/comment_events.jsonl (one comment event per line, with
      ticket_id, author_id, public flag, and body — the full thread text)

Auth is an OAuth client_credentials grant (client "confmail" registered on
aaai.zendesk.com, read scope); the secret is read from docs/secrets.txt, never
from source. Tokens expire after 30 min and are refreshed proactively.

Incremental endpoints are limited to 10 requests/min, so the script sleeps
between pages and honors Retry-After on 429. Progress cursors are checkpointed
to data/tickets/state.json after every page, so a rerun resumes where it left
off instead of restarting.

Usage:
    python scripts/pull_zendesk_tickets.py            # estimate + full pull
    python scripts/pull_zendesk_tickets.py --estimate # counts only, no pull
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRETS_PATH = REPO_ROOT / "docs" / "secrets.txt"
OUT_DIR = REPO_ROOT / "data" / "tickets"
STATE_PATH = OUT_DIR / "state.json"

BASE = "https://aaai.zendesk.com"
CLIENT_ID = "confmail"
# Incremental export endpoints allow 10 req/min; stay just under it.
PAGE_SLEEP_SECONDS = 6.5
# Refresh the OAuth token well before its 1800 s expiry.
TOKEN_LIFETIME_SLACK = 1500


def read_secret() -> str:
    for line in SECRETS_PATH.read_text().splitlines():
        line = line.strip()
        if line.startswith("secret:"):
            return line.split(":", 1)[1].strip()
    sys.exit(f"No 'secret:' line found in {SECRETS_PATH}")


class ZendeskClient:
    """Minimal read-only client: OAuth refresh + rate-limit-aware GET."""

    def __init__(self, secret: str) -> None:
        self._secret = secret
        self._client = httpx.Client(timeout=60)
        self._token: str | None = None
        self._token_born = 0.0

    def _ensure_token(self) -> None:
        if self._token and (time.monotonic() - self._token_born) < TOKEN_LIFETIME_SLACK:
            return
        resp = self._client.post(
            f"{BASE}/oauth/tokens",
            json={
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": self._secret,
                "scope": "read",
            },
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        self._token_born = time.monotonic()

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET with token refresh, 429 backoff, and transient-5xx retries."""
        for attempt in range(6):
            self._ensure_token()
            resp = self._client.get(
                f"{BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self._token}"},
            )
            if resp.status_code == 401:  # token revoked early — force refresh
                self._token = None
                continue
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "30"))
                print(f"  429 rate-limited; sleeping {wait}s", flush=True)
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"GET {path} failed after retries")


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def append_jsonl(path: Path, records: list[dict]) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def print_estimate(zd: ZendeskClient) -> None:
    total = zd.get("/api/v2/tickets/count.json")["count"]["value"]
    print(f"Non-archived tickets (tickets/count): {total}")
    grand = 0
    for status in ["new", "open", "pending", "hold", "solved", "closed"]:
        n = zd.get(
            "/api/v2/search/count.json",
            params={"query": f"type:ticket status:{status}"},
        )["count"]
        grand += n
        print(f"  status:{status:8s} {n}")
    print(f"Estimated pullable tickets (all statuses incl. archived): {grand}")


def pull_tickets(zd: ZendeskClient, state: dict) -> None:
    """Phase 1: all tickets (+ side-loaded users) via cursor incremental export."""
    if state.get("tickets_done"):
        print("Phase 1 (tickets): already complete, skipping.")
        return
    tickets_path = OUT_DIR / "tickets.jsonl"
    users_path = OUT_DIR / "users.jsonl"
    seen_users: set[int] = set(state.get("seen_users", []))
    cursor = state.get("tickets_cursor")
    n_tickets = state.get("n_tickets", 0)
    page = state.get("tickets_page", 0)

    while True:
        params: dict = {"per_page": 1000, "include": "users"}
        if cursor:
            params["cursor"] = cursor
        else:
            params["start_time"] = 1  # epoch start: everything the account has
        data = zd.get("/api/v2/incremental/tickets/cursor.json", params=params)

        tickets = data.get("tickets", [])
        users = [u for u in data.get("users", []) if u["id"] not in seen_users]
        seen_users.update(u["id"] for u in users)
        append_jsonl(tickets_path, tickets)
        append_jsonl(users_path, users)

        n_tickets += len(tickets)
        page += 1
        cursor = data.get("after_cursor")
        state.update(
            tickets_cursor=cursor,
            n_tickets=n_tickets,
            tickets_page=page,
            seen_users=sorted(seen_users),
        )
        print(f"  page {page}: +{len(tickets)} tickets (total {n_tickets})", flush=True)

        if data.get("end_of_stream"):
            state["tickets_done"] = True
            save_state(state)
            break
        save_state(state)
        time.sleep(PAGE_SLEEP_SECONDS)

    print(f"Phase 1 done: {n_tickets} tickets -> {tickets_path}")


def pull_comment_events(zd: ZendeskClient, state: dict) -> None:
    """Phase 2: every comment across all tickets via ticket-events export."""
    if state.get("events_done"):
        print("Phase 2 (comment events): already complete, skipping.")
        return
    events_path = OUT_DIR / "comment_events.jsonl"
    start_time = state.get("events_start_time", 1)
    n_comments = state.get("n_comments", 0)
    page = state.get("events_page", 0)

    while True:
        data = zd.get(
            "/api/v2/incremental/ticket_events.json",
            params={"start_time": start_time, "include": "comment_events"},
        )
        batch: list[dict] = []
        for event in data.get("ticket_events", []):
            for child in event.get("child_events", []):
                if child.get("event_type") == "Comment":
                    batch.append(
                        {
                            "ticket_id": event.get("ticket_id"),
                            "created_at": event.get("created_at"),
                            "author_id": child.get("author_id"),
                            "public": child.get("public"),
                            "body": child.get("body"),
                            "via": (child.get("via") or {}).get("channel"),
                        }
                    )
        append_jsonl(events_path, batch)
        n_comments += len(batch)
        page += 1
        start_time = data.get("end_time", start_time)
        state.update(
            events_start_time=start_time, n_comments=n_comments, events_page=page
        )
        print(f"  page {page}: +{len(batch)} comments (total {n_comments})", flush=True)

        if data.get("end_of_stream"):
            state["events_done"] = True
            save_state(state)
            break
        save_state(state)
        time.sleep(PAGE_SLEEP_SECONDS)

    print(f"Phase 2 done: {n_comments} comments -> {events_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--estimate", action="store_true", help="Print ticket counts and exit."
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    zd = ZendeskClient(read_secret())

    print_estimate(zd)
    if args.estimate:
        return

    state = load_state()
    pull_tickets(zd, state)
    time.sleep(PAGE_SLEEP_SECONDS)  # shared 10/min budget across both endpoints
    pull_comment_events(zd, state)

    manifest = {
        "source": f"{BASE} (OAuth client {CLIENT_ID}, read-only)",
        "pulled_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tickets": state.get("n_tickets", 0),
        "comments": state.get("n_comments", 0),
        "files": ["tickets.jsonl", "users.jsonl", "comment_events.jsonl"],
        "note": "Real user PII — gitignored, do not commit or share.",
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("Manifest written:", OUT_DIR / "manifest.json")


if __name__ == "__main__":
    main()
