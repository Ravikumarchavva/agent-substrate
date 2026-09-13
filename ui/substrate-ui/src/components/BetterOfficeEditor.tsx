"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { DocxEditor } from "@betteroffice/docx-react";
import "@betteroffice/docx-react/styles.css";
import { XlsxEditor } from "@betteroffice/xlsx-react";
import { PptxEditor } from "@betteroffice/pptx-react";

// Word documents reference Calibri/Arial/etc. by name; the actual binaries
// can't be redistributed. Liberation Sans is the open, metric-compatible
// replacement @betteroffice/fonts ships (see its README) — PptxEditor's
// `fonts` prop is required and renders nothing ("no font has been
// registered for slide text") without it. Served as a static asset rather
// than importing @betteroffice/fonts' loader (that package targets DocxEditor's
// layout engine specifically, not this raw-bytes prop shape).
const PPTX_FONT_FAMILY = "Liberation Sans";
// Raw fetch() is NOT basePath-aware (this app is mounted at "/chat" behind
// agent-substrate-platform's proxy — see next.config.ts) — has to be spelled
// out, same as API_BASE in lib/api/_client.ts.
const PPTX_FONT_URL = "/chat/fonts/LiberationSans-Regular.ttf";

type Kind = "docx" | "xlsx" | "pptx";

async function fetchFileBytes(
  fileUrl: string,
): Promise<{ bytes: Uint8Array; checksum: string }> {
  const res = await fetch(fileUrl);
  if (!res.ok) throw new Error(`Failed to load file: ${res.status}`);
  const checksum = res.headers.get("X-File-Checksum") ?? "";
  const bytes = new Uint8Array(await res.arrayBuffer());
  return { bytes, checksum };
}

async function saveBytes(
  threadId: string,
  path: string,
  bytes: Uint8Array | ArrayBuffer,
  baseChecksum: string,
): Promise<{ checksum: string } | { conflict: true }> {
  const res = await fetch(
    `/chat/api/backend/workspace/file?thread_id=${encodeURIComponent(
      threadId,
    )}&path=${encodeURIComponent(path)}`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Base-Checksum": baseChecksum,
      },
      body: bytes,
    },
  );
  if (res.status === 409) return { conflict: true };
  if (!res.ok) throw new Error(`Save failed: ${res.status}`);
  const { checksum } = (await res.json()) as { checksum: string; seq: number };
  return { checksum };
}

/**
 * Embeds the BetterOffice editor for an editable Office file (docx/xlsx/pptx)
 * — a client-side WASM library (Apache-2.0), replacing the OnlyOffice
 * Document Server integration (AGPLv3 — the licensing reason for this swap).
 * No server round trip to open/edit; save PUTs the edited bytes back through
 * the same `/workspace/file` endpoint Monaco/text editing already uses, so
 * version history (`FileVersion`, `VersionHistoryDropdown`) keeps working
 * unchanged.
 *
 * Neither xlsx nor pptx has a read-only mode (confirmed absent from their
 * types, unlike DocxEditor's `readOnly` prop), but both have a real
 * lightweight read-only preview to fall back to instead — `SpreadsheetView`
 * (SheetJS) for xlsx, `PptxSlideViewer` (BetterOffice's own low-level
 * rendering primitives, chrome-free) for pptx — so `editMode=false` shows
 * that rather than mounting the full editor. docx always toggles its
 * native `readOnly` instead of using a separate fallback component.
 */
export function BetterOfficeEditor({
  kind,
  fileUrl,
  threadId,
  path,
  editMode = false,
  fallback,
}: {
  kind: Kind;
  fileUrl: string;
  threadId: string;
  path: string;
  /** docx: toggles native readOnly. xlsx/pptx: false renders `fallback`
   *  instead of mounting the editor. */
  editMode?: boolean;
  /** Required for xlsx/pptx (rendered when `!editMode`); unused for docx. */
  fallback?: React.ReactNode;
}) {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [errMsg, setErrMsg] = useState("");
  const [bytes, setBytes] = useState<Uint8Array | null>(null);
  const [pptxFont, setPptxFont] = useState<Uint8Array | null>(null);
  const checksumRef = useRef("");

  useEffect(() => {
    // xlsx/pptx render `fallback` while !editMode (see below) — skip
    // fetching the editor's own bytes/fonts until Edit is actually clicked,
    // since PptxSlideViewer/SpreadsheetView already fetch what they need.
    if ((kind === "xlsx" || kind === "pptx") && !editMode) return;
    let cancelled = false;
    setState("loading");
    (async () => {
      try {
        const { bytes: fileBytes, checksum } = await fetchFileBytes(fileUrl);
        if (cancelled) return;
        checksumRef.current = checksum;
        setBytes(fileBytes);
        if (kind === "pptx") {
          const fontRes = await fetch(PPTX_FONT_URL);
          if (!fontRes.ok) throw new Error(`Failed to load font: ${fontRes.status}`);
          if (cancelled) return;
          setPptxFont(new Uint8Array(await fontRes.arrayBuffer()));
        }
        if (!cancelled) setState("ready");
      } catch (e) {
        if (!cancelled) {
          setErrMsg(e instanceof Error ? e.message : String(e));
          setState("error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fileUrl, kind, editMode]);

  const persist = async (out: Uint8Array | ArrayBuffer) => {
    const result = await saveBytes(threadId, path, out, checksumRef.current);
    if ("conflict" in result) {
      console.error("[BetterOfficeEditor] save conflict: file changed since it was opened");
      return;
    }
    checksumRef.current = result.checksum;
  };

  // xlsx/pptx: a real lightweight read-only preview exists for both now —
  // use it until Edit is clicked, matching docx's readOnly (and, apparently,
  // what Claude's own artifact viewer does).
  if ((kind === "xlsx" || kind === "pptx") && !editMode) {
    return <>{fallback}</>;
  }

  if (state === "loading") {
    return (
      <div className="flex h-full w-full items-center justify-center bg-(--card)">
        <Loader2 className="h-5 w-5 animate-spin text-(--muted)" />
      </div>
    );
  }

  if (state === "error" || !bytes) {
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
  }

  if (kind === "docx") {
    return (
      <div className="h-full w-full">
        <DocxEditor
          documentBuffer={bytes}
          readOnly={!editMode}
          showToolbar={editMode}
          onSave={(buffer) => {
            void persist(buffer);
          }}
        />
      </div>
    );
  }

  if (kind === "xlsx") {
    return (
      <div className="h-full w-full">
        <XlsxEditor
          file={bytes}
          fileName={path.split("/").pop()}
          onSave={(saved) => {
            void persist(saved);
          }}
        />
      </div>
    );
  }

  // pptx
  if (!pptxFont) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-(--card)">
        <Loader2 className="h-5 w-5 animate-spin text-(--muted)" />
      </div>
    );
  }
  return (
    <div className="h-full w-full">
      <PptxEditor
        file={bytes}
        fonts={[{ family: PPTX_FONT_FAMILY, bytes: pptxFont }]}
        fileName={path.split("/").pop()}
        onSave={(saved) => {
          void persist(saved);
        }}
      />
    </div>
  );
}
