import { createEchoSpeakWebSocket } from "./desktop/bridge";
import liveMicWorkletUrl from "./liveMic.worklet.js?no-inline&url";
import type { VoiceTranscript, VoiceTransportPhase } from "./voiceTransport";

type Callbacks = {
  onPhase?(phase: VoiceTransportPhase, detail?: string): void;
  onLevel?(level: number): void;
  onFinalTranscript?(transcript: VoiceTranscript): void;
  onFailure?(error: Error): void;
  onSpeechStart?(): void;
};
type Scope = { apiBase: string; sessionId: string; projectId: string };
export type LiveAudioPacket = { kind: "pcm" | "interrupted"; data?: string; mime_type?: string };

export class LiveAudioPlayback {
  private context: AudioContext | null = null;
  private nextTime = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private epoch = 0;
  private waiters: ((played: boolean) => void)[] = [];
  private onSpeaking: (value: boolean) => void = () => undefined;
  async unlock() {
    this.context ??= new AudioContext({ sampleRate: 24000 });
    await this.context.resume();
  }
  async enqueue(packet: LiveAudioPacket, onSpeaking: (value: boolean) => void): Promise<boolean> {
    if (packet.kind === "interrupted") { this.stop(); return false; }
    if (!packet.data || !/^audio\/pcm(?:;|$)/.test(packet.mime_type || "") || !/(?:^|;)rate=24000(?:;|$)/.test(packet.mime_type || "")) return false;
    const epoch = this.epoch;
    await this.unlock();
    if (epoch !== this.epoch) return false;
    const context = this.context!;
    if (context.state !== "running") throw new Error("Click Live mic or Read to enable browser audio playback.");
    if (packet.data.length > 4_000_000 || this.nextTime - context.currentTime > 120) throw new Error("Live audio playback exceeded the queue limit.");
    const bytes = Uint8Array.from(atob(packet.data), value => value.charCodeAt(0));
    if (!bytes.length || bytes.length % 2) return false;
    const buffer = context.createBuffer(1, bytes.length / 2, 24000);
    const view = new DataView(bytes.buffer);
    const samples = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    this.onSpeaking = onSpeaking;
    this.sources.add(source);
    this.onSpeaking(true);
    source.onended = () => {
      this.sources.delete(source);
      source.disconnect();
      if (!this.sources.size && epoch === this.epoch) {
        this.onSpeaking(false);
        this.waiters.splice(0).forEach(resolve => resolve(true));
      }
    };
    const at = Math.max(context.currentTime + 0.025, this.nextTime);
    source.start(at);
    this.nextTime = at + buffer.duration;
    return true;
  }
  async finished(): Promise<boolean> {
    if (!this.sources.size) return true;
    return new Promise(resolve => this.waiters.push(resolve));
  }
  stop() {
    this.epoch++;
    for (const source of this.sources) { try { source.stop(); } catch { /* already ended */ } }
    this.sources.clear();
    this.nextTime = 0;
    this.onSpeaking(false);
    this.waiters.splice(0).forEach(resolve => resolve(false));
  }
}
export const liveAudioPlayback = new LiveAudioPlayback();

export class NativeLiveInput {
  active = false;
  private socket: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private callbacks: Callbacks = {};
  private result: Promise<VoiceTranscript | null> = Promise.resolve(null);
  private resolveResult: ((value: VoiceTranscript | null) => void) | null = null;
  private manualStop = false;
  private generation = 0;
  private succeeded = false;

  async start(scope: Scope, callbacks: Callbacks) {
    await this.stop(false);
    const generation = ++this.generation;
    this.callbacks = callbacks;
    this.manualStop = false;
    this.succeeded = false;
    this.result = new Promise(resolve => { this.resolveResult = resolve; });
    callbacks.onPhase?.("requesting_permission", "Live microphone sends audio to Google; API charges may apply.");
    this.active = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }, video: false });
      if (generation !== this.generation) { stream.getTracks().forEach(track => track.stop()); return; }
      this.stream = stream;
      await liveAudioPlayback.unlock();
      this.context = new AudioContext();
      await this.context.resume();
      await this.context.audioWorklet.addModule(liveMicWorkletUrl);
      if (generation !== this.generation) return;
      const context = this.context;
      const socket = createEchoSpeakWebSocket(`${scope.apiBase.replace(/^http/, "ws")}/media-runtime/voice/live`);
      this.socket = socket;
      const clientTurnId = crypto.randomUUID();
      let captureStarted = false;
      let speechFrames = 0;
      let speechStarted = false;
      const timeout = window.setTimeout(() => { if (!captureStarted) fail(new Error("Live microphone connection timed out.")); }, 30000);
      const fail = (error: Error) => {
        if (generation !== this.generation || this.succeeded) return;
        window.clearTimeout(timeout);
        this.resolveResult?.(null);
        callbacks.onPhase?.("error", error.message);
        callbacks.onFailure?.(error);
        void this.stop(false);
      };
      socket.onopen = () => socket.send(JSON.stringify({ session_id: scope.sessionId, project_id: scope.projectId, client_turn_id: clientTurnId }));
      socket.onerror = () => fail(new Error("Live microphone could not connect. Check the API key and selected Gemini Live model."));
      socket.onclose = () => { if (!this.succeeded && generation === this.generation) fail(new Error("Live microphone disconnected before the turn completed.")); };
      socket.onmessage = event => {
        if (generation !== this.generation) return;
        try {
          const packet = JSON.parse(event.data);
          if (packet.type === "ready" && !captureStarted) {
            captureStarted = true;
            window.clearTimeout(timeout);
            const source = context.createMediaStreamSource(stream);
            const node = new AudioWorkletNode(context, "echospeak-live-microphone");
            this.node = node;
            const mute = context.createGain(); mute.gain.value = 0;
            source.connect(node); node.connect(mute); mute.connect(context.destination);
            node.port.onmessage = audio => {
              if (!this.active || generation !== this.generation || socket.readyState !== WebSocket.OPEN) return;
              if (socket.bufferedAmount > 256000) { fail(new Error("Live microphone connection is too slow. Try again.")); return; }
              const samples = new Int16Array(audio.data);
              const level = Math.sqrt(samples.reduce((sum, value) => sum + (value / 32768) ** 2, 0) / samples.length);
              speechFrames = level > 0.025 ? speechFrames + 1 : 0;
              if (!speechStarted && speechFrames >= 5) { speechStarted = true; callbacks.onSpeechStart?.(); }
              callbacks.onLevel?.(Math.min(1, level * 4));
              socket.send(audio.data);
            };
            callbacks.onPhase?.("listening", "Listening through Gemini Live");
          } else if (packet.type === "final") {
            this.succeeded = true;
            const turn = packet.turn;
            const transcript: VoiceTranscript = { text: turn.transcript, voiceTurnId: turn.id, clientTurnId,
              sessionId: scope.sessionId, projectId: scope.projectId, providerId: "gemini-live", language: "", controlHint: turn.control_hint };
            this.resolveResult?.(transcript);
            const manual = this.manualStop;
            this.releaseMic();
            this.active = false;
            callbacks.onPhase?.("ready", "Live transcript ready");
            if (!manual) callbacks.onFinalTranscript?.(transcript);
            socket.close();
          } else if (packet.type === "error") fail(new Error(packet.message));
        } catch { fail(new Error("Invalid Live microphone response.")); }
      };
    } catch (error) {
      await this.stop(false);
      throw error;
    }
  }
  private releaseMic() {
    this.node?.disconnect(); this.node = null;
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = null;
    if (this.context) void this.context.close(); this.context = null;
    this.callbacks.onLevel?.(0);
  }
  async stop(submit: boolean): Promise<VoiceTranscript | null> {
    this.releaseMic();
    this.active = false;
    if (submit && this.socket?.readyState === WebSocket.OPEN) {
      this.manualStop = true;
      this.socket.send("end");
      this.callbacks.onPhase?.("transcribing", "Finishing Live input…");
      return this.result;
    }
    this.generation++;
    this.resolveResult?.(null);
    this.resolveResult = null;
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send("cancel");
    this.socket?.close(); this.socket = null;
    return null;
  }
}
