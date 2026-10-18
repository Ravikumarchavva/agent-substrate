import ReactMarkdown from "react-markdown";

/** An agent's message: its formatting (lists, bold, code, links) shown, at chat-bubble size. */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="space-y-2 text-sm wrap-break-word [&_ol]:list-decimal [&_ol]:pl-5 [&_ul]:list-disc [&_ul]:pl-5">
      <ReactMarkdown
        components={{
          p: ({ children: c }) => <p className="whitespace-pre-wrap">{c}</p>,
          a: ({ children: c, href }) => (
            <a href={href} target="_blank" rel="noreferrer" className="text-accent underline underline-offset-2">
              {c}
            </a>
          ),
          code: ({ children: c }) => <code className="rounded bg-badge px-1 py-0.5 text-xs">{c}</code>,
          pre: ({ children: c }) => <pre className="scroll-area overflow-x-auto rounded-lg bg-code p-3 text-xs text-code-foreground [&_code]:bg-transparent [&_code]:p-0">{c}</pre>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
