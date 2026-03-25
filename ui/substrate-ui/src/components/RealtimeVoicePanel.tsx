"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Mic, MicOff, Phone, PhoneOff, Volume2 } from "lucide-react";
import { api } from "@/lib/api";

interface RealtimeVoicePanelProps {
  /** Whether the panel is currently open/visible. */
  isOpen: boolean;
  /** Called to close the panel. */
  onClose: () => void;
}

type SessionState =
  | "idle"           // not started
  | "connecting"     // fetching token + opening WS
  | "ready"          // WS open, waiting for user to speak
  | "listening"      // VAD detected speech
  | "speaking"       // AI is playing audio response
  | "error";         // unrecoverable error

// Auto-close the session after this many ms of inactivity (no speech detected).
const IDLE_CLOSE_MS = 90_000;

// Backend WS proxy URL — Next.js rewrites /api/audio/realtime-ws → FastAPI ws://.../audio/realtime
// Falls back to using the env var WS URL directly (e.g. in Docker with SSE proxy disabled).
const WS_PROXY_URL =
  typeof window !== "undefined"
    ? `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/api/audio/realtime-ws`
    : (process.env.NEXT_PUBLIC_WS_URL
        ? `${process.env.NEXT_PUBLIC_WS_URL}/audio/realtime`
        : "ws://localhost:8000/audio/realtime");

export function RealtimeVoicePanel({ isOpen, onClose }: RealtimeVoicePanelProps) {
  const [sessionState, setSessionState] = useState<SessionState>("idle");
  const [statusText, setStatusText] = useState("Press to start");
  const [error, setError] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<string[]>([]); // running transcript lines

  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const audioQueueRef = useRef<ArrayBuffer[]>([]);
  const isPlayingRef = useRef(false);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const transcriptRef = useRef<string[]>([]);

  // ── Cleanup ──────────────────────────────────────────────────────────────
  const teardown = useCallback((reason?: string) => {
    if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    idleTimerRef.current = null;

    processorRef.current?.disconnect();
    processorRef.current = null;

    mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
    mediaStreamRef.current = null;

    if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) {
      wsRef.current.close(1000, reason ?? "user closed");
    }
    wsRef.current = null;

    audioCtxRef.current?.close().catch(() => {});
    audioCtxRef.current = null;
    audioQueueRef.current = [];
    isPlayingRef.current = false;
  }, []);

  // Teardown when the panel is closed externally
  useEffect(() => {
    if (!isOpen) {
      teardown("panel closed");
      setSessionState("idle");
      setTranscript([]);
      transcriptRef.current = [];
      setError(null);
      setStatusText("Press to start");
    }
  }, [isOpen, teardown]);

  // Cleanup on unmount
  useEffect(() => {
    return () => teardown("unmount");
  }, [teardown]);

  // ── Idle auto-close timer ────────────────────────────────────────────────
  const resetIdleTimer = useCallback(() => {
    if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    idleTimerRef.current = setTimeout(() => {
      setStatusText("Session timed out due to inactivity");
      teardown("idle timeout");
      setSessionState("idle");
    }, IDLE_CLOSE_MS);
  }, [teardown]);

  // ── PCM16 encoding helper ────────────────────────────────────────────────
  function floatTo16BitPCM(floatArr: Float32Array): ArrayBuffer {
    const buf = new ArrayBuffer(floatArr.length * 2);
    const view = new DataView(buf);
    for (let i = 0; i < floatArr.length; i++) {
      const s = Math.max(-1, Math.min(1, floatArr[i]));
      view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return buf;
  }

  function arrayBufferToBase64(buf: ArrayBuffer): string {
    const bytes = new Uint8Array(buf);
    let binary = "";
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  }

  // ── Play queued PCM16 audio chunks (24kHz, mono) ─────────────────────────
  const drainAudioQueue = useCallback(async () => {
    if (isPlayingRef.current) return;
    if (audioQueueRef.current.length === 0) {
      setSessionState("ready");
      setStatusText("Listening… speak now");
      return;
    }

    isPlayingRef.current = true;
    setSessionState("speaking");
    setStatusText("AI is speaking…");

    const ctx = audioCtxRef.current;
    if (!ctx) {
      isPlayingRef.current = false;
      return;
    }

    while (audioQueueRef.current.length > 0) {
      const pcmBuf = audioQueueRef.current.shift()!;
      const int16 = new Int16Array(pcmBuf);
      const float32 = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;

      const audioBuf = ctx.createBuffer(1, float32.length, 24000);
      audioBuf.getChannelData(0).set(float32);

      await new Promise<void>((resolve) => {
        const src = ctx.createBufferSource();
        src.buffer = audioBuf;
        src.connect(ctx.destination);
        src.onended = () => resolve();
        src.start();
      });
    }

    isPlayingRef.current = false;
    setSessionState("ready");
    setStatusText("Listening… speak now");
  }, []);

  // ── Session start ────────────────────────────────────────────────────────
  const startSession = useCallback(async () => {
    setError(null);
    setSessionState("connecting");
    setStatusText("Connecting…");
    setTranscript([]);
    transcriptRef.current = [];

    // 1. Get ephemeral token
    let token: Awaited<ReturnType<typeof api.getRealtimeToken>>;
    try {
      token = await api.getRealtimeToken();
    } catch (err) {
      setError("Failed to get session token");
      setSessionState("error");
      return;
    }

    // 2. Open mic
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("Microphone access denied");
      setSessionState("error");
      return;
    }
    mediaStreamRef.current = stream;

    // 3. Open WS proxy → OpenAI Realtime
    const wsUrl = WS_PROXY_URL;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      // Send auth handshake
      ws.send(JSON.stringify({ type: "auth", client_secret: token.client_secret }));
    };

    ws.onclose = (e) => {
      if (e.code !== 1000) {
        setError(`Connection closed: ${e.reason || e.code}`);
        setSessionState("error");
      } else {
        setSessionState("idle");
        setStatusText("Session ended");
      }
      teardown();
    };

    ws.onerror = () => {
      setError("WebSocket error — check console");
      setSessionState("error");
      teardown();
    };

    ws.onmessage = (evt) => {
      resetIdleTimer();
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(evt.data as string);
      } catch {
        return;
      }

      const type = msg.type as string;

      if (type === "session.created" || type === "session.updated") {
        setSessionState("ready");
        setStatusText("Listening… speak now");
      } else if (type === "input_audio_buffer.speech_started") {
        setSessionState("listening");
        setStatusText("Listening…");
      } else if (type === "input_audio_buffer.speech_stopped") {
        setStatusText("Processing…");
      } else if (type === "response.audio.delta") {
        // Base64 PCM16 audio chunk
        const b64 = msg.delta as string;
        if (b64) {
          const binary = atob(b64);
          const buf = new ArrayBuffer(binary.length);
          const view = new Uint8Array(buf);
          for (let i = 0; i < binary.length; i++) view[i] = binary.charCodeAt(i);
          audioQueueRef.current.push(buf);
          drainAudioQueue();
        }
      } else if (type === "response.audio_transcript.delta") {
        const text = msg.delta as string;
        if (text) {
          const lines = [...transcriptRef.current];
          if (lines.length === 0 || !lines[lines.length - 1].startsWith("AI: ")) {
            lines.push("AI: " + text);
          } else {
            lines[lines.length - 1] += text;
          }
          transcriptRef.current = lines;
          setTranscript([...lines]);
        }
      } else if (type === "conversation.item.input_audio_transcription.completed") {
        const text = msg.transcript as string;
        if (text) {
          const lines = [...transcriptRef.current, `You: ${text}`];
          transcriptRef.current = lines;
          setTranscript([...lines]);
        }
      } else if (type === "error") {
        const errMsg = (msg.error as Record<string, unknown>)?.message ?? "Unknown error";
        setError(String(errMsg));
        setSessionState("error");
      }
    };

    // 4. Set up AudioContext + ScriptProcessor for mic capture
    const ctx = new AudioContext({ sampleRate: 24000 });
    audioCtxRef.current = ctx;
    await ctx.resume();

    const source = ctx.createMediaStreamSource(stream);
    const processor = ctx.createScriptProcessor(4096, 1, 1);
    processorRef.current = processor;

    processor.onaudioprocess = (e) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;
      if (sessionState === "speaking") return; // don't send mic when AI is talking
      const pcm = floatTo16BitPCM(e.inputBuffer.getChannelData(0));
      wsRef.current.send(
        JSON.stringify({
          type: "input_audio_buffer.append",
          audio: arrayBufferToBase64(pcm),
        })
      );
    };

    source.connect(processor);
    processor.connect(ctx.destination);

    resetIdleTimer();
  }, [drainAudioQueue, resetIdleTimer, teardown, sessionState]);

  const handleToggle = useCallback(() => {
    if (sessionState === "idle" || sessionState === "error") {
      startSession();
    } else {
      teardown("user stopped");
      setSessionState("idle");
      setStatusText("Press to start");
    }
  }, [sessionState, startSession, teardown]);

  if (!isOpen) return null;

  const isActive = sessionState !== "idle" && sessionState !== "error";

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center pb-8 px-4"
      style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) { teardown(); onClose(); } }}
      role="dialog"
      aria-label="Speech-to-speech conversation"
      aria-modal="true"
    >
      <div
        className="w-full max-w-md rounded-3xl shadow-2xl flex flex-col gap-4 p-6"
        style={{ background: "var(--card)", border: "1px solid var(--border)" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Volume2 className="w-5 h-5" style={{ color: "var(--accent)" }} />
            <span className="font-semibold text-sm">Voice Conversation</span>
          </div>
          <button
            onClick={() => { teardown(); onClose(); }}
            className="p-1.5 rounded-full hover:bg-(--card-hover) transition-colors cursor-pointer"
            style={{ color: "var(--muted)" }}
            aria-label="Close voice panel"
          >
            <PhoneOff className="w-4 h-4" />
          </button>
        </div>

        {/* Waveform / status indicator */}
        <div className="flex flex-col items-center gap-4 py-4">
          {/* Animated orb */}
          <div className="relative flex items-center justify-center">
            {/* Outer pulse rings */}
            {sessionState === "listening" && (
              <>
                <span className="absolute w-24 h-24 rounded-full animate-ping opacity-20"
                  style={{ background: "var(--accent)" }} />
                <span className="absolute w-20 h-20 rounded-full animate-ping opacity-15 animation-delay-150"
                  style={{ background: "var(--accent)", animationDelay: "150ms" }} />
              </>
            )}
            {sessionState === "speaking" && (
              <span className="absolute w-20 h-20 rounded-full animate-pulse opacity-25"
                style={{ background: "var(--accent)" }} />
            )}
            {/* Core button */}
            <button
              onClick={handleToggle}
              disabled={sessionState === "connecting" || sessionState === "transcribing" as SessionState}
              className="relative w-16 h-16 rounded-full flex items-center justify-center transition-all shadow-lg disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
              style={{
                background: isActive
                  ? "var(--accent)"
                  : "var(--card-hover)",
              }}
              aria-label={isActive ? "End voice session" : "Start voice session"}
            >
              {sessionState === "connecting" ? (
                <Loader2 className="w-7 h-7 text-white animate-spin" />
              ) : isActive ? (
                <Phone className="w-7 h-7 text-white" />
              ) : (
                <Mic className="w-7 h-7" style={{ color: "var(--muted)" }} />
              )}
            </button>
          </div>

          {/* Status text */}
          <p className="text-sm text-center" style={{ color: "var(--muted)" }}>
            {error ? (
              <span style={{ color: "#ef4444" }}>{error}</span>
            ) : (
              statusText
            )}
          </p>

          {/* Idle timeout notice */}
          {isActive && (
            <p className="text-[11px] text-center" style={{ color: "var(--muted)", opacity: 0.6 }}>
              Auto-ends after {IDLE_CLOSE_MS / 1000}s of silence
            </p>
          )}
        </div>

        {/* Rolling transcript */}
        {transcript.length > 0 && (
          <div
            className="rounded-xl p-3 max-h-40 overflow-y-auto flex flex-col gap-1 text-xs"
            style={{ background: "var(--background)", border: "1px solid var(--border)" }}
          >
            {transcript.map((line, i) => (
              <p
                key={i}
                style={{
                  color: line.startsWith("You:") ? "var(--foreground)" : "var(--accent)",
                }}
              >
                {line}
              </p>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
