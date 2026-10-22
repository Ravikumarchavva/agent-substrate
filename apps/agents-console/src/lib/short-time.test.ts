import { describe, expect, it } from "vitest";
import { shortTime } from "./short-time";

describe("shortTime", () => {
  const now = new Date(2026, 9, 5, 15, 0); // Oct 5, 3 pm
  it("shows the clock today, the weekday this week, else the date", () => {
    expect(shortTime(new Date(2026, 9, 5, 9, 5).toISOString(), now)).toMatch(/9:05/);
    expect(shortTime(new Date(2026, 9, 3, 9, 5).toISOString(), now)).toBe("Sat");
    expect(shortTime(new Date(2026, 8, 3, 9, 5).toISOString(), now)).toBe("Sep 3");
  });
  it("is empty for nothing or nonsense", () => {
    expect(shortTime(null)).toBe("");
    expect(shortTime("not a date")).toBe("");
  });
});
