import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { LiveAudioPlayback } from "./liveVoiceTransport";

class AudioContextStub {
  static sources = [];
  state = "running";
  currentTime = 10;
  destination = {};
  resume = async () => undefined;
  createBuffer(_channels, length, rate) {
    return { duration: length / rate, getChannelData: () => new Float32Array(length) };
  }
  createBufferSource() {
    const source = { buffer: null, onended: null, start: vi.fn(), stop: vi.fn(), connect: vi.fn(), disconnect: vi.fn() };
    AudioContextStub.sources.push(source);
    return source;
  }
}
afterEach(() => { vi.unstubAllGlobals(); AudioContextStub.sources = []; });

describe("native Live audio", () => {
  it("queues PCM in order and stops queued speech when interrupted", async () => {
    vi.stubGlobal("AudioContext", AudioContextStub);
    const player = new LiveAudioPlayback();
    const speaking = vi.fn();
    const packet = { kind: "pcm", data: "AAA=", mime_type: "audio/pcm;rate=24000" };
    await player.enqueue(packet, speaking);
    await player.enqueue(packet, speaking);
    const [first, second] = AudioContextStub.sources;
    expect(second.start.mock.calls[0][0]).toBeGreaterThan(first.start.mock.calls[0][0]);
    const finished = player.finished();
    await player.enqueue({ kind: "interrupted" }, speaking);
    expect(first.stop).toHaveBeenCalled(); expect(second.stop).toHaveBeenCalled();
    expect(await finished).toBe(false);
    expect(speaking).toHaveBeenLastCalledWith(false);
  });
  it("rejects unsupported audio instead of decoding it as PCM", async () => {
    vi.stubGlobal("AudioContext", AudioContextStub);
    const player = new LiveAudioPlayback();
    expect(await player.enqueue({ kind: "pcm", data: "AAA=", mime_type: "audio/mp3" }, vi.fn())).toBe(false);
    expect(AudioContextStub.sources).toHaveLength(0);
  });
  it("resamples real microphone frames to 16 kHz PCM on the worklet", () => {
    let Processor;
    const frames = [];
    runInNewContext(readFileSync("src/liveMic.worklet.js", "utf8"), {
      AudioWorkletProcessor: class { port = { postMessage: (data) => frames.push(data) }; },
      sampleRate: 48000,
      registerProcessor: (_name, cls) => { Processor = cls; },
      Int16Array, Math,
    });
    const processor = new Processor();
    for (let i = 0; i < 8; i++) processor.process([[new Float32Array(128).fill(0.5)]]);
    expect(frames).toHaveLength(1);
    const pcm = new Int16Array(frames[0]);
    expect(pcm).toHaveLength(320);
    expect(pcm[0]).toBe(16384);
  });
});
