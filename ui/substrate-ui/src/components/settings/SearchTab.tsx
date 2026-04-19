"use client";

import { Search, SlidersHorizontal, Sparkles } from "lucide-react";

interface SearchTabProps {
  embeddingModel: string;
  setEmbeddingModel: (value: string) => void;
  rerankerModel: string;
  setRerankerModel: (value: string) => void;
  rerankLimit: number;
  setRerankLimit: (value: number) => void;
  contextualRag: boolean;
  setContextualRag: (value: boolean) => void;
  multipassIndexing: boolean;
  setMultipassIndexing: (value: boolean) => void;
}

const EMBEDDING_OPTIONS = [
  { id: "embed-english-light-v3.0", label: "embed-english-light-v3.0", caption: "Faster and lighter for simple tasks." },
  { id: "embed-english-v3.0", label: "embed-english-v3.0", caption: "Higher quality retrieval when recall matters more than speed." },
];

const RERANKER_OPTIONS = [
  { id: "none", label: "No reranker", caption: "Return embedding order directly." },
  { id: "mixedbread-xsmall", label: "MixedBread XSmall", caption: "Fastest, lightweight reranking." },
  { id: "mixedbread-base", label: "MixedBread Base", caption: "Balanced reranking for general workloads." },
  { id: "mixedbread-large", label: "MixedBread Large", caption: "Highest quality option for complex retrieval." },
];

function SelectCard({
  active,
  title,
  caption,
  onClick,
}: {
  active: boolean;
  title: string;
  caption: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-[24px] border p-5 text-left transition-all cursor-pointer ${active ? "border-(--accent)" : "border-(--border) hover:bg-(--card-hover)"}`}
      style={{ background: active ? "color-mix(in srgb, var(--accent) 6%, var(--card))" : "var(--card)", boxShadow: "var(--shadow-sm)" }}
    >
      <div className="text-base font-semibold text-foreground">{title}</div>
      <p className="mt-3 text-sm leading-6 text-(--muted)">{caption}</p>
      <div className="mt-4 text-xs font-medium text-(--muted)">{active ? "Selected model" : "Select model"}</div>
    </button>
  );
}

export function SearchTab({
  embeddingModel,
  setEmbeddingModel,
  rerankerModel,
  setRerankerModel,
  rerankLimit,
  setRerankLimit,
  contextualRag,
  setContextualRag,
  multipassIndexing,
  setMultipassIndexing,
}: SearchTabProps) {
  return (
    <div className="space-y-8">
      <div className="space-y-2">
        <h2 className="text-3xl font-semibold tracking-tight text-foreground">Search Settings</h2>
        <p className="max-w-3xl text-sm leading-6 text-(--muted)">
          Retrieval controls inspired by the reference admin screens. These preferences are stored locally for now, so the UI is ready before the backend indexing stack lands in this app.
        </p>
      </div>

      <div className="grid gap-5 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="space-y-5">
          <div className="rounded-[28px] p-6" style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}>
            <div className="flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-(--badge-bg) text-foreground">
                <Search className="h-5 w-5" />
              </div>
              <div>
                <h3 className="text-lg font-semibold text-foreground">Embedding model</h3>
                <p className="text-sm text-(--muted)">Choose the retrieval backbone used to represent documents and queries.</p>
              </div>
            </div>
            <div className="mt-5 grid gap-4 lg:grid-cols-2">
              {EMBEDDING_OPTIONS.map((option) => (
                <SelectCard key={option.id} active={embeddingModel === option.id} title={option.label} caption={option.caption} onClick={() => setEmbeddingModel(option.id)} />
              ))}
            </div>
          </div>

          <div className="rounded-[28px] p-6" style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}>
            <div className="flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-(--badge-bg) text-foreground">
                <Sparkles className="h-5 w-5" />
              </div>
              <div>
                <h3 className="text-lg font-semibold text-foreground">Reranking</h3>
                <p className="text-sm text-(--muted)">Improve result quality after the first retrieval pass.</p>
              </div>
            </div>
            <div className="mt-5 grid gap-4 lg:grid-cols-2">
              {RERANKER_OPTIONS.map((option) => (
                <SelectCard key={option.id} active={rerankerModel === option.id} title={option.label} caption={option.caption} onClick={() => setRerankerModel(option.id)} />
              ))}
            </div>
          </div>
        </div>

        <div className="rounded-[28px] p-6" style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}>
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-(--badge-bg) text-foreground">
              <SlidersHorizontal className="h-5 w-5" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-foreground">Post-processing</h3>
              <p className="text-sm text-(--muted)">A compact settings card modeled after the reference search workspace.</p>
            </div>
          </div>

          <div className="mt-6 space-y-5">
            <div>
              <div className="text-xs font-medium text-(--muted)">Results to rerank</div>
              <input
                type="range"
                min={5}
                max={50}
                step={5}
                value={rerankLimit}
                onChange={(event) => setRerankLimit(Number.parseInt(event.target.value, 10))}
                className="mt-3 w-full"
              />
              <div className="mt-2 text-sm font-semibold text-foreground">{rerankLimit}</div>
            </div>

            <label className="flex items-start justify-between gap-4 rounded-2xl border border-(--border) px-4 py-4">
              <div>
                <div className="text-sm font-semibold text-foreground">Contextual RAG</div>
                <div className="mt-1 text-sm leading-6 text-(--muted)">Improve prompts with more surrounding context when available.</div>
              </div>
              <input type="checkbox" checked={contextualRag} onChange={(event) => setContextualRag(event.target.checked)} className="mt-1 h-4 w-4" />
            </label>

            <label className="flex items-start justify-between gap-4 rounded-2xl border border-(--border) px-4 py-4">
              <div>
                <div className="text-sm font-semibold text-foreground">Multipass indexing</div>
                <div className="mt-1 text-sm leading-6 text-(--muted)">Keep a more exhaustive indexing pass ready for future document-heavy flows.</div>
              </div>
              <input type="checkbox" checked={multipassIndexing} onChange={(event) => setMultipassIndexing(event.target.checked)} className="mt-1 h-4 w-4" />
            </label>
          </div>
        </div>
      </div>
    </div>
  );
}