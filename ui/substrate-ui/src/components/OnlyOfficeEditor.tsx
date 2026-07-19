"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Loader2 } from "lucide-react";

declare global {
  interface Window {
    DocsAPI?: {
      DocEditor: new (elementId: string, config: unknown) => {
        destroyEditor?: () => void;
      };
    };
  }
}

// Inject the ONLYOFFICE editor API script once per doc-server origin. Only a
// *resolved* promise is cached — a rejection (e.g. the doc server wasn't up
// yet) is evicted so a later open retries the load instead of being stuck with
// a poisoned promise until a full page reload.
const _scriptPromises = new Map<string, Promise<void>>();
function loadDocsApi(baseUrl: string): Promise<void> {
  const src = `${baseUrl}/web-apps/apps/api/documents/api.js`;
  const existing = _scriptPromises.get(src);
  if (existing) return existing;
  const p = new Promise<void>((resolve, reject) => {
    if (window.DocsAPI) return resolve();
    const el = document.createElement("script");
    el.src = src;
    el.async = true;
    el.onload = () => resolve();
    el.onerror = () => reject(new Error(`Failed to load ONLYOFFICE API from ${src}`));
    document.head.appendChild(el);
  });
  p.catch(() => _scriptPromises.delete(src));
  _scriptPromises.set(src, p);
  return p;
}

/**
 * Embeds the ONLYOFFICE editor for an editable Office file (docx/xlsx/pptx).
 * Fetches the JWT-signed config from the backend, loads the doc-server API,
 * and mounts the editor. When ONLYOFFICE isn't configured (backend 503), it
 * renders the read-only `fallback` (SheetJS/Mammoth) instead.
 */
export function OnlyOfficeEditor({
  threadId,
  path,
  editMode = false,
  fallback,
}: {
  threadId: string;
  path: string;
  /** Read-only by default; the panel header's Edit toggle flips this on,
   *  re-minting the editor with edit permissions. */
  editMode?: boolean;
  fallback: React.ReactNode;
}) {
  const [state, setState] = useState<"loading" | "editor" | "fallback" | "error">(
    "loading",
  );
  const [errMsg, setErrMsg] = useState<string>("");
  const mode = editMode ? "edit" : "view";
  const editorRef = useRef<{ destroyEditor?: () => void } | null>(null);
  // ONLYOFFICE mounts into this element by id; useId is stable + collision-free
  // (strip colons — DocEditor's getElementById lookup wants a plain id).
  const elementId = `oo-${useId().replace(/:/g, "")}`;

  useEffect(() => {
    let cancelled = false;
    const fail = (msg: string) => {
      console.error("[OnlyOfficeEditor]", msg);
      if (!cancelled) {
        setErrMsg(msg);
        setState("error");
      }
    };
    (async () => {
      try {
        const res = await fetch(
          `/api/backend/workspace/onlyoffice/config?thread_id=${encodeURIComponent(
            threadId,
          )}&path=${encodeURIComponent(path)}&mode=${mode}`,
        );
        if (res.status === 503) {
          if (!cancelled) setState("fallback");
          return;
        }
        if (!res.ok) {
          fail(`config ${res.status}: ${(await res.text()).slice(0, 200)}`);
          return;
        }
        const { url, config } = (await res.json()) as { url: string; config: unknown };
        await loadDocsApi(url);
        if (cancelled) return;
        if (!window.DocsAPI) {
          fail("DocsAPI script loaded but window.DocsAPI is undefined");
          return;
        }
        editorRef.current = new window.DocsAPI.DocEditor(elementId, config);
        setState("editor");
      } catch (e) {
        fail(e instanceof Error ? `${e.name}: ${e.message}` : String(e));
      }
    })();
    return () => {
      cancelled = true;
      try {
        editorRef.current?.destroyEditor?.();
      } catch {
        /* editor already torn down */
      }
      editorRef.current = null;
    };
    // threadId+path identify the file; re-mount (new key from AppPanel) reloads.
    // mode flips view↔edit and re-inits the editor with new permissions.
  }, [threadId, path, elementId, mode]);

  if (state === "fallback") return <>{fallback}</>;
  if (state === "error")
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-(--muted)">
        <span>Couldn&apos;t open the editor.</span>
        {errMsg && (
          <code className="max-w-full overflow-auto rounded bg-(--code-bg) px-2 py-1 text-xs text-(--code-fg)">
            {errMsg}
          </code>
        )}
      </div>
    );

  // Keep exactly two children so React never inserts/removes a sibling around
  // the ONLYOFFICE-mutated element (that triggers an insertBefore DOM error):
  // one always-mounted overlay layer React fully controls, and the stable
  // editor div ONLYOFFICE owns. Overlay content toggles inside the layer.
  return (
    <div className="relative h-full w-full">
      <div className="pointer-events-none absolute inset-0 z-10">
        {state === "loading" && (
          <div className="flex h-full w-full items-center justify-center bg-(--card)">
            <Loader2 className="h-5 w-5 animate-spin text-(--muted)" />
          </div>
        )}
      </div>
      {/* ONLYOFFICE replaces this element with its editor iframe. */}
      <div id={elementId} className="h-full w-full" />
    </div>
  );
}
