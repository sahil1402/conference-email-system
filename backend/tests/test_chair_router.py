"""Unit tests for the chair router (Phase 6A, Step 2).

Pure strategy tests — no DB, no API. ``IntentMappingStrategy.assign`` takes a
classification plus a list of in-memory ``ChairInfo`` objects and returns a
``ChairAssignment``, so the whole decision is exercised with hand-built roster
fixtures. Covers: correct intent → chair match, no-match fallback to the
empty-areas chair, inactive-chair skip, deterministic tie-breaking, and the
factory / flag behavior.
"""

import pytest

from app.chair_router import (
    ChairInfo,
    ChairRoutingStrategy,
    IntentMappingStrategy,
    get_chair_router,
)
from app.pipeline.classifier import ClassificationResult


def _clf(intent: str, confidence: float = 0.8) -> ClassificationResult:
    """A minimal classification result carrying just the intent + confidence."""
    return ClassificationResult(intent=intent, confidence=confidence)


# A roster mirroring the five seeded chairs: three content chairs, one
# topic-only chair with no matching intent, and the empty-areas fallback.
def _roster() -> list[ChairInfo]:
    return [
        ChairInfo(
            id=1,
            name="Program Chair",
            role_title="Program Chair",
            areas=[
                "author_profile_compliance",
                "submission_upload_help",
                "submission_requirements",
                "submission_format_policy",
                "author_list_change",
            ],
        ),
        ChairInfo(
            id=2,
            name="Diversity & Ethics Chair",
            role_title="Diversity & Ethics Chair",
            areas=["review_decision_appeal", "desk_reject_appeal", "anonymity_violation"],
        ),
        ChairInfo(
            id=3,
            name="Local Arrangements Chair",
            role_title="Local Arrangements Chair",
            areas=["reviewer_assignment", "review_submission_help", "paper_bidding"],
        ),
        ChairInfo(
            id=4,
            name="Publicity/Sponsorship Chair",
            role_title="Publicity & Sponsorship Chair",
            areas=["reviewer_workload_role", "committee_invitation"],
        ),
        ChairInfo(id=5, name="General Chair", role_title="General Chair", areas=[]),
    ]


# ---------------------------------------------------------------------------
# Correct intent → chair match
# ---------------------------------------------------------------------------
def test_matches_intent_to_owning_chair():
    strategy = IntentMappingStrategy()
    result = strategy.assign(_clf("anonymity_violation"), _roster())
    assert result.chair_id == 2
    assert result.chair_name == "Diversity & Ethics Chair"
    assert result.matched_area == "anonymity_violation"
    assert result.is_fallback is False
    assert result.strategy == "intent_mapping"


def test_committee_intent_routes_to_publicity_chair():
    """The committee-family intents have a genuine auto-routing path."""
    strategy = IntentMappingStrategy()
    for intent in ("reviewer_workload_role", "committee_invitation"):
        result = strategy.assign(_clf(intent), _roster())
        assert result.chair_id == 4, intent
        assert result.is_fallback is False, intent
        assert result.matched_area == intent, intent


# ---------------------------------------------------------------------------
# No-match fallback to the empty-areas (General) chair
# ---------------------------------------------------------------------------
def test_unmatched_intent_falls_back_to_general_chair():
    strategy = IntentMappingStrategy()
    # An intent no chair owns (e.g. a future/unknown label).
    result = strategy.assign(_clf("visa_letter"), _roster())
    assert result.chair_id == 5
    assert result.chair_name == "General Chair"
    assert result.is_fallback is True
    assert result.matched_area is None


def test_no_fallback_chair_yields_unassigned():
    """No owner AND no empty-areas fallback → chair_id None, not a guess."""
    strategy = IntentMappingStrategy()
    roster = [
        ChairInfo(id=1, name="Program Chair", areas=["submission_requirements"]),
        ChairInfo(id=2, name="Ethics Chair", areas=["anonymity_violation"]),
    ]
    result = strategy.assign(_clf("cms_support"), roster)
    assert result.chair_id is None
    assert result.is_fallback is False
    assert "no fallback" in result.reason.lower()


# ---------------------------------------------------------------------------
# Inactive-chair skip
# ---------------------------------------------------------------------------
def test_inactive_owning_chair_is_skipped_to_fallback():
    strategy = IntentMappingStrategy()
    roster = _roster()
    # Deactivate the Diversity & Ethics chair that owns anonymity_violation.
    roster[1].active = False
    result = strategy.assign(_clf("anonymity_violation"), roster)
    # No other active chair owns it → the fallback General Chair takes it.
    assert result.chair_id == 5
    assert result.is_fallback is True


def test_inactive_fallback_chair_is_not_used():
    strategy = IntentMappingStrategy()
    roster = _roster()
    roster[4].active = False  # deactivate the General (fallback) chair
    result = strategy.assign(_clf("visa_letter"), roster)
    # Owner absent and the only fallback is inactive → unassigned.
    assert result.chair_id is None


def test_no_active_chairs_yields_unassigned():
    strategy = IntentMappingStrategy()
    roster = [ChairInfo(id=1, name="Program Chair", areas=["anonymity_violation"], active=False)]
    result = strategy.assign(_clf("anonymity_violation"), roster)
    assert result.chair_id is None
    assert "no active chairs" in result.reason.lower()


# ---------------------------------------------------------------------------
# Deterministic tie-breaking
# ---------------------------------------------------------------------------
def test_multiple_owners_break_tie_by_lowest_id():
    strategy = IntentMappingStrategy()
    roster = [
        ChairInfo(id=7, name="Chair Seven", areas=["anonymity_violation"]),
        ChairInfo(id=3, name="Chair Three", areas=["anonymity_violation"]),
        ChairInfo(id=5, name="Chair Five", areas=["anonymity_violation"]),
    ]
    result = strategy.assign(_clf("anonymity_violation"), roster)
    assert result.chair_id == 3


def test_multiple_empty_areas_fallbacks_break_tie_by_lowest_id():
    strategy = IntentMappingStrategy()
    roster = [
        ChairInfo(id=9, name="Fallback Nine", areas=[]),
        ChairInfo(id=4, name="Fallback Four", areas=[]),
    ]
    result = strategy.assign(_clf("cms_support"), roster)
    assert result.chair_id == 4
    assert result.is_fallback is True


# ---------------------------------------------------------------------------
# Factory / config flag
# ---------------------------------------------------------------------------
def test_factory_returns_intent_mapping_strategy():
    strategy = get_chair_router("intent_mapping")
    assert isinstance(strategy, IntentMappingStrategy)
    assert isinstance(strategy, ChairRoutingStrategy)


def test_factory_defaults_to_configured_strategy():
    # No argument → reads CHAIR_ROUTING_STRATEGY (default "intent_mapping").
    assert isinstance(get_chair_router(), IntentMappingStrategy)


def test_factory_rejects_unknown_strategy():
    with pytest.raises(ValueError):
        get_chair_router("nonexistent_strategy")
