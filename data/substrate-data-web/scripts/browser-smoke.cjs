/* Real-browser check. Run against `make up && make dev && make dev-compute && make demo`.
 * Signs in through Keycloak, then browses the catalog, previews the NYC taxi table, profiles it
 * and runs SQL. Credentials come from ../.env (private, never printed). */
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const env = Object.fromEntries(
  fs
    .readFileSync(path.join(__dirname, "../../substrate-data/.env"), "utf8")
    .split("\n")
    .filter((line) => line.includes("=") && !line.startsWith("#"))
    .map((line) => [
      line.slice(0, line.indexOf("=")),
      line.slice(line.indexOf("=") + 1),
    ]),
);
const base = process.env.DATA_UI_URL || "http://127.0.0.1:3011";
const shots = path.join(__dirname, "../../substrate-data/docs/screenshots");

(async () => {
  const browser = await chromium.launch({
    ...(process.env.CHROMIUM_PATH
      ? { executablePath: process.env.CHROMIUM_PATH }
      : {}),
    headless: true,
    args: ["--no-sandbox"],
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(base, { waitUntil: "networkidle" });
    // Unauthenticated users land on the identity provider's form.
    assert.match(page.url(), /18080\/realms\/substrate/);
    await page.locator("#username").fill("alice");
    await page.locator("#password").fill(env.DEV_ALICE_PASSWORD);
    await page.locator("#kc-login").click();
    await page.waitForURL(`${base}/**`);
    await page.waitForLoadState("networkidle");
    // The session is an httpOnly cookie: no token is reachable from page scripts.
    assert.equal(
      await page.evaluate(() => document.cookie.includes("sd_session")),
      false,
    );
    assert.equal(
      await page.evaluate(() => JSON.stringify(localStorage).includes("eyJ")),
      false,
    );

    await page
      .getByRole("button", { name: /Catalog/ })
      .first()
      .click();
    await page
      .getByText("yellow", { exact: false })
      .first()
      .waitFor({ timeout: 20000 });
    await page.getByText("yellow", { exact: false }).first().click();
    await page.getByText("66 data files").first().waitFor({ timeout: 20000 });
    await page.getByText("month(tpep_pickup_datetime)").first().waitFor();
    // Preview rows come from attached compute through a signed ticket.
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await page
      .locator("table, [role=grid]")
      .first()
      .waitFor({ timeout: 30000 });
    await page
      .getByText(/Rows 1–100 of 88,039,239/)
      .first()
      .waitFor({ timeout: 30000 });
    fs.mkdirSync(shots, { recursive: true });
    await page.screenshot({ path: path.join(shots, "catalog.png") });

    await page
      .getByRole("button", { name: /SQL Editor/ })
      .first()
      .click();
    const editor = page.locator(".monaco-editor").first();
    await editor.waitFor({ timeout: 20000 });
    await editor.click();
    await page.keyboard.press("Control+A");
    await page.keyboard.insertText(
      "SELECT PULocationID, count(*) AS trips FROM main.nyc.yellow " +
        "WHERE tpep_pickup_datetime >= '2026-08-01' GROUP BY 1 ORDER BY trips DESC LIMIT 5",
    );
    await page
      .getByRole("button", { name: /Run all/ })
      .first()
      .click();
    // The aggregate runs on attached compute and streams back; the grid shows its columns.
    await page
      .getByText("trips", { exact: true })
      .first()
      .waitFor({ timeout: 60000 });
    await page.screenshot({ path: path.join(shots, "sql.png") });

    // A view: listed in the catalog, with its definition and a live preview through the same path.
    await page
      .getByRole("button", { name: /Catalog/ })
      .first()
      .click();
    await page.getByText("monthly_revenue").first().waitFor({ timeout: 20000 });
    await page.getByText("monthly_revenue").first().click();
    await page
      .getByText("VIEW", { exact: true })
      .first()
      .waitFor({ timeout: 20000 });
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await page
      .getByText(/12 rows/)
      .first()
      .waitFor({ timeout: 60000 });
    await page.screenshot({ path: path.join(shots, "view.png") });

    // Maintenance: a materialized view shows its freshness and refreshes on demand; the same tab
    // holds file optimization and the access-policy editor.
    const api = (method, url, body) =>
      page.evaluate(
        async ([method, url, body]) => {
          const response = await fetch(`/api/v1${url}`, {
            method,
            headers: body ? { "content-type": "application/json" } : {},
            body: body ? JSON.stringify(body) : undefined,
          });
          return { status: response.status, body: await response.text() };
        },
        [method, url, body],
      );
    await api("DELETE", "/catalogs/main/schemas/nyc/tables/smoke_mv");
    const created = await api(
      "POST",
      "/catalogs/main/schemas/nyc/materialized-views",
      {
        name: "smoke_mv",
        sql: "SELECT PULocationID AS zone, count(*) AS trips FROM main.nyc.yellow WHERE tpep_pickup_datetime >= '2026-08-01' GROUP BY 1",
      },
    );
    assert.equal(created.status, 202, created.body);
    await page.reload({ waitUntil: "networkidle" });
    await page
      .getByRole("button", { name: /Catalog/ })
      .first()
      .click();
    await page.getByText("smoke_mv").first().click();
    await page
      .getByRole("button", { name: "Maintenance", exact: true })
      .click();
    const maintenance = page.getByTestId("maintenance");
    await maintenance
      .getByRole("heading", { name: "Materialized view" })
      .waitFor({ timeout: 20000 });
    await maintenance
      .getByText(/^fresh/)
      .first()
      .waitFor({ timeout: 60000 });
    await maintenance.getByRole("button", { name: "Refresh now" }).click();
    await maintenance
      .getByRole("status")
      .getByText("Refresh started")
      .waitFor({ timeout: 20000 });
    await maintenance
      .getByRole("heading", { name: "Optimize files" })
      .waitFor();
    await page.getByTestId("policy").getByLabel("Row filter").fill("zone > 0");
    await page
      .getByTestId("policy")
      .getByRole("button", { name: "Save policy" })
      .click();
    await maintenance
      .getByRole("status")
      .getByText("Policy saved")
      .waitFor({ timeout: 20000 });
    await maintenance
      .getByText("refresh", { exact: false })
      .first()
      .waitFor({ timeout: 20000 });
    await page.screenshot({ path: path.join(shots, "maintenance.png") });
    await api("DELETE", "/catalogs/main/schemas/nyc/tables/smoke_mv");

    await page
      .getByRole("button", { name: /Compute/ })
      .first()
      .click();
    // Attached endpoints appear with their address and a "ready" health state.
    await page
      .locator("td code", { hasText: "http://127.0.0.1:8011" })
      .first()
      .waitFor({ timeout: 20000 });
    await page
      .locator('[title="Attached · ready"]')
      .first()
      .waitFor({ timeout: 20000 });
    await page.screenshot({ path: path.join(shots, "compute.png") });

    assert.deepEqual(errors, []);
    console.log(
      "PASS: sign-in, catalog, 88M-row preview, SQL, maintenance, attached compute",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
