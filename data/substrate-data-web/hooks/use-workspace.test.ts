import { afterEach, describe, expect, it, vi } from "vitest";
import { loadCatalog } from "./use-workspace";

afterEach(() => vi.unstubAllGlobals());
function catalogApi(assetStatus: number) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const path = url.split("?")[0];
      const asset = path.endsWith("/volumes") || path.endsWith("/models");
      const status = asset ? assetStatus : 200;
      const body =
        path === "/api/v1/catalogs"
          ? { catalogs: [{ name: "main", owner: "user:alice" }] }
          : path.endsWith("/schemas")
            ? { schemas: [{ name: "default", owner: "user:alice" }] }
            : path.endsWith("/tables")
              ? {
                  tables: [
                    {
                      name: "orders",
                      full_name: "main.default.orders",
                      columns: [],
                    },
                  ],
                }
              : path.endsWith("/views")
                ? { views: [] }
                : status !== 200
                  ? { detail: status === 404 ? "Not Found" : "Access denied" }
                  : path.endsWith("/volumes")
                    ? { volumes: [{ id: "v1", name: "documents" }] }
                    : { models: [] };
      return { status, ok: status === 200, json: async () => body };
    }),
  );
}
describe("catalog API compatibility", () => {
  it("keeps the table catalog available when an older API lacks asset endpoints", async () => {
    catalogApi(404);
    const tree = await loadCatalog();
    expect(tree.assetsAvailable).toBe(false);
    expect(tree.datasets[0].display_name).toBe("orders");
    expect(tree.catalogs[0].schemas[0].volumes).toEqual([]);
  });
  it("loads registered assets from an updated API", async () => {
    catalogApi(200);
    const tree = await loadCatalog();
    expect(tree.assetsAvailable).toBe(true);
    expect(tree.catalogs[0].schemas[0].volumes[0].name).toBe("documents");
  });
  it("surfaces authorization failures instead of silently treating assets as empty", async () => {
    catalogApi(403);
    await expect(loadCatalog()).rejects.toMatchObject({ status: 403 });
  });
});
