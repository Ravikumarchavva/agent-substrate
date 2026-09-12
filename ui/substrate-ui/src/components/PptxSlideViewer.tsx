"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import {
  initWasm,
  openPresentation,
  paintSlide,
  sizeCanvasForSlide,
  type PresentationHandle,
} from "@betteroffice/pptx";

// Same font requirement as PptxEditor (see BetterOfficeEditor.tsx) — the
// core engine needs real font bytes to lay out text at all.
const FONT_FAMILY = "Liberation Sans";
const FONT_URL = "/chat/fonts/LiberationSans-Regular.ttf";

let wasmReady: Promise<void> | null = null;
function ensureWasm(): Promise<void> {
  if (!wasmReady) wasmReady = initWasm();
  return wasmReady;
}

/**
 * Chrome-free, read-only slide preview — no toolbar, no collaboration UI.
 * All slides stacked and vertically scrollable (Claude's own artifact
 * viewer does the same for a pptx, rather than click-through pagination —
 * matches that directly). Uses the core `@betteroffice/pptx` package's
 * low-level rendering primitives (`openPresentation`/`layoutSlide`/
 * `paintSlide`) directly, bypassing `PptxEditor` entirely: that component
 * always renders its full editing chrome (confirmed no `readOnly`/
 * viewer-only prop exists), which is wrong for a glance-first preview —
 * same reasoning as `SpreadsheetView`'s SheetJS grid for xlsx, just built
 * on BetterOffice's own renderer since pptx has no other read-only-capable
 * parser already in this app.
 */
export function PptxSlideViewer({ fileUrl }: { fileUrl: string }) {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [errMsg, setErrMsg] = useState("");
  const [slideCount, setSlideCount] = useState(0);
  const handleRef = useRef<PresentationHandle | null>(null);
  const canvasRefs = useRef<(HTMLCanvasElement | null)[]>([]);
  const containerRef = useRef<HTMLDivElement | null>(null);
  // Bumped by the ResizeObserver below so the paint effect re-fits every
  // slide to the panel's current width whenever it's resized (maximize,
  // split, etc.).
  const [containerWidth, setContainerWidth] = useState(0);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setContainerWidth((n) => n + 1));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let cancelled = false;
    setState("loading");
    (async () => {
      try {
        const [, fileRes, fontRes] = await Promise.all([
          ensureWasm(),
          fetch(fileUrl),
          fetch(FONT_URL),
        ]);
        if (!fileRes.ok) throw new Error(`Failed to load file: ${fileRes.status}`);
        if (!fontRes.ok) throw new Error(`Failed to load font: ${fontRes.status}`);
        const bytes = new Uint8Array(await fileRes.arrayBuffer());
        const fontBytes = new Uint8Array(await fontRes.arrayBuffer());
        if (cancelled) return;

        const handle = openPresentation(bytes, {
          fonts: [{ family: FONT_FAMILY, bytes: fontBytes }],
        });
        handleRef.current = handle;
        const count = handle.snapshot().slides.length;
        canvasRefs.current = new Array(count).fill(null);
        setSlideCount(count);
        setState("ready");
      } catch (e) {
        if (!cancelled) {
          setErrMsg(e instanceof Error ? e.message : String(e));
          setState("error");
        }
      }
    })();
    return () => {
      cancelled = true;
      handleRef.current?.dispose();
      handleRef.current = null;
    };
  }, [fileUrl]);

  useEffect(() => {
    const handle = handleRef.current;
    const container = containerRef.current;
    if (state !== "ready" || !handle || !container || slideCount === 0) return;
    const dpr = window.devicePixelRatio || 1;
    const padding = 32; // matches the wrapper's p-4 (1rem) on each side
    const availableWidth = container.clientWidth - padding;

    for (let i = 0; i < slideCount; i++) {
      const canvas = canvasRefs.current[i];
      if (!canvas) continue;
      const list = handle.layoutSlide(i);

      // sizeCanvasForSlide at scale 1 gives the slide's natural CSS width —
      // fit that to the container's width (height follows from aspect
      // ratio, since the deck scrolls vertically rather than paginating).
      sizeCanvasForSlide(canvas, list, dpr, 1);
      const naturalWidth = parseFloat(canvas.style.width) || canvas.width / dpr;
      const fitScale = Math.min(availableWidth / naturalWidth, 2);
      sizeCanvasForSlide(canvas, list, dpr, fitScale > 0 ? fitScale : 1);

      const ctx = canvas.getContext("2d");
      if (!ctx) continue;
      // Resolves embedded images by part path; anything that fails to
      // decode (unexpected format, missing part) just paints without that
      // image rather than breaking the whole slide.
      void paintSlide(ctx, list, dpr, fitScale > 0 ? fitScale : 1, {
        resolveImage: (assetId) => {
          try {
            const bytes = handle.mediaBytes(assetId);
            const blob = new Blob([new Uint8Array(bytes)]);
            return createImageBitmap(blob).catch(() => null);
          } catch {
            return null;
          }
        },
      });
    }
  }, [state, slideCount, containerWidth]);

  if (state === "loading") {
    return (
      <div className="flex h-full w-full items-center justify-center bg-(--card)">
        <Loader2 className="h-5 w-5 animate-spin text-(--muted)" />
      </div>
    );
  }

  if (state === "error") {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-(--muted)">
        <span>Couldn&apos;t preview this presentation.</span>
        {errMsg && (
          <code className="max-w-full overflow-auto rounded bg-(--code-bg) px-2 py-1 text-xs text-(--code-fg)">
            {errMsg}
          </code>
        )}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="flex h-full w-full flex-col items-center gap-4 overflow-y-auto bg-(--card) p-4"
    >
      {Array.from({ length: slideCount }, (_, i) => (
        <div key={i} className="relative">
          <canvas
            ref={(el) => {
              canvasRefs.current[i] = el;
            }}
            className="block max-w-full rounded shadow-sm"
          />
          {slideCount > 1 && (
            <span className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-medium text-white">
              Slide {i + 1} / {slideCount}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
