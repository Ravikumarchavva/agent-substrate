import { describe, expect, it } from "vitest";
import { formatWait, getErrorMessage } from "./_client";

describe("formatWait", () => {
  it("speaks in hours and minutes", () => {
    expect(formatWait(30)).toBe("less than a minute");
    expect(formatWait(90)).toBe("2 minutes");
    expect(formatWait(3600)).toBe("1h");
    expect(formatWait(31200)).toBe("8h 40m");
  });
});

describe("getErrorMessage", () => {
  it("tells a person when a usage limit resets", async () => {
    const res = new Response(JSON.stringify({ detail: "Daily limit reached. Resets in 31200 seconds." }), {
      status: 429,
      headers: { "content-type": "application/json", "retry-after": "31200" },
    });
    expect(await getErrorMessage(res, "x")).toBe("You've reached your usage limit. It resets in 8h 40m.");
  });

  it("never shows an HTML error page", async () => {
    const res = new Response("<!DOCTYPE html><html>boom</html>", { status: 502, headers: { "content-type": "text/html" } });
    expect(await getErrorMessage(res, "x")).toBe("Something went wrong on our side. Please try again in a moment.");
  });
});
