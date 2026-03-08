"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Mic, MicOff } from "lucide-react";
import { api } from "@/lib/api";

interface VoiceRecorderProps {
  /** Called with the final transcript text. Parent decides whether to auto-send. */
  onTranscript: (text: string) => void;
  /** Disable the button while the chat is sending a message. */
  disabled?: boolean;
}

type RecordingState = "idle" | "recording" | "transcribing";

// Maximum recording duration (ms) — auto-stops to prevent forgotten sessions.
const MAX_RECORD_MS = 60_000;

export function VoiceRecorder({ onTranscript, disabled }: VoiceRecorderProps) {
  const [state, setState] = useState<RecordingState>("idle");
  const [error, setError] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const autoStopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      stopRecording();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stopRecording = useCallback(() => {
    if (autoStopTimerRef.current) {
      clearTimeout(autoStopTimerRef.current);
      autoStopTimerRef.current = null;
    }
    if (
      mediaRecorderRef.current &&
      mediaRecorderRef.current.state !== "inactive"
    ) {
      mediaRecorderRef.current.stop();
    }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  const startRecording = useCallback(async () => {
    setError(null);
    chunksRef.current = [];

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("Microphone access denied");
      return;
    }
    streamRef.current = stream;

    // Pick the best supported MIME type
    const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]
      .find((m) => MediaRecorder.isTypeSupported(m)) ?? "";

    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    mediaRecorderRef.current = recorder;

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };

    recorder.onstop = async () => {
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;

      if (chunksRef.current.length === 0) {
        setState("idle");
        return;
      }

      setState("transcribing");
      const blob = new Blob(chunksRef.current, {
        type: mimeType || "audio/webm",
      });
      chunksRef.current = [];

      try {
        const result = await api.transcribeAudio(blob, mimeType || "audio/webm");
        if (result.text) {
          onTranscript(result.text);
        }
      } catch (err) {
        console.error("Transcription error:", err);
        setError("Transcription failed. Please try again.");
      } finally {
        setState("idle");
      }
    };

    recorder.start(250); // collect chunks every 250 ms
    setState("recording");

    // Auto-stop after MAX_RECORD_MS to prevent forgotten recordings
    autoStopTimerRef.current = setTimeout(() => {
      if (mediaRecorderRef.current?.state === "recording") {
        stopRecording();
      }
    }, MAX_RECORD_MS);
  }, [onTranscript, stopRecording]);

  const handleClick = useCallback(() => {
    if (state === "idle") {
      startRecording();
    } else if (state === "recording") {
      stopRecording();
    }
    // "transcribing" — button is disabled, nothing to do
  }, [state, startRecording, stopRecording]);

  const isDisabled = disabled || state === "transcribing";

  return (
    <div className="relative inline-flex items-center">
      <button
        type="button"
        onClick={handleClick}
        disabled={isDisabled}
        aria-label={
          state === "idle"
            ? "Start voice recording"
            : state === "recording"
            ? "Stop recording"
            : "Transcribing…"
        }
        title={error ?? undefined}
        className="relative p-1.5 rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        style={{
          color:
            state === "recording"
              ? "var(--accent)"
              : error
              ? "#ef4444"
              : "var(--muted)",
          background:
            state === "recording" ? "color-mix(in srgb, var(--accent) 15%, transparent)" : "transparent",
        }}
      >
        {state === "transcribing" ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : state === "recording" ? (
          <MicOff className="w-4 h-4" />
        ) : (
          <Mic className="w-4 h-4" />
        )}

        {/* Animated pulse ring while recording */}
        {state === "recording" && (
          <span
            className="absolute inset-0 rounded-full animate-ping opacity-30"
            style={{ background: "var(--accent)" }}
          />
        )}
      </button>

      {/* Inline error tooltip */}
      {error && (
        <span
          className="absolute bottom-full mb-1 left-1/2 -translate-x-1/2 whitespace-nowrap text-[11px] px-2 py-0.5 rounded"
          style={{ background: "var(--card)", color: "#ef4444", border: "1px solid var(--border)" }}
        >
          {error}
        </span>
      )}
    </div>
  );
}
