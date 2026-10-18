"use client";
import { ChevronRight, FileText, Layers3, Plus, Search } from "lucide-react";
import type { WorkspaceModel } from "../../hooks/use-workspace";
import { type Chunk, type Collection } from "../../lib/types";
import { api, post } from "../api";
import { Empty } from "../empty";
import { Button } from "../ui/button";
export function RetrievalScreen({ model }: { model: WorkspaceModel }) {
  const {
    client,
    busy,
    setNotice,
    collectionId,
    setCollectionId,
    collectionName,
    setCollectionName,
    title,
    setTitle,
    text,
    setText,
    term,
    setTerm,
    matches,
    setMatches,
    collections,
    capabilities,
    chunks,
    act,
  } = model;
  return (
    <>
      <div className="rag-layout">
        <section className="panel">
          <div className="panel-heading">
            <h2>Collections</h2>
            <Layers3 size={16} />
          </div>
          <form
            className="inline-form"
            onSubmit={(e) => {
              e.preventDefault();
              void act("collection", async () => {
                const c = await api<Collection>(
                  "/collections",
                  post({ name: collectionName }),
                );
                setCollectionId(c.id);
                setCollectionName("");
                setMatches(null);
                await client.invalidateQueries({
                  queryKey: ["collections"],
                });
              });
            }}
          >
            <input
              aria-label="Collection name"
              required
              value={collectionName}
              onChange={(e) => setCollectionName(e.target.value)}
              placeholder="New collection name"
            />
            <Button disabled={!!busy} aria-label="Create collection">
              <Plus size={15} />
            </Button>
          </form>
          {collections.data?.map((c) => (
            <button
              className={`collection-row ${collectionId === c.id ? "chosen" : ""}`}
              key={c.id}
              onClick={() => {
                setCollectionId(c.id);
                setMatches(null);
              }}
            >
              <FileText size={16} />
              {c.name}
              <ChevronRight size={14} />
            </button>
          ))}
        </section>
        <section className="panel">
          <div className="panel-heading">
            <h2>
              {collections.data?.find((c) => c.id === collectionId)?.name ||
                "Select a collection"}
            </h2>
            <span className="badge">Keyword search</span>
          </div>
          {collectionId ? (
            <div className="rag-content">
              <details className="document-ingest">
                <summary>Add document</summary>
                <form
                  className="document-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void act("document", async () => {
                      await api<{ document_id: string; status: string }>(
                        `/collections/${collectionId}/documents`,
                        post({ title, text }),
                      );
                      setTitle("");
                      setText("");
                      await client.invalidateQueries({
                        queryKey: ["chunks"],
                      });
                      setNotice(
                        "Document queued: attached compute is chunking it.",
                      );
                    });
                  }}
                >
                  <label>
                    Document title
                    <input
                      required
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      placeholder="e.g. Storage architecture"
                    />
                  </label>
                  <label>
                    Document text
                    <textarea
                      required
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                      placeholder="Paste text to prepare retrieval context…"
                    />
                  </label>
                  <Button disabled={!!busy}>
                    <Plus size={14} />
                    Add document & chunk
                  </Button>
                </form>
              </details>
              <form
                className="inline-form search-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  void act("search", async () =>
                    setMatches(
                      (
                        await api<{ results: Chunk[] }>(
                          `/collections/${collectionId}/search?q=${encodeURIComponent(term)}`,
                        )
                      ).results,
                    ),
                  );
                }}
              >
                <input
                  aria-label="Search document chunks"
                  value={term}
                  required
                  onChange={(e) => setTerm(e.target.value)}
                  placeholder="Search document content…"
                />
                <Button disabled={!!busy}>
                  <Search size={14} />
                  Search
                </Button>
              </form>
              <h3>
                {matches
                  ? `${matches.length} ${matches.length === 1 ? "result" : "results"}`
                  : "Prepared chunks (up to 100)"}
              </h3>
              {(matches ?? chunks.data ?? []).map((c, i) => (
                <article className="chunk" key={`${c.document_id}-${i}`}>
                  <strong>{c.title}</strong>
                  <p>{c.text}</p>
                </article>
              ))}
              {matches?.length === 0 && (
                <Empty text="No matching chunks found." />
              )}
            </div>
          ) : (
            <Empty text="Choose or create a collection to add documents and search real chunks." />
          )}
        </section>
      </div>
      <details className="optional-integrations">
        <summary>Optional integrations</summary>
        <div className="capabilities">
          {Object.entries(capabilities.data || {})
            .filter(([, value]) => value === false)
            .map(([key]) => (
              <div key={key}>
                <span>{key.replaceAll("_", " ")}</span>
                <span className="badge">Not configured</span>
              </div>
            ))}
        </div>
        <p>{capabilities.data?.note}</p>
      </details>
    </>
  );
}
