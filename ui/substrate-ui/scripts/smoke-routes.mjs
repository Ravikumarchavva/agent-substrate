/**
 * Route smoke test — verifies all client-side routes resolve (not 404).
 *
 * Starts the production Next.js server, fetches every route in the list,
 * and asserts each returns a non-404 status code.
 *
 * Usage:  node scripts/smoke-routes.mjs          (after `pnpm build`)
 *         SMOKE_PORT=3099 node scripts/smoke-routes.mjs
 */

const START_PORT = Number(process.env.SMOKE_PORT || 3099);

/** Routes that must resolve (not 404). Add new client routes here.
 *  All app routes are served under the `/chat` basePath (next.config.ts) —
 *  a bare "/" or "/settings" 404s regardless of the page existing. */
const ROUTES = [
  "/chat",
  "/chat/test-thread-id",
  "/chat/settings",
  "/chat/settings/apps",
  "/chat/settings/llm",
  "/chat/settings/search",
  "/chat/settings/admin",
];

async function main() {
  const { spawn } = await import("node:child_process");
  const { createServer } = await import("node:net");
  const { fileURLToPath } = await import("node:url");

  async function findAvailablePort(startPort) {
    let port = startPort;

    while (true) {
      const available = await new Promise((resolve) => {
        const probe = createServer();
        probe.once("error", () => resolve(false));
        probe.once("listening", () => {
          probe.close(() => resolve(true));
        });
        probe.listen(port);
      });

      if (available) {
        return port;
      }

      port += 1;
    }
  }

  function cleanupServer(server) {
    if (!server || server.killed) {
      return Promise.resolve();
    }

    return new Promise((resolve) => {
      server.on("exit", resolve);
      server.kill();
    });
  }

  const port = await findAvailablePort(START_PORT);
  const base = `http://localhost:${port}`;
  const nextCliPath = fileURLToPath(
    new URL("../node_modules/next/dist/bin/next", import.meta.url),
  );

  // Start production server
  const server = spawn(process.execPath, [nextCliPath, "start", "-p", String(port)], {
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, NODE_ENV: "production" },
  });

  // Wait for server to be ready
  const ready = await new Promise((resolve) => {
    let output = "";
    const timeout = setTimeout(() => resolve(false), 30_000);

    function resolveReady(value) {
      clearTimeout(timeout);
      resolve(value);
    }

    server.stdout.on("data", (chunk) => {
      output += chunk.toString();
      if (output.includes("Ready in")) {
        resolveReady(true);
      }
    });

    server.stderr.on("data", (chunk) => {
      output += chunk.toString();
      if (output.includes("Ready in")) {
        resolveReady(true);
      }
    });

    server.on("error", () => {
      resolveReady(false);
    });

    server.on("exit", () => {
      if (!output.includes("Ready in")) {
        console.error(output.trim() || "Server exited before reporting readiness.");
        resolveReady(false);
      }
    });
  });

  if (!ready) {
    console.error("Server failed to start within 30s");
    await cleanupServer(server);
    process.exit(1);
  }

  console.log(`Server ready on port ${port}`);

  let failures = 0;

  try {
    for (const route of ROUTES) {
      try {
        const res = await fetch(`${base}${route}`, { redirect: "manual" });
        const status = res.status;
        const ok = status !== 404;
        const icon = ok ? "✓" : "✗";
        console.log(`  ${icon} ${route} → ${status}`);
        if (!ok) failures++;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        console.log(`  ✗ ${route} → FETCH ERROR: ${message}`);
        failures++;
      }
    }
  } finally {
    await cleanupServer(server);
  }

  if (failures > 0) {
    console.error(`\n${failures} route(s) returned 404. Fix missing pages.`);
    process.exit(1);
  }

  console.log(`\nAll ${ROUTES.length} routes OK.`);
  process.exit(0);
}

main();
