"""Manual policy invoke: ``forced_policy_key`` on POST /emails/{id}/redraft (3).

The forced policy is a GUARANTEED EXTRA grounding slot — ``MAX_RETRIEVED_CHUNKS``
still governs the ranked chunks, so a chair's pick can never be evicted by
re-ranking. It must be ACTIVE (this creates NEW grounding for a reply that may be
sent), and an unresolvable key degrades to normal retrieval instead of failing
the re-draft.

The no-key path is regression-critical: it must be bit-for-bit the old behaviour.
"""

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from sqlalchemy import select

import main
from app.api.v1 import emails as emails_api
from app.db.database import get_db
from app.db.models import Base, Email, PolicyDocument
from app.pipeline.orchestrator import EmailPipeline
from app.pipeline.retriever import RetrievedChunk


def _chunk(pid, title="T", content="C", score=1.0):
    return RetrievedChunk(
        policy_id=pid, title=title, content=content, score=score, category="cat"
    )


# Stand-in for the ranked top-k the retriever would return.
RANKED = [_chunk("policy_186", score=0.9), _chunk("policy_172", score=0.8),
          _chunk("policy_171", score=0.7)]


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _seed_policy(factory, key, *, status="active", visibility="internal"):
    async with factory() as s:
        s.add(PolicyDocument(policy_key=key, title=f"Title {key}",
                             content=f"Body of {key}", category="deletion",
                             visibility=visibility, status=status))
        await s.commit()


# ---------------------------------------------------------------------------
# _force_policy_chunk — the resolution rule
# ---------------------------------------------------------------------------
async def test_active_policy_resolves_to_a_chunk(session_factory):
    await _seed_policy(session_factory, "int_paper-deletion__v2")
    p = EmailPipeline()
    async with session_factory() as db:
        c = await p._force_policy_chunk(db, "int_paper-deletion__v2", RANKED)
    assert c is not None
    assert c.policy_id == "int_paper-deletion__v2"
    assert c.title == "Title int_paper-deletion__v2"
    assert c.content == "Body of int_paper-deletion__v2"


async def test_internal_visibility_is_not_filtered(session_factory):
    """Forcing a chair-authored INTERNAL policy is the primary use case."""
    await _seed_policy(session_factory, "int_x", visibility="internal")
    p = EmailPipeline()
    async with session_factory() as db:
        assert await p._force_policy_chunk(db, "int_x", []) is not None


@pytest.mark.parametrize("bad_status", ["inactive", "archived"])
async def test_non_active_policy_is_refused(session_factory, bad_status):
    """Retired/superseded policies must NOT be forced into a live draft."""
    await _seed_policy(session_factory, "int_retired", status=bad_status)
    p = EmailPipeline()
    async with session_factory() as db:
        assert await p._force_policy_chunk(db, "int_retired", []) is None


async def test_unknown_key_is_ignored(session_factory):
    p = EmailPipeline()
    async with session_factory() as db:
        assert await p._force_policy_chunk(db, "int_does_not_exist", []) is None


async def test_already_retrieved_policy_is_not_duplicated(session_factory):
    await _seed_policy(session_factory, "policy_186")
    p = EmailPipeline()
    async with session_factory() as db:
        assert await p._force_policy_chunk(db, "policy_186", RANKED) is None


async def test_lookup_failure_is_swallowed(session_factory, monkeypatch):
    p = EmailPipeline()

    async def explode(db, keys):
        raise RuntimeError("db down")

    monkeypatch.setattr(p.policy_repo, "get_by_keys", explode)
    async with session_factory() as db:
        assert await p._force_policy_chunk(db, "int_x", []) is None


# ---------------------------------------------------------------------------
# _compute — the extra slot, and the untouched default path
# ---------------------------------------------------------------------------
class _StubRetriever:
    def __init__(self):
        self.calls = []

    async def retrieve(self, query, intent, top_k=3, *, prior_intent=""):
        self.calls.append({"query": query, "top_k": top_k})
        return list(RANKED)


async def _compute_with(factory, forced, seed=None, seed_status="active"):
    if seed:
        await _seed_policy(factory, seed, status=seed_status)
    p = EmailPipeline()
    p.retriever = _StubRetriever()
    async with factory() as db:
        c = await p._compute(
            {"from": "a@b.com", "subject": "s", "body": "b"},
            db,
            forced_policy_key=forced,
        )
    return c, p


async def test_no_forced_key_is_unchanged(session_factory):
    """REGRESSION: the default path must be exactly the old behaviour."""
    c, p = await _compute_with(session_factory, None)
    ids = [ch.policy_id for ch in c.retrieved_chunks]
    assert ids == ["policy_186", "policy_172", "policy_171"]
    assert len(ids) == 3
    # top_k still governs; nothing extra requested.
    assert p.retriever.calls[0]["top_k"] == 3
    assert c.record["retrieval_context"]["retrieved_ids"] == ids


async def test_forced_key_appends_a_fourth_chunk(session_factory):
    c, p = await _compute_with(session_factory, "int_forced", seed="int_forced")
    ids = [ch.policy_id for ch in c.retrieved_chunks]
    assert ids == ["policy_186", "policy_172", "policy_171", "int_forced"]
    assert len(ids) == 4  # EXTRA slot — top-3 ranked chunks all survive
    # The ranked retrieval call is untouched (still top_k=3, not 4).
    assert p.retriever.calls[0]["top_k"] == 3


async def test_forced_key_already_in_topk_is_not_duplicated(session_factory):
    c, _ = await _compute_with(session_factory, "policy_172", seed="policy_172")
    ids = [ch.policy_id for ch in c.retrieved_chunks]
    assert ids == ["policy_186", "policy_172", "policy_171"]
    assert ids.count("policy_172") == 1


async def test_unresolvable_forced_key_degrades_to_normal_retrieval(session_factory):
    """No 500, no empty grounding — just the ranked chunks."""
    c, _ = await _compute_with(session_factory, "int_nope")  # never seeded
    ids = [ch.policy_id for ch in c.retrieved_chunks]
    assert ids == ["policy_186", "policy_172", "policy_171"]


async def test_inactive_forced_key_degrades_to_normal_retrieval(session_factory):
    c, _ = await _compute_with(
        session_factory, "int_old", seed="int_old", seed_status="inactive"
    )
    ids = [ch.policy_id for ch in c.retrieved_chunks]
    assert "int_old" not in ids
    assert ids == ["policy_186", "policy_172", "policy_171"]


async def test_retrieval_context_includes_the_forced_policy(session_factory):
    """Persisted ids drive 2a's hydration AND the re-eval sweep's set gate."""
    c, _ = await _compute_with(session_factory, "int_forced", seed="int_forced")
    ctx = c.record["retrieval_context"]
    assert ctx["retrieved_ids"] == [
        "policy_186", "policy_172", "policy_171", "int_forced",
    ]
    # chunk_hash is derived from the same (forced-inclusive) chunk list.
    assert ctx["chunk_hash"]


async def test_forced_chunk_reaches_the_drafter(session_factory):
    """Step 3 only guarantees CONTEXT; the prompt itself is step 4's job."""
    await _seed_policy(session_factory, "int_forced")
    p = EmailPipeline()
    p.retriever = _StubRetriever()
    seen = {}

    original = p.drafter.draft

    async def spy(email, classification, chunks, forced_policy_key=None):
        seen["ids"] = [c.policy_id for c in chunks]
        seen["forced"] = forced_policy_key
        return await original(email, classification, chunks, forced_policy_key)

    p.drafter.draft = spy
    async with session_factory() as db:
        await p._compute({"from": "a@b.com", "subject": "s", "body": "b"}, db,
                         forced_policy_key="int_forced")
    assert seen["ids"][-1] == "int_forced"
    # Task 4: the key itself is forwarded so the prompt can mark that block.
    assert seen["forced"] == "int_forced"


# ---------------------------------------------------------------------------
# Endpoint wiring
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def client(session_factory):
    async def _override_get_db():
        async with session_factory() as s:
            yield s

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, session_factory
    main.app.dependency_overrides.clear()


async def _seed_email(factory):
    async with factory() as s:
        e = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
        s.add(e)
        await s.commit()
        return str(e.id)


async def test_redraft_without_body_still_works(client, monkeypatch):
    """REGRESSION: existing callers send NO body — must stay a valid request."""
    c, factory = client
    email_id = await _seed_email(factory)
    captured = {}

    async def fake_bg(eid, forced=None, excluded=None):
        captured["args"] = (eid, forced)

    monkeypatch.setattr(emails_api, "_redraft_email_bg", fake_bg)

    r = await c.post(f"/api/v1/emails/{email_id}/redraft")
    assert r.status_code == 202
    assert r.json()["redrafting"] is True
    assert r.json()["forced_policy_key"] is None
    assert captured["args"] == (email_id, None)


async def test_redraft_accepts_and_forwards_forced_policy_key(client, monkeypatch):
    c, factory = client
    email_id = await _seed_email(factory)
    captured = {}

    async def fake_bg(eid, forced=None, excluded=None):
        captured["args"] = (eid, forced)

    monkeypatch.setattr(emails_api, "_redraft_email_bg", fake_bg)

    r = await c.post(
        f"/api/v1/emails/{email_id}/redraft",
        json={"forced_policy_key": "int_paper-deletion__v2"},
    )
    assert r.status_code == 202
    assert r.json()["forced_policy_key"] == "int_paper-deletion__v2"
    assert captured["args"] == (email_id, "int_paper-deletion__v2")


async def test_redraft_unknown_email_still_404s(client):
    c, _ = client
    r = await c.post("/api/v1/emails/does-not-exist/redraft",
                     json={"forced_policy_key": "int_x"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# forced_policy_applied — DERIVED outcome signal (3d)
# ---------------------------------------------------------------------------
async def test_applied_is_true_when_the_forced_policy_resolved(session_factory):
    c, _ = await _compute_with(session_factory, "int_forced", seed="int_forced")
    ctx = c.record["retrieval_context"]
    assert ctx["forced_policy_key"] == "int_forced"
    assert "int_forced" in ctx["retrieved_ids"]

    e = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
    e.retrieval_context = ctx
    assert emails_api._forced_policy_applied(e) is True
    assert emails_api._email_to_dict(e)["forced_policy_applied"] is True


async def test_applied_is_false_when_the_forced_policy_was_skipped(session_factory):
    """Requested but unresolvable — the case the chair must be warned about."""
    c, _ = await _compute_with(session_factory, "int_nope")  # never seeded
    ctx = c.record["retrieval_context"]
    assert ctx["forced_policy_key"] == "int_nope"
    assert "int_nope" not in ctx["retrieved_ids"]

    e = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
    e.retrieval_context = ctx
    assert emails_api._forced_policy_applied(e) is False
    assert emails_api._email_to_dict(e)["forced_policy_applied"] is False


async def test_applied_is_false_for_an_inactive_forced_policy(session_factory):
    c, _ = await _compute_with(
        session_factory, "int_old", seed="int_old", seed_status="inactive"
    )
    e = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
    e.retrieval_context = c.record["retrieval_context"]
    assert emails_api._forced_policy_applied(e) is False


async def test_applied_is_null_without_a_forced_key(session_factory):
    """REGRESSION: a plain redraft is unchanged — no forced key, no signal."""
    c, _ = await _compute_with(session_factory, None)
    ctx = c.record["retrieval_context"]
    assert ctx["forced_policy_key"] is None

    e = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
    e.retrieval_context = ctx
    assert emails_api._forced_policy_applied(e) is None
    assert emails_api._email_to_dict(e)["forced_policy_applied"] is None


async def test_applied_is_null_for_legacy_rows():
    """Drafts predating manual invoke (no key in ctx) and NULL-context rows."""
    legacy = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
    legacy.retrieval_context = {"query": "q", "retrieved_ids": ["policy_186"]}
    assert emails_api._forced_policy_applied(legacy) is None

    nullctx = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
    nullctx.retrieval_context = None
    assert emails_api._forced_policy_applied(nullctx) is None


async def test_applied_is_derived_not_stored(session_factory):
    """Editing retrieved_ids alone flips the answer — proving it is computed."""
    e = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
    e.retrieval_context = {"retrieved_ids": ["int_x"], "forced_policy_key": "int_x"}
    assert emails_api._forced_policy_applied(e) is True
    e.retrieval_context = {"retrieved_ids": [], "forced_policy_key": "int_x"}
    assert emails_api._forced_policy_applied(e) is False


# ---------------------------------------------------------------------------
# excluded_policy_ids — request-schema bound (exclusion step 1)
# ---------------------------------------------------------------------------
async def test_excluded_policy_ids_rejects_more_than_ten(client, monkeypatch):
    """The cap is a malformed-input guard, so it must 422 before any work runs."""
    c, factory = client
    email_id = await _seed_email(factory)
    called = {"bg": False}

    async def fake_bg(eid, forced=None, excluded=None):
        called["bg"] = True

    monkeypatch.setattr(emails_api, "_redraft_email_bg", fake_bg)

    r = await c.post(
        f"/api/v1/emails/{email_id}/redraft",
        json={"excluded_policy_ids": [f"policy_{n}" for n in range(11)]},
    )

    assert r.status_code == 422
    # Rejected at validation time — no redraft was scheduled.
    assert called["bg"] is False


async def test_excluded_policy_ids_accepts_exactly_ten(client, monkeypatch):
    """Boundary: 10 is the documented maximum, not one below it."""
    c, factory = client
    email_id = await _seed_email(factory)

    async def fake_bg(eid, forced=None, excluded=None):
        return None

    monkeypatch.setattr(emails_api, "_redraft_email_bg", fake_bg)

    r = await c.post(
        f"/api/v1/emails/{email_id}/redraft",
        json={"excluded_policy_ids": [f"policy_{n}" for n in range(10)]},
    )
    assert r.status_code == 202


async def test_excluded_policy_ids_is_optional(client, monkeypatch):
    """REGRESSION: omitting the field (and the whole body) still works."""
    c, factory = client
    email_id = await _seed_email(factory)

    async def fake_bg(eid, forced=None, excluded=None):
        return None

    monkeypatch.setattr(emails_api, "_redraft_email_bg", fake_bg)

    assert (await c.post(f"/api/v1/emails/{email_id}/redraft")).status_code == 202
    assert (
        await c.post(
            f"/api/v1/emails/{email_id}/redraft",
            json={"forced_policy_key": "int_x"},
        )
    ).status_code == 202


def test_excluded_policy_ids_defaults_to_none():
    """The field is additive: an empty request body leaves it unset."""
    assert emails_api.RedraftRequest().excluded_policy_ids is None
    assert emails_api.RedraftRequest(
        excluded_policy_ids=["policy_186"]
    ).excluded_policy_ids == ["policy_186"]


# ---------------------------------------------------------------------------
# excluded_policy_ids — end-to-end threading (exclusion step 2)
# ---------------------------------------------------------------------------
# No filter exists yet; these pin only that the value SURVIVES every hop, and
# that the grounding set is provably unchanged until the filter lands.
async def test_endpoint_threads_excluded_ids_to_the_background_task(client, monkeypatch):
    c, factory = client
    email_id = await _seed_email(factory)
    captured = {}

    async def fake_bg(eid, forced=None, excluded=None):
        captured["args"] = (eid, forced, excluded)

    monkeypatch.setattr(emails_api, "_redraft_email_bg", fake_bg)

    r = await c.post(
        f"/api/v1/emails/{email_id}/redraft",
        json={"forced_policy_key": "int_x", "excluded_policy_ids": ["policy_186"]},
    )
    assert r.status_code == 202
    assert r.json()["excluded_policy_ids"] == ["policy_186"]
    assert captured["args"] == (email_id, "int_x", ["policy_186"])


async def test_plain_retry_threads_none_for_both(client, monkeypatch):
    """REGRESSION: a bodyless retry must still pass None for both knobs."""
    c, factory = client
    email_id = await _seed_email(factory)
    captured = {}

    async def fake_bg(eid, forced=None, excluded=None):
        captured["args"] = (eid, forced, excluded)

    monkeypatch.setattr(emails_api, "_redraft_email_bg", fake_bg)

    r = await c.post(f"/api/v1/emails/{email_id}/redraft")
    assert r.status_code == 202
    assert r.json()["excluded_policy_ids"] is None
    assert captured["args"] == (email_id, None, None)


async def test_reprocess_email_forwards_excluded_ids_to_compute(session_factory, monkeypatch):
    """reprocess_email → _compute is the last hop; assert the kwarg arrives."""
    p = EmailPipeline()
    seen = {}

    class _Res:
        record = {}

    async def spy_compute(email_data, db, *, forced_policy_key=None, excluded_policy_ids=None):
        seen["forced"] = forced_policy_key
        seen["excluded"] = excluded_policy_ids
        raise RuntimeError("stop after capture")

    monkeypatch.setattr(p, "_compute", spy_compute)
    email = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
    email.id = 1

    async with session_factory() as db:
        with pytest.raises(RuntimeError):
            await p.reprocess_email(
                db, email,
                forced_policy_key="int_x",
                excluded_policy_ids=["policy_186", "policy_172"],
            )

    assert seen["forced"] == "int_x"
    assert seen["excluded"] == ["policy_186", "policy_172"]


async def test_public_compute_seam_forwards_excluded_ids(session_factory, monkeypatch):
    p = EmailPipeline()
    seen = {}

    async def spy_compute(email_data, db, *, forced_policy_key=None, excluded_policy_ids=None):
        seen["excluded"] = excluded_policy_ids
        return object()

    monkeypatch.setattr(p, "_compute", spy_compute)
    async with session_factory() as db:
        await p.compute({"from": "a@b.com"}, db, excluded_policy_ids=["policy_171"])
    assert seen["excluded"] == ["policy_171"]


async def test_excluded_chunk_is_dropped_from_the_grounding(session_factory):
    """INVERTED from step 2's "no filter yet" pin — the filter now acts."""
    await _seed_policy(session_factory, "int_forced")
    p = EmailPipeline()
    p.retriever = _StubRetriever()
    async with session_factory() as db:
        c = await p._compute(
            {"from": "a@b.com", "subject": "s", "body": "b"},
            db,
            forced_policy_key="int_forced",
            excluded_policy_ids=["policy_186"],
        )

    ids = [ch.policy_id for ch in c.retrieved_chunks]
    assert "policy_186" not in ids
    assert ids == ["policy_172", "policy_171", "int_forced"]
    # Persisted context reflects the filtered set (drives hydration + the sweep).
    assert c.record["retrieval_context"]["retrieved_ids"] == ids


# ---------------------------------------------------------------------------
# Exclusion filter + forced/excluded conflict (exclusion step 3)
# ---------------------------------------------------------------------------
async def _compute_excluding(factory, excluded, forced=None, seed=None):
    if seed:
        await _seed_policy(factory, seed)
    p = EmailPipeline()
    p.retriever = _StubRetriever()
    async with factory() as db:
        c = await p._compute(
            {"from": "a@b.com", "subject": "s", "body": "b"},
            db,
            forced_policy_key=forced,
            excluded_policy_ids=excluded,
        )
    return [ch.policy_id for ch in c.retrieved_chunks], c


async def test_forced_key_that_is_also_excluded_is_not_appended(session_factory):
    """THE conflict case: exclusion wins, the forced chunk must NOT appear.

    Without the guard the ordering would silently undo the removal — the forced
    key is filtered out of the ranked set, then resolved and appended right back.
    """
    ids, c = await _compute_excluding(
        session_factory,
        excluded=["int_forced"],
        forced="int_forced",
        seed="int_forced",   # active, so it WOULD resolve if not blocked
    )

    assert "int_forced" not in ids
    assert ids == ["policy_186", "policy_172", "policy_171"]
    assert c.record["retrieval_context"]["retrieved_ids"] == ids


async def test_forced_key_also_excluded_when_it_is_a_ranked_chunk(session_factory):
    """Same conflict, but the forced key is one of the ranked chunks."""
    ids, _ = await _compute_excluding(
        session_factory, excluded=["policy_172"], forced="policy_172", seed="policy_172"
    )
    assert "policy_172" not in ids
    assert ids == ["policy_186", "policy_171"]


async def test_excluding_by_an_older_lineage_key_still_drops_the_new_version(
    session_factory,
):
    """Root matching: excluding `int_base` also removes its `__v2` successor.

    An exact-key exclusion would go stale here — exactly the case the KB-edit
    sweep triggers — and the removed policy would reappear under its new key.
    """
    async with session_factory() as s:
        s.add(PolicyDocument(policy_key="int_base", title="v1", content="c",
                             category="c", visibility="internal", status="inactive",
                             superseded_by="int_base__v2", version=1))
        s.add(PolicyDocument(policy_key="int_base__v2", title="v2", content="c",
                             category="c", visibility="internal", status="active",
                             supersedes="int_base", root_key="int_base", version=2))
        await s.commit()

    class _R:
        async def retrieve(self, query, intent, top_k=3, *, prior_intent=""):
            return [_chunk("int_base__v2"), _chunk("policy_186")]

    p = EmailPipeline()
    p.retriever = _R()
    async with session_factory() as db:
        c = await p._compute(
            {"from": "a@b.com", "subject": "s", "body": "b"},
            db,
            excluded_policy_ids=["int_base"],  # the OLD key
        )

    ids = [ch.policy_id for ch in c.retrieved_chunks]
    assert "int_base__v2" not in ids   # dropped via its lineage root
    assert ids == ["policy_186"]


async def test_unknown_excluded_id_is_a_no_op(session_factory):
    """A key with no row maps to itself — it must not widen the exclusion."""
    ids, _ = await _compute_excluding(session_factory, excluded=["int_does_not_exist"])
    assert ids == ["policy_186", "policy_172", "policy_171"]


async def test_excluding_every_chunk_yields_empty_grounding(session_factory):
    """Degrades safely: no crash, empty grounding (drafter emits its no-context
    branch and the router's floor forces human_review)."""
    ids, c = await _compute_excluding(
        session_factory, excluded=["policy_186", "policy_172", "policy_171"]
    )
    assert ids == []
    assert c.record["retrieval_context"]["retrieved_ids"] == []


async def test_no_exclusions_is_byte_identical_and_costs_no_extra_query(
    session_factory, monkeypatch
):
    """REGRESSION: the default path must not even touch the policy repo."""
    await _seed_policy(session_factory, "int_forced")
    p = EmailPipeline()
    p.retriever = _StubRetriever()

    calls = {"n": 0}
    original = p.policy_repo.get_by_keys

    async def counting(db, keys):
        calls["n"] += 1
        return await original(db, keys)

    monkeypatch.setattr(p.policy_repo, "get_by_keys", counting)

    async with session_factory() as db:
        c = await p._compute(
            {"from": "a@b.com", "subject": "s", "body": "b"},
            db,
            forced_policy_key="int_forced",
            excluded_policy_ids=None,
        )

    assert [ch.policy_id for ch in c.retrieved_chunks] == [
        "policy_186", "policy_172", "policy_171", "int_forced",
    ]
    # Exactly ONE lookup: the forced resolve. No lineage query when nothing is excluded.
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# "never remove everything" guard (exclusion step 4)
# ---------------------------------------------------------------------------
# Enforced in the ENDPOINT, before scheduling: the re-draft is async (202 +
# background task), so after scheduling there is no response left to reject with.
async def _seed_email_with_context(factory, retrieved_ids):
    async with factory() as s:
        e = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
        e.retrieval_context = {"query": "q", "retrieved_ids": list(retrieved_ids)}
        s.add(e)
        await s.commit()
        return str(e.id)


async def test_excluding_every_current_policy_is_rejected(client, monkeypatch):
    """Excluding all 4 → 409, and nothing is scheduled or flagged."""
    c, factory = client
    ids = ["policy_186", "policy_172", "policy_171", "int_forced"]
    email_id = await _seed_email_with_context(factory, ids)
    scheduled = {"n": 0}

    async def fake_bg(eid, forced=None, excluded=None):
        scheduled["n"] += 1

    monkeypatch.setattr(emails_api, "_redraft_email_bg", fake_bg)

    r = await c.post(
        f"/api/v1/emails/{email_id}/redraft",
        json={"excluded_policy_ids": ids},
    )

    assert r.status_code == 409
    assert "no policy context" in r.json()["detail"]
    # Rejected outright: no background re-draft, and the row is NOT left
    # stranded showing "re-drafting…".
    assert scheduled["n"] == 0
    async with factory() as s:
        row = (await s.execute(select(Email).where(Email.id == int(email_id)))).scalar_one()
        assert row.redrafting is False


async def test_excluding_three_of_four_succeeds(client, monkeypatch):
    """Leaving one policy standing is allowed."""
    c, factory = client
    ids = ["policy_186", "policy_172", "policy_171", "int_forced"]
    email_id = await _seed_email_with_context(factory, ids)
    captured = {}

    async def fake_bg(eid, forced=None, excluded=None):
        captured["excluded"] = excluded

    monkeypatch.setattr(emails_api, "_redraft_email_bg", fake_bg)

    r = await c.post(
        f"/api/v1/emails/{email_id}/redraft",
        json={"excluded_policy_ids": ids[:3]},
    )

    assert r.status_code == 202
    assert captured["excluded"] == ids[:3]


async def test_excluding_everything_but_forcing_a_replacement_is_allowed(
    client, monkeypatch
):
    """The swap workflow: remove all current policies, ground on a new one.

    This is the case the feature exists for, so the guard must not reject it.
    """
    c, factory = client
    ids = ["policy_186", "policy_172", "policy_171"]
    email_id = await _seed_email_with_context(factory, ids)

    async def fake_bg(eid, forced=None, excluded=None):
        return None

    monkeypatch.setattr(emails_api, "_redraft_email_bg", fake_bg)

    r = await c.post(
        f"/api/v1/emails/{email_id}/redraft",
        json={"excluded_policy_ids": ids, "forced_policy_key": "int_replacement"},
    )
    assert r.status_code == 202


async def test_excluding_everything_AND_the_forced_key_is_rejected(client, monkeypatch):
    """A forced key that is itself excluded cannot rescue an empty set."""
    c, factory = client
    ids = ["policy_186", "policy_172"]
    email_id = await _seed_email_with_context(factory, ids)

    async def fake_bg(eid, forced=None, excluded=None):
        return None

    monkeypatch.setattr(emails_api, "_redraft_email_bg", fake_bg)

    r = await c.post(
        f"/api/v1/emails/{email_id}/redraft",
        json={
            "excluded_policy_ids": [*ids, "int_x"],
            "forced_policy_key": "int_x",
        },
    )
    assert r.status_code == 409


async def test_guard_fails_open_for_a_legacy_null_context_row(client, monkeypatch):
    """No stored context ⇒ nothing to compare against ⇒ let it through."""
    c, factory = client
    email_id = await _seed_email(factory)  # retrieval_context is NULL

    async def fake_bg(eid, forced=None, excluded=None):
        return None

    monkeypatch.setattr(emails_api, "_redraft_email_bg", fake_bg)

    r = await c.post(
        f"/api/v1/emails/{email_id}/redraft",
        json={"excluded_policy_ids": ["policy_186"]},
    )
    assert r.status_code == 202


async def test_plain_retry_is_never_blocked_by_the_guard(client, monkeypatch):
    """REGRESSION: no exclusions ⇒ the guard short-circuits, no lookup, no 409."""
    c, factory = client
    email_id = await _seed_email_with_context(factory, ["policy_186"])

    async def fake_bg(eid, forced=None, excluded=None):
        return None

    monkeypatch.setattr(emails_api, "_redraft_email_bg", fake_bg)

    assert (await c.post(f"/api/v1/emails/{email_id}/redraft")).status_code == 202
