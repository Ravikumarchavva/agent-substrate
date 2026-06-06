"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import mermaid from "mermaid";
import {
  Copy,
  Check,
  ZoomIn,
  ZoomOut,
  RotateCcw,
  ChevronUp,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Download,
  Pencil,
  Maximize2,
  Play,
  X,
  Code,
  Columns,
} from "lucide-react";
import { useTheme } from "@/contexts/ThemeContext";

mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });

const ZOOM_STEP = 0.25;
const PAN_STEP = 60;
const MAX_ZOOM = 4;
const MIN_ZOOM = 0.25;

interface MermaidProps {
  chart: string;
}

export function Mermaid({ chart }: MermaidProps) {
  const baseId = useId().replace(/:/g, "");
  const seq = useRef(0);

  // Raw chart source state (managed locally for editor)
  const [chartCode, setChartCode] = useState(chart);
  // Compiled code triggering rendering updates
  const [compiledCode, setCompiledCode] = useState(chart);

  const [svg, setSvg] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [errorDetails, setErrorDetails] = useState<string[]>([]);
  const [copied, setCopied] = useState(false);

  // Modes
  const [isEditing, setIsEditing] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [showCodeInFullscreen, setShowCodeInFullscreen] = useState(false);

  // Zoom & Pan states for inline view
  const [scale, setScale] = useState(1);
  const [translate, setTranslate] = useState({ x: 0, y: 0 });

  // Zoom & Pan states for fullscreen view
  const [fsScale, setFsScale] = useState(1);
  const [fsTranslate, setFsTranslate] = useState({ x: 0, y: 0 });

  // Fullscreen mouse drag-to-pan states
  const [isDragging, setIsDragging] = useState(false);
  const dragStart = useRef({ x: 0, y: 0 });

  const { theme } = useTheme();

  // Keep editor content in sync with new incoming charts if not editing
  const [prevChart, setPrevChart] = useState(chart);
  if (chart !== prevChart) {
    setPrevChart(chart);
    if (!isEditing) {
      setChartCode(chart);
      setCompiledCode(chart);
    }
  }

  // Mermaid compiler effect
  useEffect(() => {
    let active = true;
    const id = `mermaid-${baseId}-${++seq.current}`;

    const hasInit = /^%%\s*\{/.test(compiledCode.trimStart());
    const themedChart = hasInit
      ? compiledCode
      : `%%{init:{'theme':'${theme === "dark" ? "dark" : "default"}'}}%%\n${compiledCode}`;

    mermaid
      .render(id, themedChart)
      .then(({ svg: rendered }) => {
        document.getElementById(id)?.remove();
        if (active) {
          setSvg(rendered);
          setError(null);
          setErrorDetails([]);
          setScale(1);
          setTranslate({ x: 0, y: 0 });
          setFsScale(1);
          setFsTranslate({ x: 0, y: 0 });
        }
      })
      .catch((err) => {
        document.getElementById(id)?.remove();
        console.warn("Mermaid rendering error:", err);

        let msg = "Could not render diagram.";
        if (err instanceof Error) {
          msg = err.message;
        } else if (err && typeof err === "object") {
          msg = (err as { message?: string }).message || String(err);
        } else if (typeof err === "string") {
          msg = err;
        }

        const rawLines = msg.split("\n").map(l => l.trim()).filter(Boolean);
        const cleanMessage = rawLines[0] || "Could not render diagram.";

        if (active) {
          setError(cleanMessage);
          setErrorDetails(rawLines);
          setSvg(""); // Clear previous diagram
        }
      });

    return () => {
      active = false;
    };
  }, [compiledCode, baseId, theme]);

  // Inline Panning/Zooming
  const zoomIn = () => setScale(s => Math.min(+(s + ZOOM_STEP).toFixed(2), MAX_ZOOM));
  const zoomOut = () => setScale(s => Math.max(+(s - ZOOM_STEP).toFixed(2), MIN_ZOOM));
  const reset = () => { setScale(1); setTranslate({ x: 0, y: 0 }); };
  const panUp = () => setTranslate(t => ({ ...t, y: t.y + PAN_STEP }));
  const panDown = () => setTranslate(t => ({ ...t, y: t.y - PAN_STEP }));
  const panLeft = () => setTranslate(t => ({ x: t.x + PAN_STEP, y: t.y }));
  const panRight = () => setTranslate(t => ({ x: t.x - PAN_STEP, y: t.y }));

  const handleCopy = async () => {
    const codeToCopy = isEditing ? chartCode : compiledCode;
    await navigator.clipboard.writeText(codeToCopy);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const downloadPng = () => {
    if (!svg) return;

    const img = new Image();
    const svgBlob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(svgBlob);

    img.onload = () => {
      const canvas = document.createElement("canvas");
      const parser = new DOMParser();
      const doc = parser.parseFromString(svg, "image/svg+xml");
      const svgEl = doc.querySelector("svg");

      let width = 800;
      let height = 600;
      if (svgEl) {
        const viewBox = svgEl.getAttribute("viewBox");
        const attrWidth = svgEl.getAttribute("width");
        const attrHeight = svgEl.getAttribute("height");

        if (attrWidth && attrHeight) {
          width = parseFloat(attrWidth);
          height = parseFloat(attrHeight);
        } else if (viewBox) {
          const parts = viewBox.trim().split(/\s+/);
          if (parts.length === 4) {
            width = parseFloat(parts[2]);
            height = parseFloat(parts[3]);
          }
        }
      }

      const scaleFactor = 2;
      canvas.width = width * scaleFactor;
      canvas.height = height * scaleFactor;

      const ctx = canvas.getContext("2d");
      if (ctx) {
        ctx.fillStyle = theme === "dark" ? "#1f1f1f" : "#ffffff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        ctx.scale(scaleFactor, scaleFactor);
        ctx.drawImage(img, 0, 0, width, height);

        canvas.toBlob((blob) => {
          if (blob) {
            const pngUrl = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = pngUrl;
            a.download = "diagram.png";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(pngUrl);
          }
        }, "image/png");
      }
      URL.revokeObjectURL(url);
    };

    img.onerror = () => {
      URL.revokeObjectURL(url);
    };

    img.src = url;
  };

  // Fullscreen Mouse Drag-to-Pan Handlers
  const handleMouseDown = (e: React.MouseEvent) => {
    if (!svg) return;
    setIsDragging(true);
    dragStart.current = { x: e.clientX - fsTranslate.x, y: e.clientY - fsTranslate.y };
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging) return;
    setFsTranslate({
      x: e.clientX - dragStart.current.x,
      y: e.clientY - dragStart.current.y
    });
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  const btnCls = "btn-icon flex items-center justify-center w-6 h-6 rounded-lg text-(--muted) hover:text-foreground hover:bg-(--card-hover) transition-colors cursor-pointer";
  const headerBtnCls = "btn-icon flex items-center justify-center w-7 h-7 rounded-md text-(--muted) hover:text-foreground hover:bg-(--badge-bg) transition-colors cursor-pointer";

  // Fullscreen Modal Portal
  const fullscreenPortal = isFullscreen && typeof document !== "undefined" && createPortal(
    <div className="fixed inset-0 z-50 flex flex-col bg-background/98 backdrop-blur-md text-foreground">
      {/* Header bar */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-(--border) bg-(--card)">
        <div className="flex items-center gap-4">
          <button
            onClick={() => setIsFullscreen(false)}
            className="btn-icon flex items-center justify-center w-8 h-8 rounded-xl hover:bg-(--card-hover) text-(--muted) hover:text-foreground cursor-pointer transition-colors"
            title="Close"
          >
            <X className="w-5 h-5" />
          </button>
          
          <button
            onClick={() => setShowCodeInFullscreen(!showCodeInFullscreen)}
            className="flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-foreground/5 hover:bg-foreground/10 text-foreground cursor-pointer transition-colors"
          >
            <Columns className="w-3.5 h-3.5" />
            {showCodeInFullscreen ? "Hide code" : "Show code"}
          </button>
        </div>

        <div className="flex items-center gap-2">
          {/* Copy code */}
          <button
            onClick={handleCopy}
            className="btn-icon flex items-center justify-center w-8 h-8 rounded-xl hover:bg-(--card-hover) text-(--muted) hover:text-foreground cursor-pointer transition-colors"
            title="Copy diagram source"
          >
            {copied ? <Check className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4" />}
          </button>
          
          {/* Download PNG */}
          <button
            onClick={downloadPng}
            className="btn-icon flex items-center justify-center w-8 h-8 rounded-xl hover:bg-(--card-hover) text-(--muted) hover:text-foreground cursor-pointer transition-colors"
            title="Download PNG"
          >
            <Download className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Main split viewport */}
      <div className="flex-1 flex overflow-hidden min-h-0">
        {/* Left Pane: Code Editor */}
        {showCodeInFullscreen && (
          <div className="w-80 sm:w-96 border-r border-(--border) flex flex-col bg-(--card)">
            <div className="px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-(--muted) border-b border-(--border)">
              Code Editor
            </div>
            <textarea
              value={chartCode}
              onChange={(e) => setChartCode(e.target.value)}
              className="flex-1 p-4 font-mono text-[11px] bg-transparent text-foreground resize-none outline-none border-none leading-relaxed"
              placeholder="Write diagram syntax..."
            />
            <div className="p-3 border-t border-(--border) flex flex-col gap-2 bg-(--card)">
              <span className="text-[10px] text-(--muted) font-mono text-center">Note: Edits are local only and not visible to the agent</span>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setCompiledCode(chartCode)}
                  className="flex items-center gap-1.5 px-4 py-2 bg-foreground text-background font-semibold text-xs rounded-xl hover:opacity-90 transition-opacity cursor-pointer"
                >
                  <Play className="w-3 h-3 fill-current" />
                  Render
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Right Pane: Large diagram viewport */}
        <div
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          className={`flex-1 relative overflow-hidden bg-background/50 flex items-center justify-center p-8 select-none ${svg ? "cursor-grab" : ""} ${isDragging ? "cursor-grabbing" : ""}`}
        >
          {svg ? (
            <div
              style={{
                transform: `translate(${fsTranslate.x}px, ${fsTranslate.y}px) scale(${fsScale})`,
                transformOrigin: "center center",
                transition: "transform 0.15s ease",
              }}
              className="w-full h-full flex items-center justify-center [&>svg]:!w-full [&>svg]:!h-full [&>svg]:!max-w-[85%] [&>svg]:!max-h-[75vh]"
              dangerouslySetInnerHTML={{ __html: svg }}
            />
          ) : error ? (
            <div className="text-center max-w-lg p-6 rounded-2xl bg-red-500/5 border border-red-500/10">
              <div className="text-sm font-semibold text-red-500 mb-2">Invalid or unsupported diagram.</div>
              {errorDetails.length > 0 && (
                <div className="text-left font-mono text-[10px] text-foreground/70 max-h-60 overflow-y-auto whitespace-pre-wrap leading-relaxed">
                  {errorDetails.join("\n")}
                </div>
              )}
            </div>
          ) : (
            <div className="animate-pulse text-xs text-(--muted)">Generating diagram...</div>
          )}

          {/* Fullscreen Zoom overlay */}
          {svg && (
            <div className="absolute bottom-4 right-4 flex flex-col gap-1 bg-(--card)/90 backdrop-blur-md p-1.5 rounded-xl border border-(--border) shadow-lg z-10" onMouseDown={e => e.stopPropagation()}>
              <button
                onClick={() => setFsScale(s => Math.min(s + ZOOM_STEP, MAX_ZOOM))}
                className="btn-icon flex items-center justify-center w-7 h-7 rounded-lg hover:bg-(--card-hover) text-(--muted) hover:text-foreground cursor-pointer transition-colors"
                title="Zoom in"
              >
                <ZoomIn className="w-4 h-4" />
              </button>
              <button
                onClick={() => setFsScale(s => Math.max(s - ZOOM_STEP, MIN_ZOOM))}
                className="btn-icon flex items-center justify-center w-7 h-7 rounded-lg hover:bg-(--card-hover) text-(--muted) hover:text-foreground cursor-pointer transition-colors"
                title="Zoom out"
              >
                <ZoomOut className="w-4 h-4" />
              </button>
              <button
                onClick={() => { setFsScale(1); setFsTranslate({ x: 0, y: 0 }); }}
                className="btn-icon flex items-center justify-center w-7 h-7 rounded-lg hover:bg-(--card-hover) text-(--muted) hover:text-foreground cursor-pointer transition-colors"
                title="Reset zoom"
              >
                <RotateCcw className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body
  );

  return (
    <div className="mermaid-wrap group/mermaid relative my-4 w-full border border-(--border) bg-(--card) rounded-2xl overflow-hidden flex flex-col">
      {/* Card Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-(--border) bg-black/[0.01] dark:bg-white/[0.01]">
        <div className="flex items-center gap-2">
          <Code className="w-3.5 h-3.5 text-(--muted)" />
          <span className="text-xs font-semibold text-foreground/80">Mermaid</span>
        </div>

        <div className="flex items-center gap-1">
          {/* Run/Play (only in edit mode) */}
          {isEditing && (
            <button
              onClick={() => setCompiledCode(chartCode)}
              className={headerBtnCls}
              title="Run code"
            >
              <Play className="w-3.5 h-3.5 fill-current text-emerald-500" />
            </button>
          )}

          {/* Edit toggle */}
          <button
            onClick={() => setIsEditing(!isEditing)}
            className={`${headerBtnCls} ${isEditing ? "text-foreground bg-(--badge-bg)" : ""}`}
            title="Edit code inline"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>

          {/* Copy code */}
          <button
            onClick={handleCopy}
            className={headerBtnCls}
            title="Copy diagram source"
          >
            {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
          </button>

          {/* Download PNG */}
          {svg && (
            <button
              onClick={downloadPng}
              className={headerBtnCls}
              title="Download PNG"
            >
              <Download className="w-3.5 h-3.5" />
            </button>
          )}

          {/* Fullscreen */}
          <button
            onClick={() => setIsFullscreen(true)}
            className={headerBtnCls}
            title="Fullscreen view"
          >
            <Maximize2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Main card body */}
      <div className="relative p-6 bg-black/[0.01] dark:bg-white/[0.01] flex-1 flex flex-col">
        {isEditing ? (
          /* Inline Editor Mode */
          <div className="w-full flex flex-col gap-3 min-h-[220px]">
            <textarea
              value={chartCode}
              onChange={(e) => setChartCode(e.target.value)}
              className="w-full flex-1 p-4 font-mono text-[11px] bg-transparent text-foreground border border-(--border) rounded-xl outline-none resize-y leading-relaxed min-h-[160px]"
              placeholder="Write your diagram code..."
            />
            <div className="flex justify-between items-center">
              <span className="text-[10px] text-(--muted) font-mono">Note: Edits are local only and not visible to the agent</span>
              <button
                onClick={() => setCompiledCode(chartCode)}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-foreground text-background font-semibold text-xs rounded-xl hover:opacity-90 transition-opacity cursor-pointer"
              >
                <Play className="w-3 h-3 fill-current" />
                Run
              </button>
            </div>
          </div>
        ) : svg ? (
          /* Inline Preview Mode */
          <>
            {/* Viewport container */}
            <div className="w-full overflow-hidden py-4">
              <div
                style={{
                  transform: `translate(${translate.x}px, ${translate.y}px) scale(${scale})`,
                  transformOrigin: "center center",
                  transition: "transform 0.15s ease",
                }}
                dangerouslySetInnerHTML={{ __html: svg }}
              />
            </div>

            {/* Compact zoom/pan overlay pad (fades in on hover) */}
            <div className="absolute bottom-3 right-3 z-10 grid grid-cols-3 gap-0.5 bg-(--card)/85 backdrop-blur-md p-1 rounded-xl border border-(--border) shadow-md opacity-100 sm:opacity-0 sm:group-hover/mermaid:opacity-100 transition-opacity duration-200">
              <div className="w-6 h-6" />
              <button onClick={panUp} className={btnCls} title="Pan up"><ChevronUp className="w-3 h-3" /></button>
              <button onClick={zoomIn} className={btnCls} title="Zoom in"><ZoomIn className="w-3 h-3" /></button>

              <button onClick={panLeft} className={btnCls} title="Pan left"><ChevronLeft className="w-3 h-3" /></button>
              <button onClick={reset} className={btnCls} title="Reset"><RotateCcw className="w-3 h-3" /></button>
              <button onClick={panRight} className={btnCls} title="Pan right"><ChevronRight className="w-3 h-3" /></button>

              <div className="w-6 h-6" />
              <button onClick={panDown} className={btnCls} title="Pan down"><ChevronDown className="w-3 h-3" /></button>
              <button onClick={zoomOut} className={btnCls} title="Zoom out"><ZoomOut className="w-3 h-3" /></button>
            </div>
          </>
        ) : error ? (
          /* Syntax Error details mode */
          <div className="flex flex-col items-center justify-center p-8 bg-red-500/[0.02] rounded-xl border border-dashed border-red-500/20 min-h-[200px] w-full text-center">
            <div className="text-sm font-semibold text-red-500 mb-2">Invalid or unsupported diagram.</div>
            {errorDetails.length > 0 && (
              <div className="text-left max-w-lg w-full bg-red-500/5 border border-red-500/10 p-4 rounded-xl mt-2">
                <span className="text-xs font-semibold text-foreground/80 block mb-1">Problem:</span>
                <ul className="list-disc pl-4 space-y-1.5">
                  {errorDetails.slice(0, 3).map((detail, idx) => (
                    <li key={idx} className="text-[11px] font-mono text-foreground/70 break-words leading-normal">
                      {detail}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ) : (
          <div className="animate-pulse text-xs text-(--muted) py-8 text-center">Generating diagram…</div>
        )}
      </div>

      {/* Fullscreen modal portal mounting */}
      {fullscreenPortal}
    </div>
  );
}
