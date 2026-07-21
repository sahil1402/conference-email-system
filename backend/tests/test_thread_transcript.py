from datetime import datetime, timezone

from app.pipeline.thread_transcript import build_transcript


def _msg(cid, role, body, *, public=True, minute=0):
    return {
        "comment_id": cid,
        "public": public,
        "author_role": role,
        "plain_body": body,
        "created_at": datetime(2026, 7, 21, 10, minute, tzinfo=timezone.utc),
    }


def test_orders_oldest_to_newest_and_labels_roles():
    msgs = [
        _msg(1, "end-user", "How do I submit?", minute=0),
        _msg(2, "agent", "Use the portal.", minute=1),
        _msg(3, "end-user", "It errors out.", minute=2),
    ]
    t = build_transcript(msgs, char_budget=16000)
    assert t.text.index("How do I submit?") < t.text.index("Use the portal.")
    assert t.text.index("Use the portal.") < t.text.index("It errors out.")
    assert "Requester: How do I submit?" in t.text
    assert "Support: Use the portal." in t.text


def test_internal_notes_excluded():
    msgs = [
        _msg(1, "end-user", "Public question.", minute=0),
        _msg(2, "agent", "Staff-only note.", public=False, minute=1),
    ]
    t = build_transcript(msgs, char_budget=16000)
    assert "Public question." in t.text
    assert "Staff-only note." not in t.text
    assert t.included == 1


def test_latest_requester_message_is_the_anchor():
    msgs = [
        _msg(1, "end-user", "First ask.", minute=0),
        _msg(2, "agent", "Answer.", minute=1),
        _msg(3, "end-user", "Follow-up ask.", minute=2),
    ]
    t = build_transcript(msgs, char_budget=16000)
    assert t.latest_requester_message == "Follow-up ask."


def test_recent_first_budget_drops_oldest_with_marker():
    msgs = [
        _msg(1, "end-user", "A" * 100, minute=0),
        _msg(2, "agent", "B" * 100, minute=1),
        _msg(3, "end-user", "C" * 100, minute=2),
    ]
    # Budget only fits the newest turn or two.
    t = build_transcript(msgs, char_budget=130)
    assert "omitted" in t.text
    assert t.omitted >= 1
    # The latest requester turn is always present.
    assert "C" * 100 in t.text
    assert t.latest_requester_message == "C" * 100


def test_latest_turn_never_dropped_even_if_it_alone_exceeds_budget():
    msgs = [_msg(1, "end-user", "Z" * 500, minute=0)]
    t = build_transcript(msgs, char_budget=100)
    assert t.included == 1
    assert t.text  # non-empty (truncated head of the latest turn)


def test_empty_and_blank_messages_yield_empty_transcript():
    assert build_transcript([], char_budget=16000).text == ""
    blank = [_msg(1, "end-user", "   ", minute=0)]
    assert build_transcript(blank, char_budget=16000).text == ""
