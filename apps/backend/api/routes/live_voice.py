"""Authenticated PCM microphone transport. Uses ordinary query/stream after transcription."""
import asyncio
import threading

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ConfigDict

from api.auth import _api_auth_ok, _get_client_ip, _local_request_guard

router = APIRouter()


class LiveVoiceStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(default="", max_length=200)
    client_turn_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


@router.websocket("/media-runtime/voice/live")
async def live_microphone(websocket: WebSocket):
    host = _get_client_ip(websocket)
    if _local_request_guard("POST", websocket.headers.get("host", ""), websocket.headers.get("origin", ""), host) or not _api_auth_ok(websocket.headers, host):
        await websocket.close(code=1008)
        return
    protocols = websocket.headers.get("sec-websocket-protocol", "").split(",")
    await websocket.accept(subprotocol="echospeak" if "echospeak" in [p.strip() for p in protocols] else None)
    from agent.live_voice import LiveMicrophone, capture
    from api.deps import get_agent
    from agent.voice_transport import validate_voice_scope
    from agent.lean.provider import ProviderError
    events = asyncio.Queue(maxsize=256)
    cancel = threading.Event()
    loop = asyncio.get_running_loop()
    worker = None
    finished = False

    def emit(event):
        def put():
            if not events.full():
                events.put_nowait(event)
            else:
                cancel.set()
        loop.call_soon_threadsafe(put)

    microphone = LiveMicrophone(emit)
    try:
        start = LiveVoiceStart.model_validate(await asyncio.wait_for(websocket.receive_json(), timeout=10))
        validate_voice_scope(start.session_id, start.project_id)

        async def run():
            try:
                agent = await asyncio.to_thread(get_agent, start.session_id)
                turn = await asyncio.to_thread(capture, agent, session_id=start.session_id, project_id=start.project_id,
                                               client_turn_id=start.client_turn_id, microphone=microphone, cancel=cancel)
                await events.put({"type": "final", "turn": turn.model_dump(mode="json")})
            except Exception as exc:
                message = str(exc) if isinstance(exc, (ValueError, ProviderError)) else "Live audio could not connect. Check your Gemini key, model access and connection."
                await events.put({"type": "error", "message": message})

        worker = asyncio.create_task(run())

        async def receive():
            while True:
                packet = await websocket.receive()
                if packet["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect()
                if packet.get("bytes") is not None:
                    microphone.feed(packet["bytes"])
                elif packet.get("text") == "end":
                    microphone.feed(None)
                elif packet.get("text") == "cancel":
                    cancel.set()
                    return
                else:
                    raise ValueError("Unexpected microphone frame")

        receiver = asyncio.create_task(receive())
        try:
            while True:
                event_task = asyncio.create_task(events.get())
                done, _ = await asyncio.wait({receiver, event_task}, return_when=asyncio.FIRST_COMPLETED, timeout=120)
                if not done or receiver in done:
                    event_task.cancel()
                    if receiver in done:
                        receiver.result()
                    break
                event = event_task.result()
                await websocket.send_json(event)
                if event["type"] in {"final", "error"}:
                    finished = event["type"] == "final"
                    break
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.send_json({"type": "error", "message": "Microphone input was rejected or the chat changed. Reopen Voice and try again."})
        except Exception:
            pass
    finally:
        if not finished:
            cancel.set()
        if worker:
            # The provider worker observes cancellation; don't leave an unobserved asyncio task.
            try:
                await asyncio.wait_for(asyncio.shield(worker), timeout=3)
            except asyncio.TimeoutError:
                worker.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        try:
            await websocket.close()
        except Exception:
            pass
