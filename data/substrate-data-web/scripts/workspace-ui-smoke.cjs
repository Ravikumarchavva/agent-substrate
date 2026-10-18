// Isolated UI interaction checks. API responses are fixtures; storage/security are tested in pytest.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const { mkdirSync } = require("node:fs");
(async () => {
  const browser = await chromium.launch({
    ...(process.env.CHROMIUM_PATH
      ? { executablePath: process.env.CHROMIUM_PATH }
      : {}),
    headless: true,
    args: ["--no-sandbox"],
  });
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    permissions: ["clipboard-read", "clipboard-write"],
  });
  const origin = process.env.UI_REVIEW_URL || "http://127.0.0.1:3023";
  await context.addCookies([
    { name: "sd_session", value: "ui-review-fixture", url: origin },
  ]);
  await context.addInitScript(() =>
    localStorage.setItem("substrate-data-theme", "dark"),
  );
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const errors = [];
  const statements = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const stamp = "2026-10-17T10:00:00Z";
  const base = {
    owner: "user:alice",
    comment: "Shared analytics data",
    created_at: stamp,
    updated_at: stamp,
  };
  const tables = [
    {
      ...base,
      name: "orders",
      full_name: "main.default.orders",
      catalog_name: "main",
      schema_name: "default",
      columns: [
        { name: "customer", type: "VARCHAR" },
        { name: "revenue", type: "DOUBLE" },
        { name: "region", type: "VARCHAR" },
      ],
      row_count: 300,
      size_bytes: 24576,
      file_count: 3,
      format: "ICEBERG",
      partitioning: [],
      snapshot_id: "123",
      parent_table: null,
    },
  ];
  const rows = Array.from({ length: 100 }, (_, i) => [
    `Customer ${String(i + 1).padStart(3, "0")}`,
    (i + 1) * 1234.5,
    ["North", "South", "West"][i % 3],
  ]);
  const volumes = [
    {
      ...base,
      id: "volume-1",
      name: "documents",
      full_name: "main.default.documents",
      kind: "volume",
      properties: { bucket: "substrate-staging", prefix: "volumes/test/" },
    },
  ];
  const models = [
    {
      ...base,
      id: "model-1",
      name: "embedder",
      full_name: "main.default.embedder",
      kind: "model",
      properties: {
        artifact_uri: "s3://models/embedder/v1/",
        framework: "transformers",
        model_version: 1,
      },
    },
  ];
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    if (path === "/statements" && route.request().method() === "POST")
      statements.push(route.request().postDataJSON());
    let data;
    if (path === "/me")
      data = {
        principal: "user:alice",
        name: "Alice",
        email: "alice@example.test",
        groups: ["data-admins"],
        is_admin: true,
      };
    else if (path === "/overview")
      data = { tables: 1, rows: 300, size_bytes: 24576, recent_statements: [] };
    else if (path === "/catalogs")
      data = { catalogs: [{ ...base, name: "main", full_name: "main" }] };
    else if (path === "/catalogs/main/schemas")
      data = {
        schemas: [{ ...base, name: "default", full_name: "main.default" }],
      };
    else if (
      path.endsWith("/preview") ||
      (path === "/statements" && route.request().method() === "POST")
    )
      data = {
        statement_id: crypto.randomUUID(),
        columns: tables[0].columns,
        rows,
        row_count: 100,
        total: 300,
        truncated: true,
        duration_ms: 12,
        files_scanned: 3,
      };
    else if (path.endsWith("/tables/orders")) data = tables[0];
    else if (path.endsWith("/tables")) data = { tables };
    else if (path.endsWith("/views")) data = { views: [] };
    else if (path.endsWith("/volumes")) data = { volumes };
    else if (path.endsWith("/models")) data = { models };
    else if (path.endsWith("/files"))
      data = {
        directories: [{ path: "reports", directory: true }],
        files: [
          {
            path: "readme.txt",
            name: "readme.txt",
            size_bytes: 128,
            modified_at: stamp,
            directory: false,
          },
        ],
      };
    else if (path === "/statements") data = { statements: [] };
    else if (path === "/compute/resources") data = { resources: [] };
    else if (path.startsWith("/grants/")) data = { privilege_assignments: [] };
    else if (path.endsWith("/policies"))
      data = { row_filters: [], column_masks: [] };
    else data = {};
    await route.fulfill({ json: data });
  });
  mkdirSync("/tmp/substrate-ui-review", { recursive: true });
  try {
    await page.goto(origin);
    await page.getByRole("button", { name: "Catalog", exact: true }).click();
    await page.getByRole("navigation", { name: "Schema objects" }).waitFor();
    await page
      .getByRole("button", { name: /Volumes/ })
      .filter({ has: page.locator("span") })
      .last()
      .click();
    await page.getByRole("heading", { name: "Volumes", exact: true }).waitFor();
    await page.screenshot({
      path: "/tmp/substrate-ui-review/catalog-dark.png",
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "documents", exact: true })
      .last()
      .click();
    await page.getByRole("heading", { name: "Files", exact: true }).waitFor();
    assert(await page.getByText("readme.txt", { exact: true }).count());
    await page.locator("summary").filter({ hasText: "Models" }).click();
    await page
      .getByRole("button", { name: "embedder", exact: true })
      .first()
      .click();
    await page.getByText("s3://models/embedder/v1/", { exact: true }).waitFor();
    await page
      .getByRole("button", { name: "orders", exact: true })
      .first()
      .click();
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await page.getByRole("textbox", { name: "Search loaded rows" }).waitFor();
    await page
      .getByRole("textbox", { name: "Search loaded rows" })
      .fill("Customer 009");
    await page.getByText("1 of 100 loaded rows", { exact: true }).waitFor();
    await page.getByRole("textbox", { name: "Search loaded rows" }).clear();
    await page.locator(".grid-column-picker summary").click();
    await page
      .locator(".grid-column-picker")
      .getByLabel("region", { exact: true })
      .uncheck();
    assert.equal(
      await page
        .locator(".databricks-table th")
        .filter({ hasText: "region" })
        .count(),
      0,
    );
    await page.locator(".grid-column-picker summary").click();
    const header = page
      .locator(".databricks-table th")
      .filter({ hasText: "revenue" });
    await header.locator(".menu-btn").click();
    await page.getByRole("button", { name: "Pin column", exact: true }).click();
    assert.equal(
      await header.evaluate((node) => getComputedStyle(node).position),
      "sticky",
    );
    await page.locator(".grid-row-index-wrap").first().click();
    await page
      .getByText("100 of 100 loaded rows · 1 selected", { exact: true })
      .waitFor();
    const downloadPromise = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Download selected rows" })
      .last()
      .click();
    const download = await downloadPromise;
    assert(download.suggestedFilename().endsWith(".csv"));
    await page.screenshot({
      path: "/tmp/substrate-ui-review/table-dark.png",
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "Collapse sidebar", exact: true })
      .click();
    await page.waitForFunction(
      () =>
        Math.round(
          document
            .querySelector(".workspace > .sidebar")
            .getBoundingClientRect().width,
        ) === 48,
    );
    assert.equal(
      Math.round(
        await page
          .locator(".workspace > .sidebar")
          .evaluate((n) => n.getBoundingClientRect().width),
      ),
      48,
    );
    await page.getByRole("button", { name: "SQL Editor", exact: true }).click();
    await page
      .getByRole("button", { name: "Run all", exact: false })
      .first()
      .click();
    await page.getByRole("textbox", { name: "Search loaded rows" }).waitFor();
    await page.screenshot({
      path: "/tmp/substrate-ui-review/sql-dark.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 1900, height: 1000 });
    await page
      .getByRole("button", { name: "Expand sidebar", exact: true })
      .click();
    await page.waitForFunction(
      () =>
        Math.round(
          document
            .querySelector(".workspace > .sidebar")
            .getBoundingClientRect().width,
        ) === 196,
    );
    const dimensions = await page.evaluate(() => {
      const height = (selector) =>
        Math.round(
          document.querySelector(selector).getBoundingClientRect().height,
        );
      return {
        topbar: height(".topbar"),
        run: height(".sql-run-button"),
        row: height(".databricks-row"),
        toolbar: height(".sql-editor-toolbar"),
        font: getComputedStyle(document.body).fontSize,
      };
    });
    assert.deepEqual(dimensions, {
      topbar: 40,
      run: 28,
      row: 28,
      toolbar: 40,
      font: "12px",
    });
    await page.screenshot({
      path: "/tmp/substrate-ui-review/sql-expanded-dark.png",
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "Collapse sidebar", exact: true })
      .click();
    await page.setViewportSize({ width: 1600, height: 1000 });
    assert.equal(statements.at(-1).catalog, "main");
    assert.equal(statements.at(-1).schema, "default");
    const customerHeader = page
      .locator(".databricks-table th")
      .filter({ hasText: "customer" });
    await customerHeader.locator(".sort-btn").click();
    await customerHeader.locator(".sort-btn").click();
    await page.waitForFunction(() =>
      document
        .querySelector(".databricks-row")
        ?.textContent.includes("Customer 100"),
    );
    await page.locator(".grid-row-index-wrap").first().click();
    await page
      .locator(".grid-row-index-wrap")
      .nth(2)
      .click({ modifiers: ["Shift"] });
    await page
      .getByText("100 of 100 loaded rows · 3 selected", { exact: true })
      .waitFor();
    await page.keyboard.press("Control+c");
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    for (const customer of ["Customer 098", "Customer 099", "Customer 100"])
      assert(copied.includes(customer));
    await page.getByRole("button", { name: "User account menu" }).click();
    await page.getByRole("button", { name: "Use light theme" }).click();
    await page.getByRole("button", { name: "User account menu" }).click();
    await page.waitForFunction(() =>
      document.querySelector(".monaco-editor")?.classList.contains("vs"),
    );
    await page.screenshot({
      path: "/tmp/substrate-ui-review/sql-light.png",
      fullPage: true,
    });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
      "page should not overflow horizontally",
    );
    await page
      .getByRole("textbox", { name: "Search catalog", exact: true })
      .fill("embedder");
    await page
      .getByRole("region", { name: "Catalog search results" })
      .waitFor();
    await page
      .getByRole("button", { name: "main.default.embedder", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Permissions", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Access grants", exact: true })
      .waitFor();
    await page.setViewportSize({ width: 760, height: 900 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
      "catalog must fit a smaller viewport",
    );
    await page.screenshot({
      path: "/tmp/substrate-ui-review/catalog-small.png",
      fullPage: true,
    });
    // A web update must not blank the catalog when the API has not been restarted yet.
    await page.route(
      /\/api\/v1\/catalogs\/[^/]+\/schemas\/[^/]+\/(volumes|models)(\?|$)/,
      (route) => route.fulfill({ status: 404, json: { detail: "Not Found" } }),
    );
    await page.setViewportSize({ width: 1600, height: 1000 });
    await page.reload();
    await page.getByRole("button", { name: "Catalog", exact: true }).click();
    await page
      .getByText("Restart the data API to enable Volumes and Models.", {
        exact: true,
      })
      .waitFor();
    await page
      .getByRole("button", { name: "orders", exact: true })
      .first()
      .waitFor();
    assert.equal(await page.locator(".alert.error").count(), 0);
    assert.deepEqual(errors, []);
    console.log(
      "UI checks passed: catalog assets, file browser, table search, visibility, pinning, selection, CSV export, navigation collapse, SQL results, light/dark. Screenshots: /tmp/substrate-ui-review",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
