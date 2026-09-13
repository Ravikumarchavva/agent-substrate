"use client";

import { useEffect, useState } from "react";
import { Loader2, Download, FileWarning } from "lucide-react";
import { BetterOfficeEditor } from "@/components/BetterOfficeEditor";
import { OoxmlViewer } from "@/components/OoxmlViewer";
import { CodeEditorView } from "@/components/CodeEditorView";

/**
 * Renders a code-interpreter-generated file in the side panel, ChatGPT/Claude
 * "artifact" style. Office files (docx/xlsx/pptx) open in the BetterOffice
 * editor (client-side WASM, full fidelity + editing, always interactive for
 * xlsx/pptx since the library has no read-only mode for those two).
 * HTML/PDF/images/text render natively.
 */
export function FileArtifactViewer({
  fileUrl,
  fileName,
  mime,
  editMode = false,
}: {
  fileUrl: string;
  fileName: string;
  mime?: string;
  editMode?: boolean;
}) {
  const kind = artifactKind(fileName, mime);

  if (kind === "html") return <HtmlView fileUrl={fileUrl} fileName={fileName} />;

  if (kind === "pdf") {
    return (
      <iframe
        src={fileUrl}
        className="h-full w-full border-none bg-white"
        title={fileName}
      />
    );
  }

  if (kind === "image") {
    return (
      <div className="flex h-full w-full items-center justify-center overflow-auto bg-(--card) p-4">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={fileUrl} alt={fileName} className="max-h-full max-w-full object-contain" />
      </div>
    );
  }

  if (kind === "csv") return <TextView fileUrl={fileUrl} csv />;

  // Code/text → editable Monaco, with save-back. Falls back to a read-only
  // view if we can't recover the thread id + path (needed for the save PUT).
  if (kind === "text") {
    const ref = parseFileUrl(fileUrl);
    if (!ref) return <TextView fileUrl={fileUrl} csv={false} />;
    return (
      <CodeEditorView
        fileUrl={fileUrl}
        fileName={fileName}
        threadId={ref.threadId}
        path={ref.path}
      />
    );
  }

  // Office docs → BetterOffice editor when actually editing; otherwise a
  // real read-only preview until Edit is clicked (glance-first, like
  // Claude's own artifact viewer), via @silurus/ooxml for all three kinds —
  // see OoxmlViewer.tsx for why that replaced the previous per-kind
  // fallbacks (SheetJS grid / mammoth HTML / BetterOffice's own canvas).
  if (kind === "xlsx" || kind === "docx" || kind === "pptx") {
    const ref = parseFileUrl(fileUrl);
    const fallback = <OoxmlViewer kind={kind} fileUrl={fileUrl} />;
    if (!ref) return fallback;
    return (
      <BetterOfficeEditor
        kind={kind}
        fileUrl={fileUrl}
        threadId={ref.threadId}
        path={ref.path}
        editMode={editMode}
        fallback={fallback}
      />
    );
  }

  // Anything else we can't preview — offer a download.
  return <UnsupportedView fileUrl={fileUrl} fileName={fileName} />;
}

export type Kind =
  | "html" | "pdf" | "image" | "text" | "csv" | "xlsx" | "docx" | "pptx" | "other";

// Exported so callers outside the viewer (AppPanel's own Download button)
// can make kind-specific UI decisions — e.g. a PDF renders via a plain
// <iframe>, which means the *browser's own* native PDF viewer already
// provides a prominent download control inside it, making a second one in
// our surrounding chrome pure duplication.
export function artifactKind(name: string, mime?: string): Kind {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (ext === "html" || ext === "htm" || mime === "text/html") return "html";
  if (ext === "pdf" || mime === "application/pdf") return "pdf";
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext) || mime?.startsWith("image/"))
    return "image";
  if (ext === "csv") return "csv";
  if (["txt", "md", "json", "log", "py", "js", "ts", "yaml", "yml"].includes(ext)) return "text";
  if (ext === "xlsx" || ext === "xls") return "xlsx";
  if (ext === "docx") return "docx";
  if (ext === "pptx") return "pptx";
  return "other";
}

// Extract the thread id + session-relative path back out of a workspace file
// URL (`/api/backend/workspace/file?thread_id=…&path=…`).
function parseFileUrl(fileUrl: string): { threadId: string; path: string } | null {
  try {
    const u = new URL(fileUrl, window.location.origin);
    const threadId = u.searchParams.get("thread_id");
    const path = u.searchParams.get("path");
    if (!threadId || !path) return null;
    return { threadId, path };
  } catch {
    return null;
  }
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full w-full items-center justify-center p-6 text-center text-sm text-(--muted)">
      {children}
    </div>
  );
}

// Model-generated HTML reports reference sibling images/assets by RELATIVE
// filename (e.g. <img src='chart.png'>). Inside the served iframe those don't
// resolve (the served URL isn't a real directory), so the charts show broken.
// Fetch the HTML, rewrite relative src/href to the workspace file endpoint for
// the same session dir, and render via srcdoc.
function HtmlView({ fileUrl, fileName }: { fileUrl: string; fileName: string }) {
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetch(fileUrl)
      .then((r) =>
        r.ok
          ? r.text()
          : Promise.reject(new Error(r.status === 404 ? "notfound" : String(r.status))),
      )
      .then((text) => alive && setHtml(rewriteRelativeUrls(text, fileUrl)))
      .catch((e) => alive && setError(e.message === "notfound" ? "notfound" : "error"));
    return () => {
      alive = false;
    };
  }, [fileUrl]);

  if (error === "notfound")
    return <Centered>This file no longer exists.</Centered>;
  if (error) return <Centered>Couldn&apos;t load this page.</Centered>;
  if (html === null)
    return (
      <Centered>
        <Loader2 className="h-5 w-5 animate-spin" />
      </Centered>
    );
  return (
    <iframe
      srcDoc={html}
      sandbox="allow-scripts allow-popups"
      className="h-full w-full border-none bg-white"
      title={fileName}
    />
  );
}

// Rewrite relative `src`/`href` attribute values to absolute workspace-file
// URLs, resolved against the HTML file's own directory. Absolute URLs
// (http(s)://, //, /, data:, #, blob:) are left untouched.
function rewriteRelativeUrls(html: string, htmlFileUrl: string): string {
  const parsed = new URL(htmlFileUrl, window.location.origin);
  const threadId = parsed.searchParams.get("thread_id") ?? "";
  const htmlPath = parsed.searchParams.get("path") ?? "";
  const dir = htmlPath.includes("/") ? htmlPath.slice(0, htmlPath.lastIndexOf("/") + 1) : "";
  const isAbsolute = (u: string) => /^(https?:|\/\/|\/|data:|blob:|#|mailto:)/i.test(u);
  const toWorkspace = (rel: string) => {
    const clean = rel.replace(/^\.\//, "");
    const path = `${dir}${clean}`;
    return `${parsed.origin}${parsed.pathname}?thread_id=${encodeURIComponent(
      threadId,
    )}&path=${encodeURIComponent(path)}`;
  };
  return html.replace(
    /(\b(?:src|href)\s*=\s*)(["'])(.*?)\2/gi,
    (match, prefix, quote, url) =>
      isAbsolute(url) ? match : `${prefix}${quote}${toWorkspace(url)}${quote}`,
  );
}

function TextView({ fileUrl, csv }: { fileUrl: string; csv: boolean }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let alive = true;
    fetch(fileUrl)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((t) => alive && setText(t))
      .catch(() => alive && setError(true));
    return () => {
      alive = false;
    };
  }, [fileUrl]);

  if (error) return <Centered>Couldn&apos;t load this file.</Centered>;
  if (text === null)
    return (
      <Centered>
        <Loader2 className="h-5 w-5 animate-spin" />
      </Centered>
    );

  if (csv) {
    const rows = text
      .trim()
      .split(/\r?\n/)
      .map((line) => line.split(","));
    return <GridTable rows={rows} />;
  }
  return (
    <pre className="h-full w-full overflow-auto bg-(--code-bg) p-4 font-mono text-xs text-(--code-fg)">
      {text}
    </pre>
  );
}

function GridTable({ rows }: { rows: string[][] }) {
  if (rows.length === 0) return <Centered>Empty.</Centered>;
  const [header, ...body] = rows;
  return (
    <table className="w-full border-collapse text-xs">
      <thead className="sticky top-0 bg-(--card)">
        <tr>
          <th className="border border-(--border) px-2 py-1 text-(--muted)"></th>
          {header.map((cell, i) => (
            <th
              key={i}
              className="border border-(--border) px-2 py-1 text-left font-semibold text-foreground"
            >
              {String(cell)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {body.map((row, r) => (
          <tr key={r} className="even:bg-(--card)/40">
            <td className="border border-(--border) px-2 py-1 text-center text-(--muted)">{r + 2}</td>
            {header.map((_, c) => (
              <td key={c} className="border border-(--border) px-2 py-1 text-foreground">
                {String(row[c] ?? "")}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function UnsupportedView({ fileUrl, fileName }: { fileUrl: string; fileName: string }) {
  return (
    <Centered>
      <div className="flex flex-col items-center gap-3">
        <FileWarning className="h-8 w-8 text-(--muted)" />
        <p>No inline preview for this file type yet.</p>
        <a
          href={fileUrl}
          download={fileName}
          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-foreground"
          style={{ background: "var(--badge-bg)" }}
        >
          <Download className="h-4 w-4" /> Download {fileName}
        </a>
      </div>
    </Centered>
  );
}
