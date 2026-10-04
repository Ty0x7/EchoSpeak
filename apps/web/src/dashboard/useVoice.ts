import { useEffect, useRef, useState, type MutableRefObject } from "react";
import { stopTts, sanitizeForTTS, useAppStore } from "../app/runtime";
import { LocalVoiceInput, WakeListener, localVoicePlayback } from "../voiceTransport";
import type { SpeechScope, VoiceTranscript, VoiceTransportPhase } from "../voiceTransport";

/** Mic capture (Ctrl+M), the "Hey Echo" wake listener, read-aloud and voice mode. */
export function useVoice({
  apiBase,
  activeThreadId,
  activeProjectId,
  activeThreadIdRef,
  activeProjectIdRef,
  streaming,
  listening,
  setListening,
  speechEnabled,
  onTranscript,
}: {
  apiBase: string;
  activeThreadId: string;
  activeProjectId: string;
  activeThreadIdRef: MutableRefObject<string>;
  activeProjectIdRef: MutableRefObject<string>;
  streaming: boolean;
  listening: boolean;
  setListening: (value: boolean) => void;
  speechEnabled: boolean;
  onTranscript: (transcript: VoiceTranscript) => Promise<void>;
}) {
  const onTranscriptRef = useRef(onTranscript);
  onTranscriptRef.current = onTranscript;
  const [voiceReadAloud, setVoiceReadAloud] = useState<boolean>(
    () => window.localStorage.getItem("echospeak.voice.read_aloud") === "true",
  );
  const [voiceConversationMode, setVoiceConversationMode] = useState<boolean>(false);
  const [wakeWordEnabled, setWakeWordEnabled] = useState<boolean>(
    () => window.localStorage.getItem("echospeak.voice.wake") === "true",
  );
  const wakeListenerRef = useRef<WakeListener | null>(null);
  const [voicePhase, setVoicePhase] = useState<VoiceTransportPhase>("idle");
  const [voiceNotice, setVoiceNotice] = useState("");
  const [voiceInputLevel, setVoiceInputLevel] = useState(0);
  const voiceInputRef = useRef<LocalVoiceInput | null>(null);
  if (voiceInputRef.current == null) voiceInputRef.current = new LocalVoiceInput();

  useEffect(() => {
    window.localStorage.setItem("echospeak.voice.read_aloud", String(voiceReadAloud));
  }, [voiceReadAloud]);

  const speakLocalText = async (
    text: string,
    metadata: Pick<SpeechScope, "clientTurnId" | "requestId" | "executionId" | "completeTurn">,
  ) => {
    const sessionId = String(activeThreadIdRef.current || "").trim();
    const cleaned = sanitizeForTTS(text);
    if (!speechEnabled || !sessionId || !cleaned) return false;
    setVoiceNotice("");
    try {
      await localVoicePlayback.speak(
        cleaned,
        {
          apiBase,
          sessionId,
          projectId: String(activeProjectIdRef.current || ""),
          ...metadata,
        },
        {
          onPhase: (phase, detail) => {
            setVoicePhase(phase);
            setVoiceNotice(detail || "");
            useAppStore.getState().setSpeaking(phase === "speaking");
          },
          onLevel: (level) => {
            if (level > 0) useAppStore.getState().bumpSpeechBeat();
          },
        },
      );
      return true;
    } catch (error) {
      if ((error as any)?.name === "AbortError") return false;
      setVoicePhase("error");
      setVoiceNotice(error instanceof Error ? error.message : "Local speech playback is unavailable.");
      useAppStore.getState().setSpeaking(false);
      return false;
    }
  };

  const toggleReadAloud = () => {
    const enabled = !voiceReadAloud;
    setVoiceReadAloud(enabled);
    if (!enabled) stopTts();
  };
  const toggleVoiceMode = () => {
    const enabled = !voiceConversationMode;
    setVoiceConversationMode(enabled);
    if (enabled && !streaming && !listening && voicePhase !== "transcribing") void start();
    if (!enabled) {
      void voiceInputRef.current?.stop(false);
      setListening(false);
      stopTts();
      setVoicePhase("idle");
      setVoiceNotice("");
    }
  };

  const toggleWakeWord = () => {
    setWakeWordEnabled((on) => {
      window.localStorage.setItem("echospeak.voice.wake", String(!on));
      if (on) setVoiceNotice("");
      return !on;
    });
  };
  // "Hey Echo": listen only while idle; release the mic during a voice turn,
  // while a reply streams or is read aloud, and when Wake is off.
  const wakeIdle = wakeWordEnabled && !listening && !streaming && (voicePhase === "idle" || voicePhase === "error");
  useEffect(() => {
    if (!wakeIdle) {
      wakeListenerRef.current?.stop();
      return;
    }
    if (!wakeListenerRef.current) wakeListenerRef.current = new WakeListener();
    const listener = wakeListenerRef.current;
    void listener
      .start({
        apiBase,
        onWake: () => {
          listener.stop();
          void start();
        },
        onUnavailable: (message) => {
          listener.stop();
          setWakeWordEnabled(false);
          window.localStorage.setItem("echospeak.voice.wake", "false");
          setVoiceNotice(message);
        },
      })
      .catch((error) => {
        setWakeWordEnabled(false);
        setVoiceNotice(error instanceof Error ? error.message : "The microphone is unavailable.");
      });
    return () => listener.stop();
  }, [wakeIdle, apiBase]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    const sessionId = String(activeThreadIdRef.current || "").trim();
    if (!sessionId || !voiceInputRef.current) {
      setVoicePhase("error");
      setVoiceNotice("Create or select a Session before using Voice.");
      return;
    }
    stopTts();
    setVoiceNotice("");
    try {
      await voiceInputRef.current.start(
        {
          apiBase,
          sessionId,
          projectId: String(activeProjectIdRef.current || ""),
        },
        {
          onPhase: (phase, detail) => {
            setVoicePhase(phase);
            setVoiceNotice(detail || "");
            setListening(phase === "listening" || phase === "requesting_permission");
          },
          onLevel: setVoiceInputLevel,
          onFinalTranscript: (transcript) => {
            setListening(false);
            setVoiceInputLevel(0);
            void onTranscriptRef.current(transcript).catch((error) => {
              setVoicePhase("error");
              setVoiceNotice(error instanceof Error ? error.message : "The spoken instruction could not be applied.");
            });
          },
          onFailure: (error) => {
            setListening(false);
            setVoiceInputLevel(0);
            setVoicePhase("error");
            setVoiceNotice(error.message || "Local transcription is unavailable.");
          },
        },
      );
    } catch (error) {
      setListening(false);
      setVoicePhase("error");
      setVoiceNotice(error instanceof Error ? error.message : "Local microphone capture is unavailable.");
    }
  };

  const stop = async () => {
    if (!voiceInputRef.current) return;
    setListening(false);
    try {
      const transcript = await voiceInputRef.current.stop(true);
      if (!transcript) return;
      await onTranscriptRef.current(transcript);
    } catch (error) {
      setVoicePhase("error");
      setVoiceNotice(error instanceof Error ? error.message : "Local transcription is unavailable.");
    } finally {
      setListening(false);
      setVoiceInputLevel(0);
    }
  };

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!event.ctrlKey || event.key.toLowerCase() !== "m") return;
      event.preventDefault();
      if (voiceInputRef.current?.active) void stop();
      else void start();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  });

  useEffect(() => {
    if (voiceInputRef.current?.active) void voiceInputRef.current.stop(false);
    setListening(false);
    setVoiceInputLevel(0);
    setVoicePhase("idle");
    setVoiceNotice("");
    stopTts();
  }, [activeThreadId, activeProjectId]);

  useEffect(() => {
    const listener = () => {
      void voiceInputRef.current?.stop(false);
      stopTts();
    };
    window.addEventListener("beforeunload", listener);
    return () => window.removeEventListener("beforeunload", listener);
  }, []);

  return {
    voicePhase, setVoicePhase, voiceNotice, setVoiceNotice, voiceInputLevel,
    voiceReadAloud, voiceConversationMode, wakeWordEnabled,
    toggleReadAloud, toggleVoiceMode, toggleWakeWord, start, stop, speakLocalText,
  };
}
