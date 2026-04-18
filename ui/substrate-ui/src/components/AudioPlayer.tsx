"use client";

import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import { Loader2, Pause, Play, Volume2 } from "lucide-react";
import { api } from "@/lib/api";
import {
  DEFAULT_TTS_PLAYBACK_RATE,
  MODEL_PREFERENCES_UPDATED_EVENT,
  TTS_PLAYBACK_RATE_STORAGE_KEY,
  formatPlaybackRateLabel,
  getPreferredTTSFormat,
  getPreferredTTSModel,
  getPreferredTTSPlaybackRate,
  getPreferredTTSVoice,
} from "@/lib/model-preferences";
import { deleteCachedTtsAudio, getCachedTtsAudio, setCachedTtsAudio } from "@/lib/audio-cache";
import type { TTSPlaybackRate, TTSVoice } from "@/types";

interface AudioPlayerProps {
  /** The assistant message text to synthesize. */
  text: string;
}

type PlayerState = "idle" | "loading" | "playing" | "paused" | "error";

interface TtsAudioRequest {
  text: string;
  model: string;
  voice: TTSVoice;
  format: "mp3" | "wav";
}

function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0:00";

  const roundedSeconds = Math.floor(seconds);
  const minutes = Math.floor(roundedSeconds / 60);
  const remainingSeconds = roundedSeconds % 60;
  return `${minutes}:${remainingSeconds.toString().padStart(2, "0")}`;
}

export function AudioPlayer({ text }: AudioPlayerProps) {
  const [playerState, setPlayerState] = useState<PlayerState>("idle");
  const [hasAudio, setHasAudio] = useState(false);
  const [durationSeconds, setDurationSeconds] = useState(0);
  const [currentTimeSeconds, setCurrentTimeSeconds] = useState(0);
  const [playbackRate, setPlaybackRate] = useState<TTSPlaybackRate>(DEFAULT_TTS_PLAYBACK_RATE);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const blobUrlRef = useRef<string | null>(null);

  const disposeAudio = useCallback(() => {
    const currentAudio = audioRef.current;
    if (currentAudio) {
      currentAudio.pause();
      currentAudio.onended = null;
      currentAudio.onerror = null;
      currentAudio.onloadedmetadata = null;
      currentAudio.onpause = null;
      currentAudio.onplay = null;
      currentAudio.ontimeupdate = null;
      currentAudio.src = "";
    }

    audioRef.current = null;

    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
  }, []);

  const buildAudioRequest = useCallback((): TtsAudioRequest => {
    const model = getPreferredTTSModel();
    return {
      text,
      model,
      voice: getPreferredTTSVoice(model),
      format: getPreferredTTSFormat(model),
    };
  }, [text]);

  const syncPlaybackRate = useCallback((audio: HTMLAudioElement) => {
    const nextPlaybackRate = getPreferredTTSPlaybackRate();
    audio.playbackRate = nextPlaybackRate;
    setPlaybackRate(nextPlaybackRate);
  }, []);

  const createAudioFromBlob = useCallback((blob: Blob, request: TtsAudioRequest) => {
    disposeAudio();

    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.preload = "auto";
    blobUrlRef.current = url;
    audioRef.current = audio;
    syncPlaybackRate(audio);

    audio.onended = () => {
      setPlayerState("idle");
      setCurrentTimeSeconds(Number.isFinite(audio.duration) ? audio.duration : 0);
    };
    audio.onerror = () => {
      setPlayerState("error");
      setHasAudio(false);
      setDurationSeconds(0);
      setCurrentTimeSeconds(0);
      void deleteCachedTtsAudio(request);
      disposeAudio();
    };
    audio.onloadedmetadata = () => {
      setHasAudio(true);
      setDurationSeconds(Number.isFinite(audio.duration) ? audio.duration : 0);
      setCurrentTimeSeconds(audio.currentTime);
      setPlayerState("idle");
    };
    audio.onpause = () => {
      if (!audio.ended) {
        setPlayerState("paused");
      }
    };
    audio.onplay = () => setPlayerState("playing");
    audio.ontimeupdate = () => setCurrentTimeSeconds(audio.currentTime);

    setHasAudio(true);
    return audio;
  }, [disposeAudio, syncPlaybackRate]);

  const ensureAudio = useCallback(async (forceRefetch = false): Promise<HTMLAudioElement | null> => {
    if (audioRef.current) {
      syncPlaybackRate(audioRef.current);
      return audioRef.current;
    }

    const request = buildAudioRequest();
    setPlayerState("loading");

    try {
      const cachedBlob = forceRefetch ? null : await getCachedTtsAudio(request);
      if (cachedBlob) {
        return createAudioFromBlob(cachedBlob, request);
      }

      const freshBlob = await api.textToSpeech(text, request.voice, request.model);
      await setCachedTtsAudio(request, freshBlob);
      return createAudioFromBlob(freshBlob, request);
    } catch (err) {
      console.error("TTS error:", err);
      setPlayerState("error");
      setHasAudio(false);
      return null;
    }
  }, [buildAudioRequest, createAudioFromBlob, syncPlaybackRate, text]);

  useEffect(() => {
    let isCancelled = false;

    void (async () => {
      const request = buildAudioRequest();
      const cachedBlob = await getCachedTtsAudio(request);
      if (!cachedBlob || isCancelled) return;
      createAudioFromBlob(cachedBlob, request);
    })();

    return () => {
      isCancelled = true;
      disposeAudio();
    };
  }, [buildAudioRequest, createAudioFromBlob, disposeAudio]);

  useEffect(() => {
    const handlePreferenceUpdate = (event: Event) => {
      const nextEvent = event as CustomEvent<{ key?: string; value?: string | null }>;
      if (nextEvent.detail?.key !== TTS_PLAYBACK_RATE_STORAGE_KEY) return;

      const nextPlaybackRate = getPreferredTTSPlaybackRate();
      setPlaybackRate(nextPlaybackRate);
      if (audioRef.current) {
        audioRef.current.playbackRate = nextPlaybackRate;
      }
    };

    window.addEventListener(MODEL_PREFERENCES_UPDATED_EVENT, handlePreferenceUpdate as EventListener);
    return () => {
      window.removeEventListener(MODEL_PREFERENCES_UPDATED_EVENT, handlePreferenceUpdate as EventListener);
    };
  }, []);

  const handleClick = useCallback(async () => {
    if (playerState === "loading") return;

    if (playerState === "playing") {
      audioRef.current?.pause();
      setPlayerState("paused");
      return;
    }

    if (playerState === "error") {
      disposeAudio();
      const audio = await ensureAudio(true);
      if (!audio) return;
      syncPlaybackRate(audio);
      audio.currentTime = 0;
      await audio.play();
      return;
    }

    const audio = await ensureAudio();
    if (!audio) return;

    syncPlaybackRate(audio);
    if (audio.duration > 0 && audio.currentTime >= audio.duration - 0.05) {
      audio.currentTime = 0;
      setCurrentTimeSeconds(0);
    }

    await audio.play();
  }, [disposeAudio, ensureAudio, playerState, syncPlaybackRate]);

  const handleSeek = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    if (!audioRef.current) return;
    const nextTime = Number.parseFloat(event.target.value);
    audioRef.current.currentTime = nextTime;
    setCurrentTimeSeconds(nextTime);
  }, []);

  if (hasAudio) {
    return (
      <div
        className="flex min-w-0 items-center gap-2 rounded-full border border-(--border) bg-(--card) px-2 py-1"
        title={`Saved speech clip at ${formatPlaybackRateLabel(playbackRate)} playback`}
      >
        <button
          type="button"
          onClick={handleClick}
          disabled={playerState === "loading"}
          aria-label={
            playerState === "playing"
              ? "Pause saved speech"
              : playerState === "loading"
              ? "Loading saved speech"
              : "Play saved speech"
          }
          className="rounded-full p-1.5 transition-colors hover:bg-background disabled:cursor-not-allowed disabled:opacity-40 cursor-pointer"
          style={{ color: playerState === "error" ? "#ef4444" : "var(--muted)" }}
        >
          {playerState === "loading" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : playerState === "playing" ? (
            <Pause className="h-3.5 w-3.5" />
          ) : (
            <Play className="h-3.5 w-3.5" />
          )}
        </button>

        <input
          type="range"
          min={0}
          max={durationSeconds || 0}
          step={0.1}
          value={Math.min(currentTimeSeconds, durationSeconds || 0)}
          onChange={handleSeek}
          aria-label="Seek saved speech"
          className="h-1 w-24 cursor-pointer appearance-none rounded-full bg-(--border) sm:w-28"
          style={{ accentColor: "var(--accent)" }}
        />

        <span className="min-w-[3rem] text-[10px] tabular-nums" style={{ color: "var(--muted)" }}>
          {formatClock(currentTimeSeconds)}
        </span>
        <span className="text-[10px]" style={{ color: "var(--muted)" }}>
          {formatPlaybackRateLabel(playbackRate)}
        </span>
      </div>
    );
  }

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
