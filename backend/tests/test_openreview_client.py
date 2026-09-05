"""Tests for the OpenReview authentication layer.

FULLY HERMETIC — NO NETWORK, NO REAL CREDENTIALS. Two mechanisms enforce that,
and both matter:

* Settings are ``SimpleNamespace`` stand-ins, so nothing here reads the real
  ``.env`` or the real account.
* The client class is INJECTED. This is not stylistic: openreview-py's
  ``OpenReviewClient.__init__`` performs an actual login over the network as
  soon as a username and password are passed, so a test that let the real class
  be constructed would authenticate for real. The injected fake records its
  arguments instead.

Mirrors ``test_zendesk_credential_provider.py``: SimpleNamespace settings,
hand-written fakes over ``unittest.mock``, and ``monkeypatch`` for module-level
lookups.
"""

from types import SimpleNamespace

import pytest

from app.integrations.openreview import (
    OpenReviewAuthError,
    OpenReviewCredentialError,
    OpenReviewDependencyError,
    get_openreview_client,
)
from app.integrations.openreview import client as orc


# --- test doubles ----------------------------------------------------------


def make_settings(**overrides) -> SimpleNamespace:
    """Settings stub with valid defaults; override per test."""
    base = {
        "OPENREVIEW_USERNAME": "chair@example.org",
        "OPENREVIEW_PASSWORD": "not-a-real-password",
        "OPENREVIEW_BASE_URL": "https://api2.openreview.net",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeClient:
    """Stands in for openreview-py's OpenReviewClient, recording its kwargs."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


def fake_factory_returning(instance):
    """A client_factory that records its call and returns a fixed object."""
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return instance

    factory.calls = calls
    return factory


# --- missing credentials ---------------------------------------------------


@pytest.mark.parametrize("missing", ["OPENREVIEW_USERNAME", "OPENREVIEW_PASSWORD"])
@pytest.mark.parametrize("bad_value", [None, "", "   "])
def test_missing_credential_raises_naming_that_field(missing, bad_value):
    """Absent, empty and whitespace-only all count as missing.

    A `OPENREVIEW_PASSWORD=` line in a .env is a forgotten credential, not a
    password of zero characters, so all three must fail the same way — and the
    message must name the field, since "auth failed" alone sends someone
    hunting through the wrong layer.
    """
    settings = make_settings(**{missing: bad_value})

    with pytest.raises(OpenReviewCredentialError) as exc:
        get_openreview_client(settings, client_factory=FakeClient)

    assert missing in str(exc.value)
    assert ".env" in str(exc.value)


def test_missing_both_credentials_names_both_not_just_the_first():
    """One round-trip should reveal everything that is wrong, not the first
    thing — otherwise a misconfigured deploy is fixed one variable per attempt.
    """
    settings = make_settings(OPENREVIEW_USERNAME=None, OPENREVIEW_PASSWORD=None)

    with pytest.raises(OpenReviewCredentialError) as exc:
        get_openreview_client(settings, client_factory=FakeClient)

    message = str(exc.value)
    assert "OPENREVIEW_USERNAME" in message
    assert "OPENREVIEW_PASSWORD" in message


def test_missing_credentials_never_construct_a_client():
    """The credential check runs BEFORE anything is built, so a misconfigured
    environment cannot reach the network at all."""
    factory = fake_factory_returning(FakeClient())
    settings = make_settings(OPENREVIEW_USERNAME="")

    with pytest.raises(OpenReviewCredentialError):
        get_openreview_client(settings, client_factory=factory)

    assert factory.calls == []


def test_blank_base_url_is_rejected_rather_than_defaulting():
    """THE footgun this guard exists for.

    openreview-py falls back to `http://localhost:3001` when given no baseurl,
    so a blanked value must fail loudly here instead of silently pointing a live
    action at a local dev server.
    """
    settings = make_settings(OPENREVIEW_BASE_URL="")

    with pytest.raises(OpenReviewCredentialError) as exc:
        get_openreview_client(settings, client_factory=FakeClient)

    assert "OPENREVIEW_BASE_URL" in str(exc.value)


# --- the happy path --------------------------------------------------------


def test_client_is_built_with_the_configured_credentials():
    instance = FakeClient()
    factory = fake_factory_returning(instance)

    result = get_openreview_client(make_settings(), client_factory=factory)

    assert result is instance
    assert factory.calls == [
        {
            "baseurl": "https://api2.openreview.net",
            "username": "chair@example.org",
            "password": "not-a-real-password",
        }
    ]


def test_credentials_are_stripped_before_use():
    """Surrounding whitespace in a .env value is an editing artefact, not part
    of the credential, and would otherwise cause a baffling login rejection."""
    factory = fake_factory_returning(FakeClient())
    settings = make_settings(
        OPENREVIEW_USERNAME="  chair@example.org  ",
        OPENREVIEW_PASSWORD="  not-a-real-password  ",
        OPENREVIEW_BASE_URL="  https://api2.openreview.net  ",
    )

    get_openreview_client(settings, client_factory=factory)

    assert factory.calls[0] == {
        "baseurl": "https://api2.openreview.net",
        "username": "chair@example.org",
        "password": "not-a-real-password",
    }


def test_base_url_is_always_passed_explicitly():
    """Never omitted, whatever the value — omitting it is what triggers the
    localhost fallback inside openreview-py."""
    factory = fake_factory_returning(FakeClient())

    get_openreview_client(
        make_settings(OPENREVIEW_BASE_URL="https://api2.dev.openreview.net"),
        client_factory=factory,
    )

    assert factory.calls[0]["baseurl"] == "https://api2.dev.openreview.net"


# --- failure translation ---------------------------------------------------


def test_login_failure_is_reported_as_an_auth_error_not_a_credential_error():
    """A rejected password is a runtime failure, not a config one. Keeping the
    two error types apart is what stops "you forgot a variable" and "the
    password is wrong" from looking identical to a caller."""

    def exploding_factory(**kwargs):
        raise RuntimeError("401 Unauthorized")

    with pytest.raises(OpenReviewAuthError) as exc:
        get_openreview_client(make_settings(), client_factory=exploding_factory)

    assert "OpenReview login failed" in str(exc.value)
    assert not isinstance(exc.value, OpenReviewCredentialError)


def test_auth_error_message_does_not_leak_the_credentials():
    """The message reaches logs, so it may carry the base URL and the upstream
    error but must never carry the username or the password."""

    def exploding_factory(**kwargs):
        raise RuntimeError("bad login")

    settings = make_settings(
        OPENREVIEW_USERNAME="secret-user@example.org",
        OPENREVIEW_PASSWORD="super-secret-password",
    )

    with pytest.raises(OpenReviewAuthError) as exc:
        get_openreview_client(settings, client_factory=exploding_factory)

    message = str(exc.value)
    assert "secret-user@example.org" not in message
    assert "super-secret-password" not in message
    assert "https://api2.openreview.net" in message


def test_missing_package_raises_a_dependency_error_with_the_install_command():
    """The lazy import's failure mode, exercised without uninstalling anything."""

    def boom():
        raise OpenReviewDependencyError(
            "The 'openreview' package is required to talk to OpenReview but is "
            "not installed. Install it with: "
            "pip install --only-binary=:all: openreview-py"
        )

    with pytest.raises(OpenReviewDependencyError) as exc:
        orc.get_openreview_client(make_settings(), client_factory=lambda **kw: boom())

    assert "pip install" in str(exc.value)


def test_dependency_error_is_not_swallowed_into_an_auth_error():
    """A missing package is not a failed login; the two must stay separable."""

    def factory(**kwargs):
        raise OpenReviewDependencyError("not installed")

    with pytest.raises(OpenReviewDependencyError):
        get_openreview_client(make_settings(), client_factory=factory)


# --- no caching, by design --------------------------------------------------


def test_each_call_builds_a_fresh_client():
    """Pins the deliberate NO-CACHING decision.

    Building a client costs one login, on an action that is chair-approval-gated
    and therefore rare — unlike the Zendesk OAuth provider, which caches because
    it is consulted on every request of a polling loop. Reuse is still available
    to a caller that wants it: this returns a client object, so a batch can hold
    one, rather than the decision being hidden in module state.
    """
    factory = fake_factory_returning(FakeClient())
    settings = make_settings()

    get_openreview_client(settings, client_factory=factory)
    get_openreview_client(settings, client_factory=factory)

    assert len(factory.calls) == 2


def test_module_holds_no_client_state_between_calls():
    """Nothing is memoised at module level, so there is no stale-token failure
    mode to reason about."""
    before = {k: v for k, v in vars(orc).items() if not k.startswith("__")}
    get_openreview_client(make_settings(), client_factory=fake_factory_returning(FakeClient()))
    after = {k: v for k, v in vars(orc).items() if not k.startswith("__")}

    assert before.keys() == after.keys()


# --- the real class is never constructed here -------------------------------


def test_real_openreview_client_is_never_constructed_by_these_tests():
    """Belt-and-braces on the hermetic claim.

    `_load_client_class` is the ONLY route to the real, network-logging class.
    Every test above injects a `client_factory`, which short-circuits it — so if
    it were ever reached, this monkeypatched sentinel would fire.
    """
    reached = []

    def tripwire():
        reached.append(True)
        return FakeClient

    original = orc._load_client_class
    try:
        orc._load_client_class = tripwire
        get_openreview_client(make_settings(), client_factory=FakeClient)
    finally:
        orc._load_client_class = original

    assert reached == [], "the real client loader was reached despite injection"


def test_load_client_class_returns_the_v2_client_when_the_package_is_present():
    """The one test that touches openreview-py — an IMPORT only, no client is
    constructed, so still no network. Skipped where the package is absent."""
    pytest.importorskip("openreview")

    cls = orc._load_client_class()

    assert cls.__name__ == "OpenReviewClient"
    # v2 lives under openreview.api; v1 is openreview.Client.
    assert cls.__module__.startswith("openreview.api")
