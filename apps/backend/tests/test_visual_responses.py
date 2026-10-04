"""Visual responses: tool cards (widgets), artifacts and their sandbox, the image proxy."""

from __future__ import annotations

import json
import threading

import pytest

from agent.lean import artifacts, widgets
from agent.lean.rich_tools import _product_from_jsonld, _product_from_walmart
import api.auth as api_auth


# ── widgets ───────────────────────────────────────────────────────────────

def test_widgets_drop_unknown_types_and_unsafe_urls():
    assert widgets.normalize({"type": "nope", "data": {}}) is None
    assert widgets.normalize("not a dict") is None
    card = widgets.normalize({"type": "product_carousel", "data": {"items": [
        {"title": "Pad", "url": "javascript:alert(1)", "price": 10},
        {"title": "Good pad", "url": "https://shop.example/p/1", "price": "$24.99", "image": "data:image/png;base64,xx", "rating": 9},
    ]}})
    assert card is not None and len(card["data"]["items"]) == 1
    item = card["data"]["items"][0]
    assert item["price"] == 24.99 and item["image"] == "" and item["rating"] is None
    assert widgets.normalize({"type": "product_carousel", "data": {"items": [{"title": "x", "url": "https://a.b", "price": None}]}}) is None


def test_chart_needs_labels_and_numbers():
    assert widgets.normalize({"type": "chart", "data": {"labels": [], "series": []}}) is None
    good = widgets.normalize({"type": "chart", "data": {"kind": "sparkle", "labels": ["a", "b"], "series": [{"name": "s", "values": [1, "2"]}]}})
    assert good["data"]["kind"] == "line" and good["data"]["series"][0]["values"] == [1.0, 2.0]


def test_attach_only_inside_a_tool_call_and_capped():
    widgets.attach({"type": "citations", "data": {"items": [{"url": "https://a.example"}]}})  # outside: ignored, no error

    def tool():
        for i in range(10):
            widgets.attach({"type": "citations", "data": {"items": [{"url": f"https://a{i}.example"}]}})
        return "done"

    result, cards = widgets.collect(tool)
    assert result == "done" and len(cards) == widgets.MAX_WIDGETS_PER_CALL


def test_loop_sends_widgets_with_the_tool_result(monkeypatch):
    from agent.lean.loop import LeanTurn
    from agent.lean.personas import AgentPersona
    from agent.lean.provider import ModelTurn, ToolCall
    from agent.lean.toolbox import NativeTool, Toolbox
    from tests.test_lean_runtime import ScriptedClient

    def forecast(_args):
        widgets.attach({"type": "weather", "data": {"location": "Denver", "current": {"temp": 60, "code": 0}}})
        return "60F and clear"

    events: list[dict] = []
    client = ScriptedClient([
        ModelTurn(tool_calls=[ToolCall(id="c1", name="forecast", arguments="{}")]),
        ModelTurn(content="It is 60F and clear in Denver."),
    ])
    box = Toolbox(toolsets=["forecast"], extra_tools=[NativeTool(name="forecast", description="d", parameters={"type": "object", "properties": {}}, func=forecast)])
    turn = LeanTurn(
        client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="s", history=[], toolbox=box,
        session_id="s1", request_id="r", execution_id="e", emit=events.append, cancel=threading.Event(), persist_tool_runs=False,
    )
    result = turn.run("weather?")
    end = next(e for e in events if e["type"] == "tool_end")
    assert end["widgets"][0]["type"] == "weather"
    row = next(r for r in result.timeline if r.get("kind") == "tool")
    assert row["widgets"][0]["data"]["location"] == "Denver"  # kept for reload


# ── product page parsing ─────────────────────────────────────────────────

def test_products_are_read_from_schema_org_and_walmart_data():
    html = '<script type="application/ld+json">{"@type": "Product", "name": "Pad", "image": ["https://img/1.jpg"], "offers": {"price": "29.99", "priceCurrency": "USD"}, "aggregateRating": {"ratingValue": 4.5, "reviewCount": 12}}</script>'
    assert _product_from_jsonld(html) == {"title": "Pad", "price": "29.99", "currency": "USD", "image": "https://img/1.jpg", "rating": 4.5, "reviews": 12}
    walmart = {"props": {"pageProps": {"initialData": {"data": {"product": {
        "name": "Controller", "priceInfo": {"currentPrice": {"price": 39.0, "currencyUnit": "USD"}},
        "imageInfo": {"thumbnailUrl": "https://i5.walmartimages.com/x.jpg"}, "averageRating": 4.2, "numberOfReviews": 80}}}}}}
    parsed = _product_from_walmart(f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(walmart)}</script>')
    assert parsed["price"] == 39.0 and parsed["title"] == "Controller" and parsed["reviews"] == 80
    assert _product_from_jsonld("<html>no data</html>") is None


# ── artifacts ─────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def test_artifact_versions_edit_and_restore(store):
    record = artifacts.create(title="Tip calculator", kind="html", content="```html\n<p>Tip: 15%</p>\n```", session_id="s1")
    assert record["versions"][0]["content"] == "<p>Tip: 15%</p>"  # fence stripped
    artifacts.update(record["id"], edits=[{"find": "15%", "replace": "20%"}])
    with pytest.raises(ValueError):
        artifacts.update(record["id"], edits=[{"find": "missing", "replace": "x"}])
    v3 = artifacts.restore(record["id"], 1)
    assert [v["n"] for v in v3["versions"]] == [1, 2, 3]
    assert v3["versions"][-1]["content"] == "<p>Tip: 15%</p>" and "Restored" in v3["versions"][-1]["note"]
    assert artifacts.list_all("s1")[0]["versions"] == 3 and artifacts.list_all("other") == []


def test_artifact_tools_announce_a_card_and_find_the_latest(store):
    from agent.tools import _tool_execution_context

    token = _tool_execution_context.set({"session_id": "s9"})
    try:
        out, cards = widgets.collect(lambda: artifacts.create_artifact_tool({"title": "Demo", "type": "html", "content": "<b>hi</b>"}))
        assert "Created artifact" in out and cards[0]["type"] == "artifact" and cards[0]["data"]["version"] == 1
        out, cards = widgets.collect(lambda: artifacts.update_artifact_tool({"content": "<b>hello</b>"}))  # no id given
        assert "version 2" in out and cards[0]["data"]["version"] == 2
    finally:
        _tool_execution_context.reset(token)


def test_artifact_frames_are_sandboxed_and_need_a_token(store, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routes.lean import router

    record = artifacts.create(title="App", kind="html", content="<html><head><title>x</title></head><body><script>fetch('http://evil')</script></body></html>")
    page = artifacts.frame_html(record)
    assert page.index("Content-Security-Policy") < page.index("<title>")  # CSP comes first in <head>
    assert "connect-src 'none'" in artifacts.FRAME_CSP and "allow-same-origin" not in artifacts.FRAME_CSP and "allow-popups" not in artifacts.FRAME_CSP

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.get(f"/lean/artifacts/{record['id']}/frame")
    assert response.headers["content-security-policy"].startswith("sandbox allow-scripts")
    token = client.post(f"/lean/artifacts/{record['id']}/frame-token").json()["token"]
    assert artifacts.frame_token_ok(record["id"], token)
    assert not artifacts.frame_token_ok("0" * 12, token)  # one artifact only
    markdown = artifacts.create(title="Doc", kind="markdown", content="# hi")
    assert client.get(f"/lean/artifacts/{markdown['id']}/frame").status_code == 404  # only html/svg run in frames


def test_server_lets_a_frame_token_through_only_for_its_frame(store, monkeypatch):
    from fastapi.testclient import TestClient

    from api import server

    monkeypatch.setattr(server.config, "api_auth_enabled", True, raising=False)
    monkeypatch.setattr(server.config, "api_auth_localhost_bypass", False, raising=False)
    monkeypatch.setattr(api_auth, "_configured_api_auth_key", lambda: "secret-key")
    record = artifacts.create(title="App", kind="html", content="<p>x</p>")
    token = artifacts.issue_frame_token(record["id"])
    client = TestClient(server.app)
    assert client.get(f"/lean/artifacts/{record['id']}/frame").status_code == 401
    assert client.get(f"/lean/artifacts/{record['id']}/frame?t={token}").status_code == 200
    assert client.get(f"/lean/artifacts/{record['id']}?t={token}").status_code == 401  # token is for the frame only


# ── media proxy ───────────────────────────────────────────────────────────

def test_media_proxy_rejects_bad_urls_and_non_images(store, monkeypatch):
    from agent.lean import media_proxy

    with pytest.raises(media_proxy.MediaError) as err:
        media_proxy.fetch_image("file:///etc/passwd")
    assert err.value.status == 400

    def fake_fetch(url, **_kwargs):
        if "page" in url:
            return url, {"content-type": "text/html"}, b"<html>"
        return url, {"content-type": "application/octet-stream"}, b"\x89PNG\r\n\x1a\n" + b"0" * 32

    monkeypatch.setattr("agent.safe_web_retrieval.fetch_public_bytes", fake_fetch)
    with pytest.raises(media_proxy.MediaError) as err:
        media_proxy.fetch_image("https://example.com/page")
    assert err.value.status == 415
    body, kind = media_proxy.fetch_image("https://example.com/pic")
    assert kind == "image/png" and body.startswith(b"\x89PNG")
    assert media_proxy.media_response_headers("image/svg+xml")["Content-Security-Policy"].endswith("sandbox")


# ── SOUL.md default refresh ──────────────────────────────────────────────

def test_unedited_old_soul_is_replaced_but_edited_one_is_kept(tmp_path, monkeypatch):
    import subprocess

    from agent.lean.soul_defaults import refresh_default_soul

    bundled = tmp_path / "bundled.md"
    bundled.write_text("# new soul", encoding="utf-8")
    target = tmp_path / "data" / "SOUL.md"
    assert refresh_default_soul(bundled, target) == "installed"
    old = subprocess.run(["git", "show", "bf51e80:apps/backend/SOUL.md"], capture_output=True).stdout.decode("utf-8")
    if old:
        target.write_bytes(old.replace("\n", "\r\n").encode("utf-8"))  # CRLF, as on Windows
        assert refresh_default_soul(bundled, target) == "updated"
        assert target.read_text(encoding="utf-8") == "# new soul"
    target.write_text("# my own soul", encoding="utf-8")
    assert refresh_default_soul(bundled, target) == "kept"
