import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, isComputeUnavailable, post } from "./api";
import { cell } from "./grid";
afterEach(() => vi.unstubAllGlobals());
describe("workspace API client", () => {
  it("sends structured request bodies to the versioned API", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    vi.stubGlobal("fetch", fetch);
    await api("/statements", post({ sql: "SELECT 1" }));
    expect(fetch.mock.calls[0][0]).toBe("/api/v1/statements");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      sql: "SELECT 1",
    });
  });
  it("surfaces RFC 9457 problems with their stable code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: async () => ({
          type: "https://substrate.dev/problems/compute-unavailable",
          detail: "No SQL compute is attached and ready",
        }),
      }),
    );
    const error = (await api("/statements").catch(
      (e: unknown) => e,
    )) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.message).toBe("No SQL compute is attached and ready");
    expect(isComputeUnavailable(error)).toBe(true);
  });
  it("preserves null, nested data, and zero values in grid cells", () => {
    expect(cell(null)).toBe("NULL");
    expect(cell(0)).toBe("0");
    expect(cell({ x: 1 })).toBe('{"x":1}');
  });
});
