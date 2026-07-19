"use client";

import { useEffect, useRef, useState } from "react";
import Editor from "@monaco-editor/react";
import { Loader2, Save, Check, AlertTriangle } from "lucide-react";
import { useTheme } from "@/contexts/ThemeContext";

// Map file extension → Monaco language id. Anything unlisted falls back to
// plaintext, which still gives a clean editable surface.
const LANGS: Record<string, string> = {
  py: "python",
  js: "javascript",
  jsx: "javascript",
  ts: "typescript",
  tsx: "typescript",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  md: "markdown",
  html: "html",
  css: "css",
  sh: "shell",
  bash: "shell",
  sql: "sql",
  txt: "plaintext",
  log: "plaintext",
};

function langFor(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  return LANGS[ext] ?? "plaintext";
}

type SaveState = "idle" | "saving" | "saved" | "conflict" | "error";

/**
 * Editable code/text view backed by Monaco. Loads the file (capturing the
 * `X-File-Checksum` it opened with), and on save PUTs the buffer back with
 * `X-Base-Checksum` for optimistic concurrency — a 409 (the agent rewrote the
 * file meanwhile) surfaces a reload prompt rather than clobbering. Save with
 * the toolbar button or ⌘/Ctrl-S.
 */
export function CodeEditorView({
  fileUrl,
  fileName,
  threadId,
  path,
}: {
  fileUrl: string;
  fileName: string;
  threadId: string;
  path: string;
}) {
  const { theme } = useTheme();
  const [text, setText] = useState<string | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [save, setSave] = useState<SaveState>("idle");
  const baseChecksum = useRef<string>("");
  // Latest buffer, read synchronously by the ⌘S handler (avoids stale closures).
  const bufferRef = useRef<string>("");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch(fileUrl);
        if (!r.ok) throw new Error(String(r.status));
        baseChecksum.current = r.headers.get("X-File-Checksum") ?? "";
        const t = await r.text();
        if (!alive) return;
        setText(t);
        bufferRef.current = t;
        setDirty(false);
        setSave("idle");
      } catch {
        if (alive) setLoadError(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, [fileUrl]);

  const doSave = async () => {
    if (save === "saving") return;
    setSave("saving");
    try {
      const r = await fetch(
        `/api/backend/workspace/file?thread_id=${encodeURIComponent(
          threadId,
        )}&path=${encodeURIComponent(path)}`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/octet-stream",
            "X-Base-Checksum": baseChecksum.current,
          },
          body: bufferRef.current,
        },
      );
      if (r.status === 409) {
        setSave("conflict");
        return;
      }
      if (!r.ok) throw new Error(String(r.status));
      const { checksum } = (await r.json()) as { checksum: string; seq: number };
      baseChecksum.current = checksum;
      setDirty(false);
      setSave("saved");
      setTimeout(() => setSave((s) => (s === "saved" ? "idle" : s)), 1500);
    } catch {
      setSave("error");
    }
  };

  if (loadError)
    return (
      <div className="flex h-full w-full items-center justify-center p-6 text-center text-sm text-(--muted)">
        Couldn&apos;t load this file.
      </div>
    );
  if (text === null)
    return (
      <div className="flex h-full w-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-(--muted)" />
      </div>
    );

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-(--border) bg-(--card) px-3 py-1.5">
        <span className="truncate text-xs text-(--muted)">{langFor(fileName)}</span>
        <div className="flex items-center gap-2">
          {save === "conflict" && (
            <span className="flex items-center gap-1 text-xs text-amber-500">
              <AlertTriangle className="h-3.5 w-3.5" /> Assistant changed this file
              <button
                onClick={() => {
                  // Re-fetch the current canonical, discarding local edits.
                  setText(null);
                  setSave("idle");
                  fetch(fileUrl)
                    .then(async (r) => {
                      baseChecksum.current = r.headers.get("X-File-Checksum") ?? "";
                      const t = await r.text();
                      setText(t);
                      bufferRef.current = t;
                      setDirty(false);
                    })
                    .catch(() => setLoadError(true));
                }}
                className="ml-1 rounded px-1.5 py-0.5 text-foreground underline"
              >
                Reload
              </button>
            </span>
          )}
          {save === "error" && (
            <span className="text-xs text-red-500">Save failed</span>
          )}
          <button
            onClick={doSave}
            disabled={!dirty || save === "saving"}
            className="flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-(--card-hover) disabled:opacity-40"
            style={{ background: "var(--badge-bg)" }}
            title="Save (⌘/Ctrl-S)"
          >
            {save === "saving" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : save === "saved" ? (
              <Check className="h-3.5 w-3.5 text-green-500" />
            ) : (
              <Save className="h-3.5 w-3.5" />
            )}
            {save === "saved" ? "Saved" : dirty ? "Save" : "Saved"}
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        <Editor
          defaultLanguage={langFor(fileName)}
          value={text}
          theme={theme === "dark" ? "vs-dark" : "light"}
          options={{
            minimap: { enabled: false },
            fontSize: 13,
            scrollBeyondLastLine: false,
            automaticLayout: true,
            tabSize: 2,
          }}
          onMount={(editor, monaco) => {
            editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
              void doSave();
            });
          }}
          onChange={(v) => {
            bufferRef.current = v ?? "";
            setDirty(true);
            if (save === "saved") setSave("idle");
          }}
        />
      </div>
    </div>
  );
}
