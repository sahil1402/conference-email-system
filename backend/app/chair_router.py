"""Chair router (the "which chair" decision) — Phase 6A.

A SECOND, independent routing decision layered beside the lane router
(``app/pipeline/router.py``). The lane router decides *faq vs human_review*;
this module decides WHICH chair a ``human_review`` email is assigned to. It is a
separate, swappable component — its own strategy interface, config flag, and
factory — so a learned / RL assignment policy can replace the rule-based mapping
later without touching the callers or the lane decision. The lane logic is NOT
duplicated here: the orchestrator calls this only after the lane router has
already chosen ``human_review``.

Kept pure and DB-free, like the other pipeline modules: a strategy receives the
classification plus the list of candidate chairs and returns an assignment. The
orchestrator owns fetching active chairs from the repository (via
``ChairRepository``) and persisting the resulting ``assigned_chair_id`` — so
this module stays unit-testable with plain in-memory ``ChairInfo`` objects and
never imports the DB layer.

Fallback convention: a chair whose ``areas`` list is empty is the catch-all
(the General Chair). When no active chair owns the classified intent, the
assignment goes to that fallback chair; if no fallback chair is active either,
``chair_id`` comes back ``None`` (the orchestrator leaves the email unassigned
rather than guessing).
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.core.config import settings
from app.pipeline.classifier import ClassificationResult


class ChairInfo(BaseModel):
    """A DB-free view of a candidate chair the router can assign to.

    Mirrors the fields of the ``Chair`` ORM row the strategy actually needs; the
    orchestrator builds these from ``ChairRepository`` results so the strategy
    never touches SQLAlchemy.
    """

    id: int
    name: str
    role_title: str = ""
    areas: list[str] = Field(default_factory=list)
    active: bool = True


class ChairAssignment(BaseModel):
    """Output of the chair router — which chair, and a transparent rationale."""

    chair_id: int | None = Field(
        ..., description="Assigned chair id, or None if no chair could be assigned."
    )
    chair_name: str | None = Field(
        default=None, description="Assigned chair's name (None when unassigned)."
    )
    reason: str = Field(
        default="", description="Human-readable explanation of the assignment."
    )
    matched_area: str | None = Field(
        default=None,
        description="The chair area string that matched the intent (None on fallback/unassigned).",
    )
    is_fallback: bool = Field(
        default=False,
        description="True when the email was assigned to the catch-all fallback chair.",
    )
    strategy: str = Field(
        default="", description="Strategy that produced this assignment."
    )


class ChairRoutingStrategy(ABC):
    """Interface for the 'which chair' decision (the swappable seam).

    Implementations map a classified email to a chair. Keeping this an abstract
    base means a future ``LearnedChairStrategy`` / ``RLChairStrategy`` can be
    dropped in behind the ``CHAIR_ROUTING_STRATEGY`` flag without any caller
    (orchestrator / API) changing how it calls ``assign``.
    """

    #: Stable identifier for the strategy (surfaced in the assignment + audit).
    name: str = "abstract"

    @abstractmethod
    def assign(
        self, classification: ClassificationResult, chairs: list[ChairInfo]
    ) -> ChairAssignment:
        """Return the chair assignment for a classified, human-review email."""
        raise NotImplementedError


class IntentMappingStrategy(ChairRoutingStrategy):
    """Rule-based chair assignment by intent → chair ``areas`` lookup.

    Considers only ACTIVE chairs. Assigns to the chair whose ``areas`` contains
    the classified intent; ties (more than one owner) break deterministically by
    lowest chair id. With no owner, assigns to the catch-all fallback chair (the
    active chair with empty ``areas``, lowest id). With no fallback active,
    returns ``chair_id=None``.
    """

    name = "intent_mapping"

    def assign(
        self, classification: ClassificationResult, chairs: list[ChairInfo]
    ) -> ChairAssignment:
        intent = classification.intent
        active = [c for c in chairs if c.active]

        if not active:
            return ChairAssignment(
                chair_id=None,
                reason="No active chairs are available to assign.",
                strategy=self.name,
            )

        # Direct intent → area match. Deterministic: lowest id among owners.
        owners = sorted(
            (c for c in active if intent in (c.areas or [])), key=lambda c: c.id
        )
        if owners:
            chair = owners[0]
            return ChairAssignment(
                chair_id=chair.id,
                chair_name=chair.name,
                reason=f"Intent '{intent}' is owned by chair '{chair.name}'.",
                matched_area=intent,
                is_fallback=False,
                strategy=self.name,
            )

        # Fallback: the catch-all chair (empty areas), lowest id.
        fallbacks = sorted(
            (c for c in active if not (c.areas or [])), key=lambda c: c.id
        )
        if fallbacks:
            chair = fallbacks[0]
            return ChairAssignment(
                chair_id=chair.id,
                chair_name=chair.name,
                reason=(
                    f"No active chair owns intent '{intent}'; assigned to the "
                    f"fallback chair '{chair.name}'."
                ),
                matched_area=None,
                is_fallback=True,
                strategy=self.name,
            )

        return ChairAssignment(
            chair_id=None,
            reason=(
                f"No active chair owns intent '{intent}' and no fallback chair "
                f"(empty areas) is active."
            ),
            strategy=self.name,
        )


def get_chair_router(strategy: str | None = None) -> ChairRoutingStrategy:
    """Return the chair-routing strategy for ``strategy`` (or the configured one).

    The single construction seam. Unknown strategies raise ``ValueError`` rather
    than silently falling back, so a typo in config fails loudly. Adding a new
    strategy = widen ``CHAIR_ROUTING_STRATEGY`` in config + add a branch here.
    """
    strategy = strategy or settings.CHAIR_ROUTING_STRATEGY
    if strategy == "intent_mapping":
        return IntentMappingStrategy()
    raise ValueError(f"Unknown CHAIR_ROUTING_STRATEGY: {strategy!r}")
