from types import SimpleNamespace

from app.pipeline.drafter import _build_user_prompt


def _clf():
    return SimpleNamespace(intent="cms_support", confidence=0.9)


def test_prompt_renders_conversation_when_transcript_present():
    email = {
        "from": "a@x.com",
        "sender_name": "Alex",
        "subject": "Upload",
        "body": "single-body-should-not-appear",
        "thread_transcript": "Requester: How?\n\nSupport: Portal.\n\nRequester: It errors.",
    }
    prompt = _build_user_prompt(email, _clf(), [])
    assert "CONVERSATION" in prompt
    assert "It errors." in prompt
    assert "single-body-should-not-appear" not in prompt
    assert "latest" in prompt.lower()


def test_prompt_single_body_unchanged_without_transcript():
    email = {"from": "a@x.com", "subject": "Upload", "body": "MyBody"}
    prompt = _build_user_prompt(email, _clf(), [])
    assert "ORIGINAL EMAIL" in prompt
    assert "Body: MyBody" in prompt
    assert "CONVERSATION" not in prompt
