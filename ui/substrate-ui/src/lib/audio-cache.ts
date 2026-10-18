import type { TTSVoice } from "@/types";

interface CachedTtsAudioInput {
  text: string;
  model: string;
  voice: TTSVoice;
  format: "mp3" | "wav";
}

const AUDIO_CACHE_NAME = "substrate-tts-audio-v1";

function hashValue(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

function buildCacheRequest(input: CachedTtsAudioInput): Request {
  const origin = typeof window === "undefined" ? "https://local.substrate-ui" : window.location.origin;
  const cacheKey = hashValue([
    input.model.trim(),
    input.voice.trim(),
    input.format,
    input.text.trim(),
  ].join("\u241F"));

  return new Request(new URL(`/__tts-cache/${cacheKey}`, origin).toString(), {
    method: "GET",
  });
}

async function getAudioCache(): Promise<Cache | null> {
  if (typeof window === "undefined" || !("caches" in window)) return null;
  return caches.open(AUDIO_CACHE_NAME);
}

export async function getCachedTtsAudio(input: CachedTtsAudioInput): Promise<Blob | null> {
  const cache = await getAudioCache();
  if (!cache) return null;

  const cachedResponse = await cache.match(buildCacheRequest(input));
  if (!cachedResponse) return null;

  return cachedResponse.blob();
}

export async function setCachedTtsAudio(
  input: CachedTtsAudioInput,
  blob: Blob,
): Promise<void> {
  const cache = await getAudioCache();
  if (!cache) return;

  const contentType = blob.type || (input.format === "wav" ? "audio/wav" : "audio/mpeg");
  const response = new Response(blob, {
    headers: {
      "Content-Type": contentType,
      "Cache-Control": "public, max-age=31536000, immutable",
    },
  });

  await cache.put(buildCacheRequest(input), response);
}

export async function deleteCachedTtsAudio(input: CachedTtsAudioInput): Promise<void> {
  const cache = await getAudioCache();
  if (!cache) return;

  await cache.delete(buildCacheRequest(input));
}