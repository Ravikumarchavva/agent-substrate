"use client";

import { useEffect, useRef } from "react";

// Module-level, not per-instance: one AdSlot mounts per completed message,
// so a ref on the component would re-inject the script on every message.
let ethicalAdsScriptLoaded = false;

/**
 * One ad unit, rendered after a completed assistant message — the same
 * placement ChatGPT uses (bottom of the message, below the action row).
 *
 * Network: EthicalAds, not AdSense. Two reasons, both specific to this
 * placement:
 *   1. EthicalAds has no traffic minimum and is built for a dev-tool
 *      audience (Python docs, FastAPI, Read the Docs all run it) — a fit
 *      for a low-traffic launch, where AdSense's review process and
 *      volume expectations are a poor match.
 *   2. It ships no tracking cookie and doesn't read page content — it
 *      picks an ad from `data-ea-type`/`data-ea-publisher` alone. AdSense's
 *      script scans surrounding DOM content for contextual targeting,
 *      which here would mean scanning live chat messages. Not acceptable
 *      next to a conversation.
 *
 * Renders nothing if `NEXT_PUBLIC_ETHICALADS_PUBLISHER_ID` isn't set —
 * there is no publisher id checked into this repo. Get one at
 * https://www.ethicalads.io/publishers/ before this shows real ads.
 */
export function AdSlot() {
  const publisherId = process.env.NEXT_PUBLIC_ETHICALADS_PUBLISHER_ID;
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!publisherId || ethicalAdsScriptLoaded) return;
    ethicalAdsScriptLoaded = true;

    const script = document.createElement("script");
    script.src = "https://media.ethicalads.io/media/client/ethicalads.min.js";
    script.async = true;
    document.body.appendChild(script);
  }, [publisherId]);

  if (!publisherId) return null;

  return (
    <div
      ref={containerRef}
      className="ethical-ad mt-3 max-w-sm rounded-xl border border-(--border) p-3 text-xs"
      data-ea-publisher={publisherId}
      data-ea-type="text"
      aria-label="Advertisement"
    />
  );
}
