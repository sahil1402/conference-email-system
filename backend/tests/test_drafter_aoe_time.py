"""AoE current-time injection into the drafter prompts.

The model has no clock, so the drafter states "now" in Anywhere-on-Earth
(UTC-12) — the frame AAAI deadlines use. These tests pin the conversion, both
injection points (system + user prompt), and that a single label reaches both
messages through ``draft()``. When no time is supplied the prompts are
byte-for-byte the pre-clock output (guarded here so the baseline never drifts).
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

from app.pipeline import drafter as drafter_module
from app.pipeline.drafter import (
    ResponseDrafter,
    _build_user_prompt,
    _now_aoe_label,
    _system_prompt,
)

FIXED_LABEL = "2026-07-28 20:00 AoE (UTC-12)"


def _clf():
    return SimpleNamespace(intent="submission_requirements", confidence=0.8)


def _email():
    return {"from": "author@university.edu", "subject": "Deadline?", "body": "When?"}


# ---------------------------------------------------------------------------
# Conversion: UTC -> AoE (UTC-12), including the day-boundary roll-back.
# ---------------------------------------------------------------------------
def test_now_aoe_label_subtracts_twelve_hours():
    # 08:00 UTC is still the previous calendar day at 20:00 in AoE.
    utc = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
    assert _now_aoe_label(utc) == "2026-07-28 20:00 AoE (UTC-12)"


def test_now_aoe_label_noon_utc_is_midnight_aoe():
    # Noon UTC is exactly midnight AoE — same calendar day begins.
    utc = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    assert _now_aoe_label(utc) == "2026-07-29 00:00 AoE (UTC-12)"


def test_now_aoe_label_no_arg_is_current_wall_clock():
    # Smoke: no arg computes from the real clock, well-formed and labelled.
    label = _now_aoe_label()
    assert label.endswith(" AoE (UTC-12)")
    # Parses back as a datetime (proves the strftime shape).
    datetime.strptime(label.replace(" AoE (UTC-12)", ""), "%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# System prompt injection.
# ---------------------------------------------------------------------------
def test_system_prompt_leads_with_authoritative_time():
    sp = _system_prompt(FIXED_LABEL)
    assert sp.startswith(f"The current date and time is {FIXED_LABEL}.")
    assert "ACTUAL current" in sp
    assert "authoritative" in sp
    # The grounding rules survive after the clock line.
    assert "professional assistant to a conference program chair" in sp


def test_system_prompt_without_time_is_unchanged():
    assert _system_prompt() == _system_prompt(None)
    assert "current date and time" not in _system_prompt()


# ---------------------------------------------------------------------------
# User prompt injection.
# ---------------------------------------------------------------------------
def test_user_prompt_prepends_current_time_block():
    p = _build_user_prompt(_email(), _clf(), [], None, now_aoe=FIXED_LABEL)
    assert p.startswith("--- CURRENT TIME ---\n")
    assert FIXED_LABEL in p
    assert "ACTUAL current date and time" in p
    assert "authoritative" in p
    # The time block precedes the email block.
    assert p.index("--- CURRENT TIME ---") < p.index("--- ORIGINAL EMAIL ---")


def test_user_prompt_without_time_has_no_block():
    p = _build_user_prompt(_email(), _clf(), [])
    assert "--- CURRENT TIME ---" not in p
    assert p.startswith("--- ORIGINAL EMAIL ---")


# ---------------------------------------------------------------------------
# End-to-end: draft() threads ONE label into BOTH messages (local path).
# ---------------------------------------------------------------------------
class _CapturingClient:
    """OpenAI-style OK client that records the last POST payload."""

    last_json: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *args, **kwargs):
        _CapturingClient.last_json = kwargs["json"]
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "message": {
                            "content": "=== REPLY ===\nHello.\n"
                            "=== CITATIONS ===\nnone\n"
                            "=== NOTES FOR CHAIR ===\nnone"
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        )


async def test_draft_injects_same_aoe_label_into_both_messages(monkeypatch):
    monkeypatch.setattr(drafter_module, "_now_aoe_label", lambda: FIXED_LABEL)
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _CapturingClient)

    await ResponseDrafter(provider="local").draft(_email(), _clf(), [])

    messages = _CapturingClient.last_json["messages"]
    system_content = messages[0]["content"]
    user_content = messages[1]["content"]
    # Both messages carry the identical authoritative time.
    assert FIXED_LABEL in system_content
    assert FIXED_LABEL in user_content
    assert system_content.startswith("The current date and time is")
    assert user_content.startswith("--- CURRENT TIME ---")
