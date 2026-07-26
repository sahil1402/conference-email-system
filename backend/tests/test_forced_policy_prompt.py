"""Chair-selected policy marking in the drafter prompt (task 4).

A forced policy sits in the context like any other chunk, and the base task rule
("answer only the question(s) the requester raised — nothing more") would let the
model silently drop it. These tests pin the marker + its narrow instruction, and —
critically — that a draft with NO forced policy produces a byte-identical prompt
to the pre-task-4 baseline.
"""

import pytest

from app.pipeline.classifier import ClassificationResult
from app.pipeline.drafter import (
    _CHAIR_SELECTED_INSTRUCTION,
    _CHAIR_SELECTED_MARKER,
    ResponseDrafter,
    _build_user_prompt,
)
from app.pipeline.retriever import RetrievedChunk

# Snapshot of the prompt this code produced BEFORE task 4, captured from the
# pre-change implementation. Any drift in the no-forced-policy path fails here.
BASELINE_PROMPT = (
    "--- ORIGINAL EMAIL ---\n"
    "From: Author <a@b.com>\n"
    "Subject: Withdraw\n"
    "Body: Please withdraw.\n"
    "\n"
    "--- CLASSIFICATION ---\n"
    "Intent: submission_upload_help (confidence: 0.76)\n"
    "\n"
    "--- RETRIEVED POLICY CONTEXT ---\n"
    "[policy_186] Modification Guidelines\n"
    "After the deadline nothing changes.\n"
    "\n"
    "[policy_172] Abstract Submission\n"
    "Abstracts are due earlier.\n"
    "\n"
    "--- TASK ---\n"
    "Using only the policy context above for grounding, answer only the "
    "question(s) the requester raised — nothing more."
)

RANKED = [
    RetrievedChunk(policy_id="policy_186", title="Modification Guidelines",
                   content="After the deadline nothing changes.", score=0.9, category="sub"),
    RetrievedChunk(policy_id="policy_172", title="Abstract Submission",
                   content="Abstracts are due earlier.", score=0.8, category="sub"),
]
FORCED = RetrievedChunk(policy_id="int_paper-deletion__v2", title="Paper Deletion",
                        content="Withdrawal requires written confirmation.",
                        score=0.0, category="del")

CLS = ClassificationResult(intent="submission_upload_help", confidence=0.76,
                           reasoning="r", method="llm_distiller")
EMAIL = {"from": "a@b.com", "sender_name": "Author", "subject": "Withdraw",
         "body": "Please withdraw."}


# ---------------------------------------------------------------------------
# Regression: the default prompt must not move a single byte
# ---------------------------------------------------------------------------
def test_no_forced_policy_prompt_is_byte_identical_to_baseline():
    assert _build_user_prompt(EMAIL, CLS, RANKED) == BASELINE_PROMPT


def test_explicit_none_is_also_byte_identical():
    assert _build_user_prompt(EMAIL, CLS, RANKED, None) == BASELINE_PROMPT


def test_marker_and_instruction_absent_without_a_forced_policy():
    p = _build_user_prompt(EMAIL, CLS, RANKED)
    assert _CHAIR_SELECTED_MARKER not in p
    assert _CHAIR_SELECTED_INSTRUCTION.strip() not in p


def test_unresolved_forced_key_leaves_the_prompt_unchanged():
    """A key naming no present chunk must not emit a dangling marker/instruction."""
    p = _build_user_prompt(EMAIL, CLS, RANKED, "int_not_in_context")
    assert p == BASELINE_PROMPT


# ---------------------------------------------------------------------------
# The forced-policy prompt
# ---------------------------------------------------------------------------
def test_forced_chunk_is_marked_and_instruction_added():
    p = _build_user_prompt(EMAIL, CLS, [*RANKED, FORCED], "int_paper-deletion__v2")

    # The marker prefaces the forced block specifically.
    assert f"{_CHAIR_SELECTED_MARKER}\n[int_paper-deletion__v2] Paper Deletion" in p
    # Exactly one block is marked.
    assert p.count(_CHAIR_SELECTED_MARKER) == 2  # once in context, once in the task line
    assert p.count(f"{_CHAIR_SELECTED_MARKER}\n[") == 1
    # The instruction is appended to the existing task rule, not replacing it.
    assert "answer only the question(s) the requester raised — nothing more." in p
    # 4d: the instruction must demand BOTH parts. A real-LLM check showed that
    # merely saying "address the marked policy" made the model answer ONLY that
    # and drop the requester's question, so these clauses are load-bearing.
    assert "Do BOTH of the following" in p
    assert "fully answer the question(s) the requester actually raised" in p
    assert "SEPARATE closing paragraph" in p
    assert "ADDITION to your answer, never a replacement" in p


def test_normal_chunks_are_formatted_exactly_as_before():
    p = _build_user_prompt(EMAIL, CLS, [*RANKED, FORCED], "int_paper-deletion__v2")
    # Untouched, unmarked, same "[id] title\ncontent" shape.
    assert "[policy_186] Modification Guidelines\nAfter the deadline nothing changes." in p
    assert "[policy_172] Abstract Submission\nAbstracts are due earlier." in p
    assert f"{_CHAIR_SELECTED_MARKER}\n[policy_186]" not in p
    assert f"{_CHAIR_SELECTED_MARKER}\n[policy_172]" not in p


def test_forced_prompt_is_the_baseline_plus_only_the_additions():
    """Nothing else in the prompt shifts — the diff is exactly marker+instruction."""
    forced = _build_user_prompt(EMAIL, CLS, RANKED, "policy_186")  # force a ranked one
    # Same text as baseline once the two additions are removed.
    stripped = forced.replace(f"{_CHAIR_SELECTED_MARKER}\n", "").replace(
        _CHAIR_SELECTED_INSTRUCTION, ""
    )
    assert stripped == BASELINE_PROMPT


def test_marking_an_already_ranked_chunk_works():
    """Task 3 skips duplicate injection, so a forced key may name a RANKED chunk."""
    p = _build_user_prompt(EMAIL, CLS, RANKED, "policy_172")
    assert f"{_CHAIR_SELECTED_MARKER}\n[policy_172] Abstract Submission" in p
    assert f"{_CHAIR_SELECTED_MARKER}\n[policy_186]" not in p


# ---------------------------------------------------------------------------
# Through the real drafter (model call stubbed) — the prompt actually sent
# ---------------------------------------------------------------------------
@pytest.fixture
def capture_prompt(monkeypatch):
    """Run ResponseDrafter with the local provider and capture its user prompt."""
    seen = {}
    d = ResponseDrafter(provider="local")

    async def fake_local(self, user_prompt):
        seen["prompt"] = user_prompt
        from app.pipeline.drafter import DraftResponse
        return DraftResponse(draft_text="ok", citations=[], model_used="stub",
                             generation_metadata={})

    monkeypatch.setattr(ResponseDrafter, "_draft_local", fake_local)
    return d, seen


async def test_drafter_sends_the_marker_when_forced(capture_prompt):
    d, seen = capture_prompt
    await d.draft(EMAIL, CLS, [*RANKED, FORCED], "int_paper-deletion__v2")
    assert _CHAIR_SELECTED_MARKER in seen["prompt"]
    assert "Do BOTH of the following" in seen["prompt"]
    assert "ADDITION to your answer, never a replacement" in seen["prompt"]


async def test_drafter_prompt_unchanged_when_not_forced(capture_prompt):
    d, seen = capture_prompt
    await d.draft(EMAIL, CLS, RANKED)
    assert seen["prompt"] == BASELINE_PROMPT
    assert _CHAIR_SELECTED_MARKER not in seen["prompt"]
