"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { Copy, Check, RotateCw, ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { ToolCall } from "@/types";
import { McpAppRenderer } from "@/components/McpAppRenderer";
import "katex/dist/katex.min.css";

type Props = {
  role: "user" | "assistant";
  content: string;
  reasoning?: string;
  timestamp?: Date;
  toolCalls?: ToolCall[];
  isToolExecuting?: boolean;
  onRegenerate?: () => void;
  /** Called when an MCP App sends a context update (submitResult / ui/update-model-context) */
  onMcpAppResult?: (toolName: string, result: unknown) => void;
};

export function MessageBubble({ 
  role, 
  content, 
  reasoning, 
  timestamp, 
  toolCalls,
  isToolExecuting,
  onRegenerate,
  onMcpAppResult 
}: Props) {
  const isUser = role === "user";
  const [copied, setCopied] = useState(false);
  const [expandedTools, setExpandedTools] = useState<Record<string, boolean>>({});
  
  // Ensure content is always a valid string
  const safeContent = typeof content === 'string' ? content : String(content || "");
  const safeReasoning = typeof reasoning === 'string' ? reasoning : String(reasoning || "");

  const copyToClipboard = async () => {
    await navigator.clipboard.writeText(safeContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const formatTime = (date: Date) => {
    return new Intl.DateTimeFormat('en-US', {
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    }).format(date);
  };

  const toggleTool = (toolId: string) => {
    setExpandedTools(prev => ({ ...prev, [toolId]: !prev[toolId] }));
  };

  const getToolIcon = (toolName: string) => {
    if (toolName.toLowerCase().includes('search')) return '🔍';
    if (toolName.toLowerCase().includes('calculator')) return '🧮';
    if (toolName.toLowerCase().includes('time')) return '🕐';
    if (toolName.toLowerCase().includes('code')) return '💻';
    return '🔧';
  };

  return (
    <div className="group relative px-4 py-6 hover:bg-[var(--card-hover)] transition-colors">
      <div className="max-w-3xl mx-auto">
        <div className="flex gap-4">
          {/* Avatar */}
          <div className="shrink-0">
            <div
              className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold ${
                isUser
                  ? "bg-linear-to-br from-blue-500 to-purple-600"
                  : "bg-linear-to-br from-emerald-500 to-teal-600"
              }`}
            >
              {isUser ? "U" : "AI"}
            </div>
          </div>

          {/* Content */}
          <div className="flex-1 min-w-0 space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold">
                {isUser ? "You" : "Assistant"}
              </span>
              {timestamp && (
                <span className="text-xs text-zinc-500">
                  {formatTime(timestamp)}
                </span>
              )}
            </div>

            {/* Tool Calls - Show during execution */}
            {!isUser && toolCalls && toolCalls.length > 0 && (
              <div className="space-y-2">
                {toolCalls.map((tool) => {
                  const hasApp = tool._meta?.ui?.httpUrl;
                  
                  return (
                    <div key={tool.id}>
                      <div
                        className="border border-[var(--border)] rounded-lg bg-[var(--card)] overflow-hidden"
                      >
                        <button
                          onClick={() => toggleTool(tool.id)}
                          className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-[var(--card-hover)] transition-colors"
                        >
                          {isToolExecuting ? (
                            <Loader2 className="w-4 h-4 animate-spin text-emerald-500" />
                          ) : expandedTools[tool.id] ? (
                            <ChevronDown className="w-4 h-4 text-zinc-400" />
                          ) : (
                            <ChevronRight className="w-4 h-4 text-zinc-400" />
                          )}
                          <span className="text-lg">{getToolIcon(tool.name)}</span>
                          <span className="text-sm font-medium text-zinc-300">
                            {tool.name.replace(/_/g, ' ')}
                          </span>
                          {hasApp && (
                            <span className="ml-1 text-[10px] px-1.5 py-0.5 rounded bg-emerald-900/50 text-emerald-400 border border-emerald-800">
                              Interactive
                            </span>
                          )}
                          {isToolExecuting && (
                            <span className="text-xs text-zinc-500 ml-auto">Running...</span>
                          )}
                        </button>
                        
                        {expandedTools[tool.id] && (
                          <div className="px-3 py-2 border-t border-[var(--border)] space-y-2">
                            <div>
                              <div className="text-xs font-semibold text-zinc-500 mb-1">Arguments</div>
                              <pre className="text-xs text-zinc-400 bg-[var(--input-bg)] p-2 rounded overflow-x-auto">
                                {JSON.stringify(
                                  typeof tool.arguments === 'string' 
                                    ? JSON.parse(tool.arguments) 
                                    : tool.arguments,
                                  null,
                                  2
                                )}
                              </pre>
                            </div>
                            {tool.result && (
                              <div>
                                <div className="text-xs font-semibold text-zinc-500 mb-1">Result</div>
                                <div className="text-xs text-zinc-400 bg-[var(--input-bg)] p-2 rounded max-h-40 overflow-y-auto">
                                  {tool.result}
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                      </div>

                      {/* MCP App interactive UI */}
                      {hasApp && (
                        <McpAppRenderer
                          httpUrl={tool._meta!.ui!.httpUrl}
                          toolName={tool.name}
                          toolArguments={
                            typeof tool.arguments === 'string'
                              ? JSON.parse(tool.arguments)
                              : tool.arguments
                          }
                          onResult={(result) => onMcpAppResult?.(tool.name, result)}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* Show reasoning/thinking process if available */}
            {safeReasoning && !isUser && (
              <details className="text-xs text-zinc-400 border-l-2 border-zinc-700 pl-3 py-2">
                <summary className="cursor-pointer hover:text-zinc-300 font-medium">
                  💭 Thinking process
                </summary>
                <div className="mt-2 whitespace-pre-wrap font-mono text-zinc-500">
                  {safeReasoning}
                </div>
              </details>
            )}

            {/* Main message content */}
            {safeContent && (
              <div className="prose prose-invert prose-sm max-w-none">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm, remarkMath]}
                  rehypePlugins={[rehypeKatex]}
                >
                  {safeContent}
                </ReactMarkdown>
              </div>
            )}

            {/* Actions */}
            {!isUser && safeContent && (
              <div className="flex items-center gap-2 pt-2 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                  onClick={copyToClipboard}
                  className="p-1.5 hover:bg-[var(--card)] rounded text-zinc-400 hover:text-zinc-200 transition-colors"
                  title="Copy to clipboard"
                >
                  {copied ? (
                    <Check className="w-4 h-4" />
                  ) : (
                    <Copy className="w-4 h-4" />
                  )}
                </button>
                {onRegenerate && (
                  <button
                    onClick={onRegenerate}
                    className="p-1.5 hover:bg-[var(--card)] rounded text-zinc-400 hover:text-zinc-200 transition-colors"
                    title="Regenerate response"
                  >
                    <RotateCw className="w-4 h-4" />
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
