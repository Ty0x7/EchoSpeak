"""Signed Agent Cards (A2A 1.0 §8.4) and outbound A2A only to agents the owner pinned."""
import json
from urllib.parse import urlparse

import pytest

from agent import a2a_trust
from agent.a2a import A2ATaskManager

REMOTE = "https://agent.example.com"


def test_canonical_json_follows_rfc_8785():
    assert a2a_trust.jcs({"b": 2, "a": [1, 2.0, "é"], "c": {"z": None, "y": True}}) == '{"a":[1,2,"é"],"b":2,"c":{"y":true,"z":null}}'
    assert a2a_trust.without_defaults({"a": "", "b": 0, "c": False, "d": [], "e": {"f": None}, "g": True, "h": "x"}) == {"g": True, "h": "x"}


def test_a_signed_card_verifies_and_any_change_breaks_it():
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    jwk = a2a_trust.public_jwk(key)
    card = a2a_trust.sign_card({"name": "Remote", "skills": [{"id": "a", "name": "Research"}]}, key=key)
    assert a2a_trust.verify_card(card, [jwk]) == jwk
    assert a2a_trust.verify_card(card, [jwk], pinned=a2a_trust.thumbprint(jwk)) == jwk
    assert a2a_trust.verify_card(card, [jwk], pinned="someone-else") is None
    assert a2a_trust.verify_card({**card, "name": "Impostor"}, [jwk]) is None
    other = a2a_trust.public_jwk(ec.generate_private_key(ec.SECP256R1()))
    assert a2a_trust.verify_card(card, [{**other, "kid": jwk["kid"]}]) is None  # same kid, wrong key


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """A second EchoSpeak at agent.example.com, reached through the real routes."""
    from fastapi.testclient import TestClient
    from agent import credential_broker, privacy
    from api.server import app
    from config import config
    broker = credential_broker.CredentialBroker(tmp_path / "credentials")
    monkeypatch.setattr(credential_broker, "get_credential_broker", lambda: broker)
    monkeypatch.setattr(a2a_trust, "_folder", lambda: tmp_path)
    monkeypatch.setattr(privacy, "require", lambda component, target: None)
    monkeypatch.setattr(config, "a2a_enabled", True, raising=False)
    monkeypatch.setattr(config, "a2a_auth_key", "remote-secret", raising=False)
    asked = []

    def process(self, task):
        from agent.a2a import TaskState
        asked.append(task.messages[0].text)
        self.update_status(task.id, TaskState.WORKING)
        self.add_agent_message(task.id, "The capital of Australia is Canberra.")
        return self.update_status(task.id, TaskState.COMPLETED)
    monkeypatch.setattr(A2ATaskManager, "process_task", process)
    client = TestClient(app)

    class Response:
        def __init__(self, r): self.r, self.status_code = r, r.status_code
        def raise_for_status(self): self.r.raise_for_status()
        def json(self): return self.r.json()

    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, **kw: Response(client.get(urlparse(url).path)))
    monkeypatch.setattr(httpx, "post", lambda url, json=None, headers=None, **kw: Response(
        client.post(urlparse(url).path, json=json, headers=headers)))
    return asked


def test_inspect_pin_and_ask_another_echo(remote):
    info = a2a_trust.inspect_agent(REMOTE)
    assert info["name"] and info["fingerprint"]
    a2a_trust.trust(info, token="remote-secret")
    assert [a["url"] for a in a2a_trust.trusted_agents()] == [REMOTE]
    assert "token_ref" in a2a_trust.trusted_agents()[0]  # the token sits in the broker
    reply = a2a_trust.ask(a2a_trust.trusted_agents()[0], "What's the capital of Australia?")
    assert reply == "The capital of Australia is Canberra."
    assert remote == ["What's the capital of Australia?"]


def test_a_new_signing_key_is_refused_until_the_owner_reviews_it(remote, tmp_path):
    a2a_trust.trust(a2a_trust.inspect_agent(REMOTE), token="remote-secret")
    (tmp_path / "signing.json").unlink()  # the remote agent's key changes (here: a fresh one is made)
    with pytest.raises(ValueError, match="no longer signed by the key you approved"):
        a2a_trust.ask(a2a_trust.trusted_agents()[0], "hello")
    assert remote == []  # nothing was sent


def test_a_card_cannot_point_its_trust_at_another_sites_keys(remote, monkeypatch):
    real = a2a_trust._get_json

    def tampered(url):
        data = real(url)
        if url.endswith(a2a_trust.CARD_PATH):
            header = json.loads(a2a_trust.b64url_decode(data["signatures"][0]["protected"]))
            header["jku"] = "https://keys.attacker.example/jwks.json"
            data["signatures"][0]["protected"] = a2a_trust.b64url(json.dumps(header).encode())
        return data
    monkeypatch.setattr(a2a_trust, "_get_json", tampered)
    with pytest.raises(ValueError, match="isn't signed"):
        a2a_trust.inspect_agent(REMOTE)


def test_the_tool_appears_only_with_a_pinned_agent_and_counts_as_outside_content(remote):
    from agent.lean import policy
    from agent.lean.approvals import tool_needs_approval
    assert a2a_trust.tool() == []
    a2a_trust.trust(a2a_trust.inspect_agent(REMOTE), token="remote-secret")
    [tool] = a2a_trust.tool()
    assert tool.name == "ask_remote_agent"
    assert policy.is_untrusted_source("ask_remote_agent") and policy.is_external_action("ask_remote_agent")
    assert tool_needs_approval(None, "ask_remote_agent", {})[0]
