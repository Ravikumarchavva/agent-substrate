import { describe as suite, expect, it } from "vitest";
import { DEFAULT_CHOICE, describe, toChoice, toSchedule } from "./schedule";

suite("schedule", () => {
  it("builds the cron a choice means", () => {
    expect(toSchedule({ ...DEFAULT_CHOICE, frequency: "daily", time: "08:30" })).toEqual({ kind: "cron", expression: "30 8 * * *" });
    expect(toSchedule({ ...DEFAULT_CHOICE, frequency: "weekdays", time: "09:00" }).expression).toBe("0 9 * * 1-5");
    expect(toSchedule({ ...DEFAULT_CHOICE, frequency: "weekly", time: "16:00", weekday: 5 }).expression).toBe("0 16 * * 5");
    expect(toSchedule({ ...DEFAULT_CHOICE, frequency: "monthly", time: "07:05", day: 3 }).expression).toBe("5 7 3 * *");
    expect(toSchedule({ ...DEFAULT_CHOICE, frequency: "hourly" })).toEqual({ kind: "interval", expression: "3600" });
    expect(toSchedule({ ...DEFAULT_CHOICE, frequency: "custom", custom: " */5 * * * * " }).expression).toBe("*/5 * * * *");
  });

  it("reads a stored schedule back into the same choice", () => {
    for (const frequency of ["daily", "weekdays", "weekly", "monthly", "hourly"] as const) {
      const choice = { ...DEFAULT_CHOICE, frequency, time: "16:45", weekday: 5, day: 12 };
      const back = toChoice(toSchedule(choice));
      expect(back.frequency).toBe(frequency);
      if (frequency !== "hourly") expect(back.time).toBe("16:45");
    }
    expect(toChoice({ kind: "cron", expression: "*/5 * * * *" })).toMatchObject({ frequency: "custom", custom: "*/5 * * * *" });
  });

  it("says it in words", () => {
    expect(describe({ kind: "cron", expression: "0 8 * * 1-5" })).toBe("Weekdays at 8:00 AM");
    expect(describe({ kind: "cron", expression: "0 16 * * 5" })).toBe("Every Friday at 4:00 PM");
    expect(describe({ kind: "cron", expression: "0 0 * * *" })).toBe("Daily at 12:00 AM");
    expect(describe({ kind: "cron", expression: "0 9 1 * *" })).toBe("Monthly on the 1st at 9:00 AM");
    expect(describe({ kind: "interval", expression: "7200" })).toBe("Every 2 hours");
    expect(describe({ kind: "interval", expression: "3600" })).toBe("Every hour");
    expect(describe({ kind: "cron", expression: "*/5 * * * *" })).toBe("Cron: */5 * * * *");
  });
});
