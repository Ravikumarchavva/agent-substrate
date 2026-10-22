"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { Loader2, Pause, Play, Volume2 } from "lucide-react";
import { Button } from "@/design";
import { api } from "@/lib/api";
import { toSpeechText } from "@/lib/speech-text";
import {
  DEFAULT_TTS_PLAYBACK_RATE,
  MODEL_PREFERENCES_UPDATED_EVENT,
  TTS_PLAYBACK_RATE_OPTIONS,
  TTS_PLAYBACK_RATE_STORAGE_KEY,
  formatPlaybackRateLabel,
  getPreferredTTSFormat,
  getPreferredTTSModel,
  getPreferredTTSPlaybackRate,
  getPreferredTTSVoice,
  writeStoredValue,
} from "@/lib/model-preferences";
import { deleteCachedTtsAudio, getCachedTtsAudio, setCachedTtsAudio } from "@/lib/audio-cache";
import type { TTSPlaybackRate, TTSVoice } from "@/types";
import { reportError } from "@/lib/report-error";

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

export function AudioPlayer({ text: markdown }: AudioPlayerProps) {
  const text = useMemo(() => toSpeechText(markdown), [markdown]);
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
      reportError("Couldn't read that aloud", err);
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

  // One icon in the row of message actions until it is playing; then it opens into the controls (progress, time, speed) and folds back when it ends.
  const open = hasAudio && (playerState === "playing" || playerState === "paused" || playerState === "loading");
  const failed = playerState === "error";
  const label = playerState === "playing" ? "Pause" : playerState === "loading" ? "Loading audio…" : failed ? "Couldn't read that aloud: try again" : hasAudio ? "Play saved speech" : "Listen";

  return (
    <div className="flex items-center gap-1" title={hasAudio ? `Saved speech clip at ${formatPlaybackRateLabel(playbackRate)} playback` : undefined}>
      <button
        type="button"
        onClick={handleClick}
        disabled={playerState === "loading"}
        aria-label={label}
        title={label}
        className={`btn-icon flex h-6 w-6 cursor-pointer items-center justify-center rounded-md transition-colors hover:bg-card-hover disabled:cursor-not-allowed disabled:opacity-40 ${failed ? "text-danger" : "text-muted"}`}
      >
        {playerState === "loading" ? <Loader2 className="animate-spin" /> : playerState === "playing" ? <Pause /> : hasAudio || playerState === "paused" ? <Play /> : <Volume2 />}
      </button>
      {open && (
        <>
          <input
            type="range"
            min={0}
            max={durationSeconds || 0}
            step={0.1}
            value={Math.min(currentTimeSeconds, durationSeconds || 0)}
            onChange={handleSeek}
            aria-label="Seek saved speech"
            className="h-1 w-20 cursor-pointer appearance-none rounded-full bg-border"
            style={{ accentColor: "var(--accent)" }}
          />
          <span className="min-w-8 text-2xs tabular-nums text-muted">{formatClock(currentTimeSeconds)}</span>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => {
              const rates = TTS_PLAYBACK_RATE_OPTIONS.map((o) => o.id);
              const next = rates[(rates.indexOf(playbackRate) + 1) % rates.length];
              writeStoredValue(TTS_PLAYBACK_RATE_STORAGE_KEY, String(next));
            }}
            aria-label={`Playback speed ${formatPlaybackRateLabel(playbackRate)}, change`}
            title="Change playback speed"
            className="h-6 w-auto px-1 text-2xs tabular-nums"
          >
            {formatPlaybackRateLabel(playbackRate)}
          </Button>
        </>
      )}
    </div>
  );
}
