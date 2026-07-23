"""Unit tests for the policy conflict detector (LLM boundary mocked)."""
import pytest

from app.core.config import settings
from app.pipeline import policy_conflict as pc
from app.pipeline.policy_conflict import ConflictReport, _parse, detect_conflicts

CANDS = [
    {
        "policy_key": "policy_142",
        "title": "Reviewer deadline",
        "content": "Reviews are due within 14 days of assignment.",
    },
    {
        "policy_key": "policy_118",
        "title": "Author registration",
        "content": "Authors must register by June 1.",
    },
]


class _FakeResp:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _stub_local(monkeypatch, content):
    """Route detect_conflicts through the real _call_local, returning `content`."""
    monkeypatch.setattr(settings, "MODEL_PROVIDER", "local")
    monkeypatch.setattr(settings, "LOCAL_MODEL_BASE_URL", "http://local/v1")
    monkeypatch.setattr(settings, "LOCAL_MODEL_NAME", "m")
    monkeypatch.setattr(settings, "LOCAL_MODEL_API_KEY", None)
    captured = {}

    async def _fake_post_chat(client, url, payload, headers):
        captured["payload"] = payload
        captured["url"] = url
        return _FakeResp(content)

    monkeypatch.setattr(pc, "post_chat", _fake_post_chat)
    return captured


# --- _parse (pure) -------------------------------------------------------

def test_parse_keeps_verbatim_snippet():
    items = _parse(
        '{"conflicts": [{"policy_key": "policy_142", "explanation": "10 vs 14 days", '
        '"snippets": ["due within 14 days"]}]}',
        CANDS,
    )
    assert len(items) == 1
    assert items[0].policy_key == "policy_142"
    assert items[0].title == "Reviewer deadline"  # filled from the candidate
    assert items[0].snippets == ["due within 14 days"]


def test_parse_drops_hallucinated_snippet_but_keeps_conflict():
    items = _parse(
        '{"conflicts": [{"policy_key": "policy_142", "explanation": "x", '
        '"snippets": ["due within 30 days"]}]}',
        CANDS,
    )
    assert len(items) == 1
    assert items[0].snippets == []  # snippet not a real substring → dropped


def test_parse_drops_unknown_key():
    items = _parse(
        '{"conflicts": [{"policy_key": "policy_999", "explanation": "x", "snippets": []}]}',
        CANDS,
    )
    assert items == []


def test_parse_dedupes_repeated_key():
    items = _parse(
        '{"conflicts": [{"policy_key": "policy_142"}, {"policy_key": "policy_142"}]}',
        CANDS,
    )
    assert len(items) == 1


def test_parse_none_on_no_json():
    assert _parse("the policies look consistent to me", CANDS) is None


def test_parse_none_when_conflicts_not_a_list():
    assert _parse('{"conflicts": "nope"}', CANDS) is None


def test_parse_tolerates_prose_around_json():
    items = _parse(
        'Here is the result:\n{"conflicts": [{"policy_key": "policy_118"}]}\nDone.',
        CANDS,
    )
    assert len(items) == 1 and items[0].policy_key == "policy_118"


# --- detect_conflicts (LLM boundary mocked) ------------------------------

@pytest.mark.asyncio
async def test_detect_parses_and_summarizes(monkeypatch):
    captured = _stub_local(
        monkeypatch,
        '{"conflicts": [{"policy_key": "policy_142", "explanation": "10 vs 14 days", '
        '"snippets": ["due within 14 days"]}]}',
    )
    report = await detect_conflicts(
        title="Reviewer response deadline",
        content="Reviews are due within 10 days.",
        candidates=CANDS,
    )
    assert isinstance(report, ConflictReport)
    assert report.available is True
    assert report.summary == "1 of 2 related policies conflict."
    assert report.candidates_checked == ["policy_142", "policy_118"]
    assert [c.policy_key for c in report.conflicts] == ["policy_142"]
    # The new policy + both candidates reach the model.
    user_msg = captured["payload"]["messages"][1]["content"]
    assert "Reviews are due within 10 days." in user_msg
    assert "policy_142" in user_msg and "policy_118" in user_msg


@pytest.mark.asyncio
async def test_detect_zero_conflicts(monkeypatch):
    _stub_local(monkeypatch, '{"conflicts": []}')
    report = await detect_conflicts(title="T", content="C", candidates=CANDS)
    assert report.available is True
    assert report.conflicts == []
    assert report.summary == "No conflicts found among 2 related policies."


@pytest.mark.asyncio
async def test_detect_empty_candidates_is_available(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_PROVIDER", "fallback")  # must not matter
    report = await detect_conflicts(title="T", content="C", candidates=[])
    assert report is not None and report.available is True
    assert report.conflicts == [] and report.candidates_checked == []


@pytest.mark.asyncio
async def test_detect_none_without_real_llm(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_PROVIDER", "fallback")
    report = await detect_conflicts(title="T", content="C", candidates=CANDS)
    assert report is None


@pytest.mark.asyncio
async def test_detect_none_on_unparseable_output(monkeypatch):
    _stub_local(monkeypatch, "they seem fine")
    report = await detect_conflicts(title="T", content="C", candidates=CANDS)
    assert report is None


@pytest.mark.asyncio
async def test_detect_never_raises_on_transport_error(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_PROVIDER", "local")
    monkeypatch.setattr(settings, "LOCAL_MODEL_BASE_URL", "http://local/v1")
    monkeypatch.setattr(settings, "LOCAL_MODEL_NAME", "m")
    monkeypatch.setattr(settings, "LOCAL_MODEL_API_KEY", None)

    async def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(pc, "post_chat", _boom)
    report = await detect_conflicts(title="T", content="C", candidates=CANDS)
    assert report is None  # swallowed → unavailable, never raised
