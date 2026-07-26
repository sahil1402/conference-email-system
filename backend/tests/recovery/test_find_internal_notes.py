"""Tests for the read-only internal-note discovery script.

The Zendesk API is mocked end to end — nothing here touches the network, the
ConfMail database, or any pipeline module. Coverage focuses on the three things
that would make this script dangerous or useless if wrong:

1. **Filtering** — only ``public: false`` comments, only by the confirmed
   author, only inside the window.
2. **Report structure** — both the machine-readable JSON and the human-readable
   Markdown, since a future republish script consumes the former.
3. **The whoami checkpoint** — it must halt before any ticket search. This is
   asserted on the recorded call log, not just on the exit code, so the
   checkpoint cannot be silently skipped by a later refactor.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from scripts.recovery import find_internal_notes as fin

NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)
ME_ID = 12345678
OTHER_ID = 99999999
BASE_URL = "https://aaai.zendesk.com/api/v2"


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _comment(
    comment_id: int,
    *,
    author_id: int = ME_ID,
    public: bool | None = False,
    created_at: datetime | None = None,
    body: str = "Thanks for your note — the deadline is AoE.",
    **extra,
) -> dict:
    comment = {
        "id": comment_id,
        "type": "Comment",
        "author_id": author_id,
        "public": public,
        "plain_body": body,
        "created_at": _iso(created_at or (NOW - timedelta(days=1))),
        "via": {"channel": "web"},
    }
    comment.update(extra)
    return comment


class FakeZendeskClient:
    """Duck-typed stand-in for :class:`ReadOnlyZendeskClient`.

    Records every path requested so tests can assert on *which* endpoints were
    (and were not) reached.
    """

    def __init__(
        self,
        *,
        user: dict | None = None,
        pages: list[dict] | None = None,
        comments: dict[int, list[dict]] | None = None,
    ) -> None:
        self.base_url = BASE_URL
        self._user = user if user is not None else {
            "id": ME_ID,
            "name": "Marc Chair",
            "email": "marc@example.org",
            "role": "admin",
        }
        self._pages = pages or [{"tickets": [], "end_of_stream": True}]
        self._comments = comments or {}
        self.calls: list[tuple[str, dict | None]] = []
        self._page_index = 0

    @property
    def paths(self) -> list[str]:
        return [path for path, _ in self.calls]

    def get(self, path: str, params: dict | None = None) -> dict:
        self.calls.append((path, params))
        if path == fin.ME_PATH:
            return {"user": self._user}
        if path == fin.INCREMENTAL_PATH:
            page = self._pages[min(self._page_index, len(self._pages) - 1)]
            self._page_index += 1
            return page
        if path.startswith("/tickets/") and path.endswith("/comments.json"):
            ticket_id = int(path.split("/")[2])
            return {"comments": self._comments.get(ticket_id, [])}
        raise AssertionError(f"unexpected path requested: {path}")


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """Keep the suite fast: the script's rate-limit courtesy sleeps are real."""
    monkeypatch.setattr(fin.time, "sleep", lambda _seconds: None)


# ---------------------------------------------------------------------------
# 1. Filtering
# ---------------------------------------------------------------------------


class TestFilterInternalNotes:
    """``filter_internal_notes`` must match on all three criteria at once."""

    def _filter(self, comments):
        return fin.filter_internal_notes(
            comments,
            author_id=ME_ID,
            window_start=NOW - timedelta(days=4),
            window_end=NOW,
        )

    def test_matches_internal_note_by_author_in_window(self):
        matches = self._filter([_comment(1)])
        assert [c["id"] for c in matches] == [1]

    def test_public_comment_excluded(self):
        assert self._filter([_comment(1, public=True)]) == []

    def test_other_author_excluded(self):
        """A colleague's internal note is not this agent's mistake to republish."""
        assert self._filter([_comment(1, author_id=OTHER_ID)]) == []

    def test_comment_older_than_window_excluded(self):
        assert self._filter([_comment(1, created_at=NOW - timedelta(days=5))]) == []

    def test_comment_after_window_end_excluded(self):
        assert self._filter([_comment(1, created_at=NOW + timedelta(hours=1))]) == []

    def test_window_boundaries_are_inclusive(self):
        start = NOW - timedelta(days=4)
        matches = self._filter(
            [_comment(1, created_at=start), _comment(2, created_at=NOW)]
        )
        assert [c["id"] for c in matches] == [1, 2]

    @pytest.mark.parametrize("public_value", [None, "false", 0])
    def test_non_boolean_public_flag_is_not_reported(self, public_value):
        """Ambiguous privacy is excluded — these findings drive a republish."""
        assert self._filter([_comment(1, public=public_value)]) == []

    def test_missing_created_at_excluded(self):
        comment = _comment(1)
        comment["created_at"] = None
        assert self._filter([comment]) == []

    def test_mixed_batch_keeps_only_real_matches(self):
        comments = [
            _comment(1),  # match
            _comment(2, public=True),  # public reply
            _comment(3, author_id=OTHER_ID),  # someone else
            _comment(4, created_at=NOW - timedelta(days=10)),  # too old
            _comment(5, created_at=NOW - timedelta(hours=2)),  # match
        ]
        assert [c["id"] for c in self._filter(comments)] == [1, 5]


# ---------------------------------------------------------------------------
# 2. Report structure — both formats
# ---------------------------------------------------------------------------


def _sample_report(findings=None):
    identity = fin.Identity(
        id=ME_ID, name="Marc Chair", email="marc@example.org", role="admin"
    )
    ticket = {"id": 21567, "subject": "Supplementary deadline", "status": "open"}
    if findings is None:
        findings = fin.build_findings(
            ticket,
            [_comment(555, body="The supplementary deadline is Jan 15 AoE.")],
            base_url=BASE_URL,
        )
    return fin.build_report(
        identity=identity,
        findings=findings,
        window_start=NOW - timedelta(days=4),
        window_end=NOW,
        days=4,
        base_url=BASE_URL,
        tickets_scanned=7,
        comments_scanned=19,
        generated_at=NOW,
    )


class TestJsonReport:
    def test_top_level_shape(self):
        report = _sample_report()
        assert report["report"] == "zendesk_internal_notes_discovery"
        assert report["schema_version"] == 1
        assert report["read_only"] is True
        assert report["generated_at_utc"] == "2026-07-26T12:00:00Z"
        assert report["account_base_url"] == BASE_URL
        assert report["identity"] == {
            "id": ME_ID,
            "name": "Marc Chair",
            "email": "marc@example.org",
            "role": "admin",
        }
        assert report["window"] == {
            "days": 4,
            "start_utc": "2026-07-22T12:00:00Z",
            "end_utc": "2026-07-26T12:00:00Z",
        }
        assert report["stats"] == {
            "tickets_scanned": 7,
            "comments_scanned": 19,
            "tickets_with_matches": 1,
            "matches": 1,
        }
        assert report["affected_ticket_ids"] == [21567]

    def test_finding_carries_every_required_field(self):
        """A future republish script consumes these keys — keep them stable."""
        finding = _sample_report()["findings"][0]
        assert finding == {
            "ticket_id": 21567,
            "ticket_subject": "Supplementary deadline",
            "ticket_status": "open",
            "ticket_url": "https://aaai.zendesk.com/agent/tickets/21567",
            "comment_id": 555,
            "comment_type": "Comment",
            "created_at": _iso(NOW - timedelta(days=1)),
            "author_id": ME_ID,
            "public": False,
            "via_channel": "web",
            "preview": "The supplementary deadline is Jan 15 AoE.",
        }

    def test_report_is_json_serialisable(self):
        assert json.loads(json.dumps(_sample_report()))["stats"]["matches"] == 1


class TestMarkdownReport:
    def test_includes_identity_window_and_finding(self):
        markdown = fin.render_markdown(_sample_report())
        assert "# Zendesk internal notes" in markdown
        assert f"id `{ME_ID}`" in markdown
        assert "marc@example.org" in markdown
        assert "last 4 day(s)" in markdown
        assert "## Ticket #21567 — Supplementary deadline" in markdown
        assert "https://aaai.zendesk.com/agent/tickets/21567" in markdown
        assert "The supplementary deadline is Jan 15 AoE." in markdown

    def test_states_that_nothing_was_written(self):
        assert "Nothing was written to Zendesk" in fin.render_markdown(_sample_report())

    def test_empty_report_says_no_matches(self):
        markdown = fin.render_markdown(_sample_report(findings=[]))
        assert "## No matches" in markdown
        assert "## Ticket #" not in markdown

    def test_groups_multiple_comments_under_one_ticket(self):
        ticket = {"id": 42, "subject": "Two notes", "status": "pending"}
        findings = fin.build_findings(
            ticket, [_comment(1, body="first"), _comment(2, body="second")],
            base_url=BASE_URL,
        )
        markdown = fin.render_markdown(_sample_report(findings=findings))
        assert markdown.count("## Ticket #42") == 1
        assert "Internal notes in window: 2" in markdown
        assert "### Comment `1`" in markdown and "### Comment `2`" in markdown


class TestWriteReports:
    def test_writes_both_formats(self, tmp_path):
        report = _sample_report()
        md_path, json_path = fin.write_reports(report, output_dir=tmp_path)

        assert md_path.suffix == ".md" and json_path.suffix == ".json"
        assert md_path.parent == tmp_path and json_path.parent == tmp_path
        assert json.loads(json_path.read_text(encoding="utf-8")) == report
        assert "## Ticket #21567" in md_path.read_text(encoding="utf-8")

    def test_creates_output_dir_when_missing(self, tmp_path):
        target = tmp_path / "nested" / "output"
        md_path, _ = fin.write_reports(_sample_report(), output_dir=target)
        assert md_path.exists()


# ---------------------------------------------------------------------------
# 3. The whoami confirmation checkpoint
# ---------------------------------------------------------------------------


class TestWhoamiCheckpoint:
    """Without an explicit id confirmation, NO ticket endpoint may be touched."""

    def test_unconfirmed_run_resolves_identity_and_stops(self, tmp_path, capsys):
        client = FakeZendeskClient()
        exit_code = fin.main(
            ["--output-dir", str(tmp_path)], client=client, now=NOW
        )

        assert exit_code == 0
        # The checkpoint ran...
        assert client.paths == [fin.ME_PATH]
        # ...and nothing else did.
        assert fin.INCREMENTAL_PATH not in client.paths
        assert not any("/comments.json" in path for path in client.paths)
        assert list(tmp_path.iterdir()) == []

        out = capsys.readouterr().out
        assert "STOP — identity not confirmed" in out
        assert str(ME_ID) in out

    def test_mismatched_confirmation_aborts_before_searching(self, tmp_path, capsys):
        client = FakeZendeskClient()
        exit_code = fin.main(
            ["--confirm-author-id", str(OTHER_ID), "--output-dir", str(tmp_path)],
            client=client,
            now=NOW,
        )

        assert exit_code == 3
        assert client.paths == [fin.ME_PATH]
        assert list(tmp_path.iterdir()) == []
        assert "ABORT" in capsys.readouterr().err

    def test_confirmed_run_proceeds_to_search(self, tmp_path):
        client = FakeZendeskClient(
            pages=[{"tickets": [{"id": 7, "subject": "s", "status": "open"}],
                    "end_of_stream": True}],
            comments={7: [_comment(1)]},
        )
        exit_code = fin.main(
            ["--confirm-author-id", str(ME_ID), "--output-dir", str(tmp_path)],
            client=client,
            now=NOW,
        )

        assert exit_code == 0
        assert client.paths[0] == fin.ME_PATH
        assert fin.INCREMENTAL_PATH in client.paths
        assert "/tickets/7/comments.json" in client.paths

    def test_identity_without_numeric_id_raises(self):
        client = FakeZendeskClient(user={"name": "Nobody"})
        with pytest.raises(RuntimeError, match="numeric user id"):
            fin.resolve_identity(client)


# ---------------------------------------------------------------------------
# End-to-end sweep
# ---------------------------------------------------------------------------


class TestEndToEndSweep:
    def _client(self):
        tickets = [
            {"id": 100, "subject": "Has an internal note", "status": "open"},
            {"id": 200, "subject": "Only public replies", "status": "solved"},
            {"id": 300, "subject": "Deleted", "status": "deleted"},
        ]
        return FakeZendeskClient(
            pages=[{"tickets": tickets, "end_of_stream": True}],
            comments={
                100: [
                    _comment(11, public=True, body="original question",
                             author_id=OTHER_ID),
                    _comment(12, body="Yes, extensions are possible."),
                ],
                200: [_comment(21, public=True, body="a real reply")],
                300: [_comment(31, body="should never be read")],
            },
        )

    def test_report_contents_and_files(self, tmp_path):
        client = self._client()
        assert fin.main(
            ["--confirm-author-id", str(ME_ID), "--days", "4",
             "--output-dir", str(tmp_path)],
            client=client,
            now=NOW,
        ) == 0

        json_files = list(tmp_path.glob("*.json"))
        md_files = list(tmp_path.glob("*.md"))
        assert len(json_files) == 1 and len(md_files) == 1

        report = json.loads(json_files[0].read_text(encoding="utf-8"))
        assert report["affected_ticket_ids"] == [100]
        assert report["stats"]["matches"] == 1
        assert report["stats"]["tickets_scanned"] == 2  # deleted one dropped
        assert report["findings"][0]["comment_id"] == 12
        assert report["findings"][0]["ticket_url"].endswith("/agent/tickets/100")

    def test_deleted_tickets_are_never_fetched(self, tmp_path):
        client = self._client()
        fin.main(
            ["--confirm-author-id", str(ME_ID), "--output-dir", str(tmp_path)],
            client=client,
            now=NOW,
        )
        assert "/tickets/300/comments.json" not in client.paths

    def test_window_start_is_sent_as_epoch_seconds(self, tmp_path):
        client = self._client()
        fin.main(
            ["--confirm-author-id", str(ME_ID), "--days", "4",
             "--output-dir", str(tmp_path)],
            client=client,
            now=NOW,
        )
        export_params = next(
            params for path, params in client.calls if path == fin.INCREMENTAL_PATH
        )
        assert export_params["start_time"] == int(
            (NOW - timedelta(days=4)).timestamp()
        )

    def test_rejects_nonsense_window(self, tmp_path):
        client = FakeZendeskClient()
        assert fin.main(
            ["--days", "0", "--output-dir", str(tmp_path)], client=client, now=NOW
        ) == 2
        assert client.calls == []  # bailed before even the whoami call

    def test_paginates_until_end_of_stream(self, tmp_path):
        client = FakeZendeskClient(
            pages=[
                {"tickets": [{"id": 1, "subject": "a", "status": "open"}],
                 "after_cursor": "CURSOR2", "end_of_stream": False},
                {"tickets": [{"id": 2, "subject": "b", "status": "open"}],
                 "end_of_stream": True},
            ],
            comments={1: [_comment(1)], 2: [_comment(2)]},
        )
        fin.main(
            ["--confirm-author-id", str(ME_ID), "--output-dir", str(tmp_path)],
            client=client,
            now=NOW,
        )
        export_calls = [p for path, p in client.calls if path == fin.INCREMENTAL_PATH]
        assert len(export_calls) == 2
        assert export_calls[1]["cursor"] == "CURSOR2"


# ---------------------------------------------------------------------------
# Read-only guard + small helpers
# ---------------------------------------------------------------------------


class TestReadOnlyGuard:
    def test_module_declares_read_only(self):
        assert fin.READ_ONLY is True

    def test_non_get_request_is_rejected(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            request = httpx.Request(method, f"{BASE_URL}/tickets/1.json")
            with pytest.raises(fin.WriteAttemptError):
                fin._reject_non_get(request)

    def test_get_request_is_allowed(self):
        assert fin._reject_non_get(httpx.Request("GET", f"{BASE_URL}/tickets/1.json")) is None

    def test_real_client_installs_the_guard(self):
        class _Provider:
            base_url = BASE_URL

            def get_auth_header(self):
                return {"Authorization": "Bearer test"}

        client = fin.ReadOnlyZendeskClient(_Provider())
        with pytest.raises(fin.WriteAttemptError):
            client._client.post(f"{BASE_URL}/tickets/1.json", json={})

    def test_script_source_contains_no_write_verbs(self):
        """A structural guard: no POST/PUT/PATCH/DELETE call on the data client."""
        source = fin.__file__
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        for call in ("self._client.post(", "self._client.put(",
                     "self._client.patch(", "self._client.delete(",
                     "client.post(", "client.put("):
            assert call not in text, f"read-only script must not call {call}"


class TestHelpers:
    def test_agent_ticket_url_derives_subdomain_from_base_url(self):
        assert (
            fin.agent_ticket_url(BASE_URL, 21567)
            == "https://aaai.zendesk.com/agent/tickets/21567"
        )

    def test_preview_prefers_plain_body_and_collapses_whitespace(self):
        comment = {"plain_body": "line one\n\n  line two", "body": "ignored"}
        assert fin.plain_preview(comment) == "line one line two"

    def test_preview_falls_back_to_body_then_strips_html(self):
        assert fin.plain_preview({"body": "plain text"}) == "plain text"
        assert (
            fin.plain_preview({"html_body": "<p>hello <b>there</b></p>"})
            == "hello there"
        )

    def test_preview_truncates_with_ellipsis(self):
        preview = fin.plain_preview({"plain_body": "x" * 500})
        assert len(preview) == fin.PREVIEW_CHARS + 1
        assert preview.endswith("…")

    def test_preview_of_empty_body_is_empty_string(self):
        assert fin.plain_preview({}) == ""

    @pytest.mark.parametrize(
        "value,expected_hour",
        [("2026-07-26T12:00:00Z", 12), ("2026-07-26T08:00:00-04:00", 12)],
    )
    def test_datetime_parsing_normalises_to_utc(self, value, expected_hour):
        parsed = fin.parse_zendesk_datetime(value)
        assert parsed is not None
        assert parsed.tzinfo is not None
        assert parsed.hour == expected_hour

    @pytest.mark.parametrize("value", [None, "", "not-a-date"])
    def test_datetime_parsing_returns_none_on_bad_input(self, value):
        assert fin.parse_zendesk_datetime(value) is None
