import { describe, expect, it } from "vitest";
import { linkifyCitations } from "./MessageBubble";

describe("linkifyCitations", () => {
  it("rewrites a valid citation marker into a citation: link", () => {
    expect(linkifyCitations("Signed by Dr. Shanthi [1].", new Set([1]))).toBe(
      "Signed by Dr. Shanthi [1](citation:1)."
    );
  });

  it("leaves an out-of-range index as literal text — the grounding guarantee", () => {
    expect(linkifyCitations("A wild guess [5].", new Set([1]))).toBe(
      "A wild guess [5]."
    );
  });

  it("leaves everything unchanged when there are no valid indices", () => {
    expect(linkifyCitations("Nothing here [1].", new Set())).toBe(
      "Nothing here [1]."
    );
  });

  it("does not touch a real markdown link", () => {
    expect(linkifyCitations("See [1](https://example.com).", new Set([1]))).toBe(
      "See [1](https://example.com)."
    );
  });

  it("does not touch image syntax", () => {
    expect(linkifyCitations("![1](chart.png)", new Set([1]))).toBe(
      "![1](chart.png)"
    );
  });

  it("does not rewrite inside inline code", () => {
    const input = "Access via `arr[1]` in code.";
    expect(linkifyCitations(input, new Set([1]))).toBe(input);
  });

  it("does not rewrite inside a fenced code block", () => {
    const input = "```js\nconst x = arr[1];\n```";
    expect(linkifyCitations(input, new Set([1]))).toBe(input);
  });

  it("expands a comma-separated citation into two links", () => {
    expect(linkifyCitations("Both agree [1, 2].", new Set([1, 2]))).toBe(
      "Both agree [1](citation:1)[2](citation:2)."
    );
  });

  it("only rewrites indices that are all valid in a comma-separated group", () => {
    // [1, 5] — 5 isn't a real source, so neither number is rewritten
    // (rewriting just [1] would silently split apart what the model wrote
    // as one combined citation).
    expect(linkifyCitations("Mixed [1, 5].", new Set([1]))).toBe("Mixed [1, 5].");
  });

  it("handles two independent citations back to back", () => {
    expect(linkifyCitations("[1][2]", new Set([1, 2]))).toBe(
      "[1](citation:1)[2](citation:2)"
    );
  });

  it("returns empty/falsy content unchanged", () => {
    expect(linkifyCitations("", new Set([1]))).toBe("");
  });
});
