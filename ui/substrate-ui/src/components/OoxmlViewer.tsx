"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";

type Kind = "xlsx" | "docx" | "pptx";

/**
 * Read-only Office file preview using @silurus/ooxml (MIT, client-side
 * Rust/WASM parser + Canvas renderer). This is the default view path for
 * docx/xlsx/pptx in FileArtifactViewer — BetterOfficeEditor (Apache-2.0,
 * also WASM) still handles actual editing, but was found to have real
 * fidelity gaps as a *viewer*: no xlsx chart rendering, pptx tables
 * rendering as placeholder boxes, and a hard crash opening some docx files
 * even in its own read-only mode. The SheetJS-grid/mammoth-HTML fallbacks
 * this replaced had the same problem from the other direction — correct
 * data, but no cell styling, no charts, no embedded images.
 *
 * docx/pptx use the library's own virtualized `DocxScrollViewer` /
 * `PptxScrollViewer` (continuous vertical scroll, all pages/slides stacked
 * — matches how Claude's own artifact viewer shows a pptx) rather than the
 * click-through-paginated `DocxViewer`/`PptxViewer`, so there's no pager
 * chrome to build by hand. xlsx uses `XlsxViewer`, which already manages
 * its own sheet tabs + zoom chrome.
 */
export function OoxmlViewer({ kind, fileUrl }: { kind: Kind; fileUrl: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error" | "notfound">("loading");

  useEffect(() => {
    let cancelled = false;
    // Holds whichever viewer instance actually got constructed, so cleanup
    // can release its WASM/canvas resources — switching files (kind or
    // fileUrl changing) or closing the panel used to just drop the
    // reference, leaking every previously-opened viewer's resources.
    let viewer: { load: (buffer: ArrayBuffer) => Promise<void>; destroy?: () => void } | null = null;
    setState("loading");

    (async () => {
      try {
        const res = await fetch(fileUrl);
        if (!res.ok) throw new Error(res.status === 404 ? "notfound" : String(res.status));
        const buffer = await res.arrayBuffer();
        if (cancelled) return;
        const el = containerRef.current;
        if (!el) return;

        if (kind === "xlsx") {
          const { XlsxViewer } = await import("@silurus/ooxml/xlsx");
          if (cancelled) return;
          viewer = new XlsxViewer(el);
          await viewer.load(buffer);
        } else if (kind === "pptx") {
          const { PptxScrollViewer } = await import("@silurus/ooxml/pptx");
          if (cancelled) return;
          viewer = new PptxScrollViewer(el);
          await viewer.load(buffer);
        } else {
          const { DocxScrollViewer } = await import("@silurus/ooxml/docx");
          if (cancelled) return;
          viewer = new DocxScrollViewer(el);
          await viewer.load(buffer);
        }
        if (cancelled) {
          // Cancelled while `.load()` was in flight — a stale viewer must
          // not surface as "ready" or keep holding its resources.
          viewer?.destroy?.();
          viewer = null;
          return;
        }
        setState("ready");
      } catch (e) {
        if (cancelled) return;
        setState(e instanceof Error && e.message === "notfound" ? "notfound" : "error");
      }
    })();

    return () => {
      cancelled = true;
      viewer?.destroy?.();
      viewer = null;
    };
  }, [kind, fileUrl]);

  if (state === "notfound") return <Centered>This file no longer exists.</Centered>;
  if (state === "error") return <Centered>Couldn&apos;t preview this file.</Centered>;

  return (
    <div className="relative h-full w-full">
      {state === "loading" && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-(--card)">
          <Loader2 className="h-5 w-5 animate-spin text-(--muted)" />
        </div>
      )}
      <div ref={containerRef} className="h-full w-full overflow-auto bg-white" />
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full w-full items-center justify-center p-6 text-center text-sm text-(--muted)">
      {children}
    </div>
  );
}
