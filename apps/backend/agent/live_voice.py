"""Native Live microphone intake; generated tool calls wait for the ordinary governed turn.

Only transport work happens before a final transcript exists. A short-lived, single-use
handoff binds the buffered first model response/socket to that exact durable voice turn.
No tool is executed by microphone capture or the WebSocket handler.
"""
from __future__ import annotations

import base64
import atexit
import hashlib
import json
import queue
import threading
import time

from agent.lean.provider import ProviderError

_LOCK = threading.RLock()
_PENDING: dict[str, dict] = {}
_CAPTURING: set[str] = set()


def schema_fingerprint(tools: list) -> str:
    return hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest()


def scope_fingerprint(session_id: str, persona, history: list) -> str:
    from agent.state import get_state_store
    from config import config
    state = get_state_store().get_thread_state(session_id)
    payload = {"project": state.active_project_id, "permissions": state.permissions,
               "workspace": state.workspace_id, "workspace_root": state.workspace_root,
               "project_path": state.project_path,
               "constraints": state.constraints, "selected_model": state.selected_model_id,
               "persona": persona.model_dump(mode="json"), "history": history,
               "system_actions": config.enable_system_actions, "generation": config.allow_generation_actions}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class LiveMicrophone:
    def __init__(self, emit):
        self.frames = queue.Queue(maxsize=120)
        self.emit = emit
        self.text = ""
        self.total = 0
        self.started = time.monotonic()
        self.accepting = False

    def ready(self):
        self.accepting = True
        self.emit({"type": "ready"})

    def pause(self):
        self.accepting = False

    def feed(self, data: bytes | None):
        if data is not None:
            self.total += len(data)
            if not data or len(data) % 2 or len(data) > 64000 or self.total > 2_880_000:
                raise ValueError("Microphone audio must be bounded 16 kHz mono PCM16 (up to 90 seconds).")
        try:
            self.frames.put_nowait(data)
        except queue.Full:
            raise ValueError("Live microphone connection is too slow. Stop and try again.") from None

    def transcript(self, text: str):
        self.text = (self.text + text)[:50000]
        self.emit({"type": "transcript", "text": self.text})

    def pump(self, socket):
        if not self.accepting:
            return
        if time.monotonic() - self.started > 110:
            raise ProviderError(504, "Live microphone turn timed out.", "gemini")
        # Drain a bounded batch; receive provider events between batches.
        for _ in range(25):
            try:
                chunk = self.frames.get_nowait()
            except queue.Empty:
                return
            if chunk is None:
                socket.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
            else:
                socket.send(json.dumps({"realtimeInput": {"audio": {"data": base64.b64encode(chunk).decode(), "mimeType": "audio/pcm;rate=16000"}}}))


def discard(voice_id: str):
    with _LOCK:
        entry = _PENDING.pop(voice_id, None)
    if entry:
        entry["timer"].cancel()
        entry["client"].close()


def bind_request(voice_id: str, session_id: str, request_id: str):
    with _LOCK:
        entry = _PENDING.get(voice_id)
        if entry and entry["session"] == session_id:
            if entry.get("request") and entry["request"] != request_id:
                raise ValueError("Live input was already claimed by another request")
            entry["request"] = request_id


def claim_client(session_id: str, request_id: str, endpoint, persona, history: list):
    with _LOCK:
        pair = next(((key, value) for key, value in _PENDING.items()
                     if value["session"] == session_id and value.get("request") == request_id), None)
        if pair is None:
            return None
        voice_id, entry = pair
        _PENDING.pop(voice_id)
    entry["timer"].cancel()
    client = entry["client"]
    try:
        matches = client.endpoint == endpoint and entry["scope"] == scope_fingerprint(session_id, persona, history)
    except Exception:
        client.close()
        raise
    if not matches:
        client.close()
        raise ProviderError(409, "Chat, model or permissions changed during microphone capture. Please speak again.", "gemini")
    return client


def capture(agent, *, session_id: str, **kwargs):
    with _LOCK:
        if session_id in _CAPTURING or any(value["session"] == session_id for value in _PENDING.values()):
            raise ValueError("A Live microphone turn is already active in this chat.")
        if len(_CAPTURING) + len(_PENDING) >= 4:
            raise ValueError("Finish an existing Live microphone turn before starting another.")
        _CAPTURING.add(session_id)
    try:
        return _capture(agent, session_id=session_id, **kwargs)
    finally:
        with _LOCK:
            _CAPTURING.discard(session_id)


@atexit.register
def stop_pending_voice():
    for key in list(_PENDING):
        discard(key)


def _capture(agent, *, session_id: str, project_id: str, client_turn_id: str, microphone: LiveMicrophone, cancel: threading.Event):
    from agent.lean.runtime import LeanSession
    from agent.lean.rooms import get_room_store
    from agent.voice_transport import validate_voice_scope, VoiceTransportTurn, get_voice_transport_store, classify_voice_control
    validate_voice_scope(session_id, project_id)
    room = get_room_store().by_thread(session_id)
    if room and room.kind == "group":
        raise ValueError("Native Live microphone currently supports direct chats. Use local transcription for group chats.")
    session = LeanSession(agent=agent, session_id=session_id, request_id="live-intake-" + client_turn_id,
                          emit=lambda event: None, cancel=cancel, source="voice", room=room)
    members = session._members()
    persona = members[0] if members else session.personas.default()
    history = session._history()
    prepared = session._build_turn(persona, "", history=history, depth=0)
    client = prepared.client
    if client.endpoint.provider != "gemini" or not any(word in client.endpoint.model.lower() for word in ("live", "native-audio")):
        client.close()
        raise ValueError("Choose a Gemini Live model for this chat, or turn Live mic off to use local transcription.")
    if not client.endpoint.api_key:
        client.close()
        raise ValueError("Save your Gemini API key before using Live microphone audio.")
    signature = scope_fingerprint(session_id, persona, history)
    tools = prepared.toolbox.schemas()
    client.live_input, client.live_keep_open, client.live_timeout = microphone, True, 110
    retained = False
    try:
        first = client.stream_turn([{"role": "system", "content": prepared.system_prompt}, *history], tools=tools, cancel=cancel)
        transcript = microphone.text.strip()
        if cancel.is_set() or first.finish_reason == "cancelled":
            raise ValueError("Live capture cancelled")
        if not transcript:
            raise ValueError("No final speech transcript was received. Try again or use local transcription.")
        validate_voice_scope(session_id, project_id)
        turn = VoiceTransportTurn(client_turn_id=client_turn_id, session_id=session_id, project_id=project_id,
                                  transcript=transcript, input_provider_id="gemini-live", status="transcript_ready",
                                  control_hint=classify_voice_control(transcript))
        turn, claimed = get_voice_transport_store().claim_client_turn(turn)
        if not claimed:
            raise ValueError("This microphone turn was already submitted")
        # A stop command never retains generated actions or a paid conversation socket.
        if turn.control_hint == "cancel_active":
            return turn
        client._prefetched_turn = first
        client._prefetched_schema = schema_fingerprint(tools)
        client.live_input = None
        with _LOCK:
            if len(_PENDING) >= 4 or any(value["session"] == session_id for value in _PENDING.values()):
                raise ValueError("A Live voice turn is already waiting for submission. Finish it before recording again.")
            timer = threading.Timer(120, lambda: discard(turn.id))
            timer.daemon = True
            _PENDING[turn.id] = {"client": client, "session": session_id, "scope": signature, "timer": timer}
            timer.start()
        retained = True
        return turn
    finally:
        if not retained:
            client.close()
