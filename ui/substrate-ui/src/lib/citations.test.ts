import { describe, expect, it } from "vitest";
import { mergeSources, parseCitations } from "./citations";

describe("parseCitations", () => {
  it("parses the exact backend wire shape", () => {
    const sources = parseCitations({
      citations: [
        {
          index: 1,
          file_name: "Naac_appLetter.pdf",
          file_id: "db-file-id",
          session_path: "Naac_appLetter.pdf",
          thread_id: "thread-1",
          page: 5,
          pages: [5],
          score: 0.9123,
          snippet: "the certificate was signed by Dr. Shanthi",
          backend: "pinecone",
        },
      ],
    });

    expect(sources).toEqual([
      {
        index: 1,
        fileName: "Naac_appLetter.pdf",
        fileId: "db-file-id",
        sessionPath: "Naac_appLetter.pdf",
        threadId: "thread-1",
        page: 5,
        pages: [5],
        score: 0.9123,
        snippet: "the certificate was signed by Dr. Shanthi",
        backend: "pinecone",
      },
    ]);
  });

  it("returns [] for missing/malformed structured_content", () => {
    expect(parseCitations(undefined)).toEqual([]);
    expect(parseCitations(null)).toEqual([]);
    expect(parseCitations("not an object")).toEqual([]);
    expect(parseCitations({})).toEqual([]);
    expect(parseCitations({ citations: "not an array" })).toEqual([]);
    expect(parseCitations({ task_list: {} })).toEqual([]); // a manage_tasks payload
  });

  it("drops entries missing index or file_name — never fabricates a source", () => {
    const sources = parseCitations({
      citations: [
        { file_name: "no-index.pdf", page: 1 },
        { index: 2, file_name: "" },
        { index: 3, file_name: "ok.pdf" },
      ],
    });

    expect(sources).toEqual([
      expect.objectContaining({ index: 3, fileName: "ok.pdf" }),
    ]);
  });

  it("normalizes page to null when absent, not undefined", () => {
    const sources = parseCitations({
      citations: [{ index: 1, file_name: "report.docx" }],
    });

    expect(sources[0].page).toBeNull();
  });

  it("ignores non-array entries within citations", () => {
    const sources = parseCitations({
      citations: [null, "bad", 42, { index: 1, file_name: "ok.pdf" }],
    });

    expect(sources).toHaveLength(1);
  });
});

describe("mergeSources", () => {
  const a = { index: 1, fileName: "a.pdf" };
  const b = { index: 2, fileName: "b.pdf" };

  it("appends new indices to an empty set", () => {
    expect(mergeSources([], [a, b])).toEqual([a, b]);
  });

  it("returns prev unchanged when next is empty", () => {
    const prev = [a];
    expect(mergeSources(prev, [])).toBe(prev);
  });

  it("last write wins for a repeated index", () => {
    const updated = { index: 1, fileName: "a.pdf", page: 9 };
    expect(mergeSources([a], [updated])).toEqual([updated]);
  });

  it("sorts the merged result by index", () => {
    expect(mergeSources([b], [a])).toEqual([a, b]);
  });

  it("accumulates across repeated merges — the cumulative-emission case", () => {
    let sources = mergeSources([], [a]);
    sources = mergeSources(sources, [a, b]); // second tool call re-emits the full ledger
    expect(sources).toEqual([a, b]);
  });
});
