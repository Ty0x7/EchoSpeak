import { afterEach, describe, expect, it, vi } from "vitest";
import { LocalVoicePlayback } from "./voiceTransport";

class AudioStub {
  static clips = [];
  constructor(src) { this.src = src; AudioStub.clips.push(this); }
  pause = vi.fn();
  load = vi.fn();
  play = async () => { this.onplay?.(); };
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); AudioStub.clips = []; });

describe("message read-aloud", () => {
  it("releases stopped playback so another message can start without an ended event", async () => {
    vi.stubGlobal("Audio", AudioStub);
    vi.stubGlobal("fetch", vi.fn(async url => String(url).endsWith("/clip.wav")
      ? new Response(new Blob(["audio"]))
      : new Response(JSON.stringify({ turn: { id: "speech-turn" }, audio_url: "/clip.wav" }), { headers: { "Content-Type": "application/json" } })));
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:clip");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const player = new LocalVoicePlayback();
    const scope = { apiBase: "http://localhost:8000", sessionId: "chat-a", projectId: "", clientTurnId: "first", completeTurn: false };
    const first = player.speak("The first message.", scope);
    await vi.waitFor(() => expect(AudioStub.clips).toHaveLength(1));
    player.stop();
    await first;
    expect(AudioStub.clips[0].pause).toHaveBeenCalled();
    const second = player.speak("The next message.", { ...scope, clientTurnId: "second" });
    await vi.waitFor(() => expect(AudioStub.clips).toHaveLength(2));
    AudioStub.clips[1].onended();
    await second;
    expect(revoke).toHaveBeenCalled();
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith("/cancel"))).toBe(true);
    player.stop();
  });
});
