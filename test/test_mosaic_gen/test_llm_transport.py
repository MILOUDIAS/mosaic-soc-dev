"""What every provider request must carry, and what a refusal must say.

Two contracts, both learned the same afternoon against opencode.ai:

  * A request that does not identify its client can be refused before it
    reaches any model. The harness's calls came back `403 error code: 1010` --
    a Cloudflare client-signature block -- with no User-Agent, and got a real
    answer from the gateway with one.
  * A refusal that reports only its status code cannot be acted on. The
    gateway's body is the half that says what to do: "requires explicit opt
    in: <url>", "Insufficient balance", "only available hosted in China".
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

import harness.llm as llm


class _Response:
    """Enough of an http response for both call paths."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"{}"


def _capture(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout=None):
        seen.append(request)
        return _Response()

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_every_provider_request_identifies_the_client(monkeypatch):
    seen = _capture(monkeypatch)
    llm._post("https://example.invalid/v1/chat/completions",
              {"Authorization": "Bearer k"}, {"model": "m"})
    llm._request_stream("https://example.invalid/v1/chat/completions",
                        {"Authorization": "Bearer k"}, {"model": "m"}, 10)
    assert len(seen) == 2, "both the blocking and the streaming path"
    for request in seen:
        assert request.get_header("User-agent"), dict(request.headers)


def test_a_caller_can_still_override_the_user_agent(monkeypatch):
    """The default identifies this harness; a caller that must look like
    something else stays in control."""
    seen = _capture(monkeypatch)
    llm._post("https://example.invalid/v1/chat/completions",
              {"User-Agent": "something-else/2"}, {"model": "m"})
    assert seen[0].get_header("User-agent") == "something-else/2"


def test_the_payload_and_content_type_are_unchanged(monkeypatch):
    """The header addition must not disturb what was already being sent."""
    seen = _capture(monkeypatch)
    llm._post("https://example.invalid/v1/chat/completions",
              {"Authorization": "Bearer k"}, {"model": "m", "max_tokens": 16})
    request = seen[0]
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data.decode()) == {"model": "m", "max_tokens": 16}


def test_a_refusal_carries_the_gateways_own_message(monkeypatch):
    """A bare "403 Forbidden" is indistinguishable from an expired key. The
    real refusal named a data-collection opt-in and where to give it."""
    body = (b'{"type":"error","error":{"type":"DataPolicyError","message":'
            b'"This model collects data ... requires explicit opt in: '
            b'https://opencode.ai/workspace/x/go"}}')

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {},
                                     io.BytesIO(body))

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError) as caught:
        llm._post("https://example.invalid/v1/chat/completions",
                  {"Authorization": "Bearer k"}, {"model": "m"})
    message = str(caught.value)
    assert "403" in message
    assert "requires explicit opt in" in message
    assert "DataPolicyError" in message
