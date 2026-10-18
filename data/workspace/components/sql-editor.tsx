"use client";
import dynamic from "next/dynamic";

type MonacoWindow = Window & {
  SubstrateMonaco?: typeof import("monaco-editor");
  MonacoEnvironment?: { getWorkerUrl: () => string };
};

async function loadLocalEditor() {
  const browser = window as MonacoWindow;
  browser.MonacoEnvironment = {
    getWorkerUrl: () => "/monaco/editor.worker.js",
  };
  if (!browser.SubstrateMonaco) {
    const stylesheet = document.createElement("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = "/monaco/editor.css";
    document.head.appendChild(stylesheet);
    await new Promise<void>((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/monaco/editor.js";
      script.onload = () => resolve();
      script.onerror = () =>
        reject(new Error("Cannot load local SQL editor assets"));
      document.head.appendChild(script);
    });
  }
  if (!browser.SubstrateMonaco)
    throw new Error("SQL editor did not initialize");
  return browser.SubstrateMonaco;
}

export const Editor = dynamic(
  async () => {
    const [editorPackage, monaco] = await Promise.all([
      import("@monaco-editor/react"),
      loadLocalEditor(),
    ]);
    editorPackage.loader.config({ monaco });
    return editorPackage.default;
  },
  { ssr: false, loading: () => <p className="empty">Loading SQL editor…</p> },
);

export function configureEditorThemes(monaco: typeof import("monaco-editor")) {
  for (const dark of [false, true]) {
    monaco.editor.defineTheme(dark ? "substrate-dark" : "substrate-light", {
      base: dark ? "vs-dark" : "vs",
      inherit: true,
      rules: [
        { token: "keyword", foreground: dark ? "B497F8" : "7C3AED" },
        { token: "number", foreground: dark ? "F0A060" : "C66425" },
      ],
      colors: {
        "editor.background": dark ? "#0e0e11" : "#ffffff",
        "editor.foreground": dark ? "#f1f0f3" : "#18171b",
        "editorLineNumber.foreground": dark ? "#77717f" : "#a9a3af",
        "editor.lineHighlightBackground": dark ? "#151519" : "#f7f7f8",
      },
    });
  }
}
