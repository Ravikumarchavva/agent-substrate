"use client";

import { useCallback, useRef, useState } from "react";
import { Loader2, Pause, Play, Volume2 } from "lucide-react";
import { api } from "@/lib/api";

interface AudioPlayerProps {
  /** The assistant message text to synthesize. */
  text: string;
}

type PlayerState = "idle" | "loading" | "playing" | "paused" | "error";

export function AudioPlayer({ text }: AudioPlayerProps) {
  const [playerState, setPlayerState] = useState<PlayerState>("idle");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // Cache the blob URL so we don't re-fetch on play/pause
  const blobUrlRef = useRef<string | null>(null);

  const ensureAudio = useCallback(async (): Promise<HTMLAudioElement | null> => {
    if (audioRef.current && blobUrlRef.current) return audioRef.current;

    setPlayerState("loading");
    try {
      const blob = await api.textToSpeech(text);
      const url = URL.createObjectURL(blob);
      blobUrlRef.current = url;
      const audio = new Audio(url);
      audio.onended = () => setPlayerState("idle");
      audio.onerror = () => {
        setPlayerState("error");
        URL.revokeObjectURL(url);
        blobUrlRef.current = null;
        audioRef.current = null;
      };
      audioRef.current = audio;
      return audio;
    } catch (err) {
      console.error("TTS error:", err);
      setPlayerState("error");
      return null;
    }
  }, [text]);

  const handleClick = useCallback(async () => {
    if (playerState === "loading") return;

    if (playerState === "playing") {
      audioRef.current?.pause();
      setPlayerState("paused");
      return;
    }

    if (playerState === "paused" && audioRef.current) {
      await audioRef.current.play();
      setPlayerState("playing");
      return;
    }

    // idle or error — (re)fetch and play
    if (playerState === "error") {
      blobUrlRef.current = null;
      audioRef.current = null;
    }

    const audio = await ensureAudio();
    if (!audio) return;

    await audio.play();
    setPlayerState("playing");
  }, [playerState, ensureAudio]);

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={playerState === "loading"}
      aria-label={
        playerState === "playing"
          ? "Pause speech"
          : playerState === "loading"
          ? "Loading audio…"
          : "Play as speech"
      }
      title={
        playerState === "error"
          ? "TTS failed — click to retry"
          : playerState === "playing"
          ? "Pause"
          : "Listen"
      }
      className="p-1.5 rounded hover:bg-(--card) transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
      style={{
        color: playerState === "error" ? "#ef4444" : "var(--muted)",
      }}
    >
      {playerState === "loading" ? (
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
      ) : playerState === "playing" ? (
        <Pause className="w-3.5 h-3.5" />
      ) : playerState === "paused" ? (
        <Play className="w-3.5 h-3.5" />
      ) : (
        <Volume2 className="w-3.5 h-3.5" />
      )}
    </button>
  );
}
