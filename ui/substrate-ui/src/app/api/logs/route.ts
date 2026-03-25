import { NextRequest, NextResponse } from "next/server";

export async function POST(request: NextRequest) {
  try {
    const body = (await request.json()) as { entries?: unknown[] };

    if (!body.entries || !Array.isArray(body.entries)) {
      return NextResponse.json(
        { error: "Missing entries array" },
        { status: 400 },
      );
    }

    const entries = body.entries.slice(0, 100);

    for (const raw of entries) {
      if (!raw || typeof raw !== "object") continue;
      const entry = raw as Record<string, unknown>;

      const logLine = {
        source: "frontend",
        timestamp:
          typeof entry.timestamp === "string"
            ? entry.timestamp
            : new Date().toISOString(),
        level: typeof entry.level === "string" ? entry.level : "info",
        message:
          typeof entry.message === "string"
            ? entry.message.slice(0, 2000)
            : "unknown",
        component:
          typeof entry.component === "string" ? entry.component : undefined,
        action:
          typeof entry.action === "string" ? entry.action : undefined,
        url: typeof entry.url === "string" ? entry.url : undefined,
        hasMeta: entry.metadata !== undefined,
      };

      console.log(JSON.stringify(logLine));
    }

    return NextResponse.json({ received: entries.length });
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }
}
