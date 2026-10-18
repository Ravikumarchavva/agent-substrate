import { describe, expect, it } from "vitest";
import { formatResetIn, limitTone } from "./limits";

describe("limits", () => {
  it("words a reset time", () => {
    expect(formatResetIn(0)).toBe("now");
    expect(formatResetIn(45)).toBe("45s");
    expect(formatResetIn(7500)).toBe("2h 5m");
    expect(formatResetIn(7200)).toBe("2h");
    expect(formatResetIn(600)).toBe("10m");
  });
  it("turns amber at 75% and red when reached", () => {
    expect(limitTone(10, 60)).toBe("success");
    expect(limitTone(45, 60)).toBe("warning");
    expect(limitTone(60, 60)).toBe("danger");
    expect(limitTone(0, 0)).toBe("danger");
  });
});
