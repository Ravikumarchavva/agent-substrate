/** Schedules as people say them ("Weekdays at 8:00 AM") and as the server stores them (a cron expression, or seconds for an interval). */

export type Frequency = "hourly" | "daily" | "weekdays" | "weekly" | "monthly" | "custom";

export interface Schedule {
  kind: "cron" | "interval";
  expression: string;
}

export interface Choice {
  frequency: Frequency;
  /** "HH:MM", 24-hour. */
  time: string;
  /** 0 = Sunday … 6 = Saturday. */
  weekday: number;
  /** Day of month for monthly. */
  day: number;
  custom: string;
}

export const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export const FREQUENCIES: { value: Frequency; label: string }[] = [
  { value: "hourly", label: "Every hour" },
  { value: "daily", label: "Daily" },
  { value: "weekdays", label: "Weekdays" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "custom", label: "Custom (cron)" },
];

export const DEFAULT_CHOICE: Choice = { frequency: "daily", time: "08:00", weekday: 1, day: 1, custom: "0 8 * * *" };

const split = (time: string): [number, number] => {
  const [h, m] = time.split(":").map((n) => Number.parseInt(n, 10));
  return [Number.isFinite(h) ? h : 8, Number.isFinite(m) ? m : 0];
};

export function toSchedule(c: Choice): Schedule {
  const [h, m] = split(c.time);
  switch (c.frequency) {
    case "hourly":
      return { kind: "interval", expression: "3600" };
    case "daily":
      return { kind: "cron", expression: `${m} ${h} * * *` };
    case "weekdays":
      return { kind: "cron", expression: `${m} ${h} * * 1-5` };
    case "weekly":
      return { kind: "cron", expression: `${m} ${h} * * ${c.weekday}` };
    case "monthly":
      return { kind: "cron", expression: `${m} ${h} ${c.day} * *` };
    default:
      return { kind: "cron", expression: c.custom.trim() };
  }
}

const pad = (n: number) => String(n).padStart(2, "0");

/** Read a stored schedule back into the form's terms; anything it does not recognise is `custom`. */
export function toChoice(s: Schedule): Choice {
  if (s.kind === "interval") {
    return s.expression === "3600" ? { ...DEFAULT_CHOICE, frequency: "hourly" } : { ...DEFAULT_CHOICE, frequency: "custom", custom: s.expression };
  }
  const m = /^(\d{1,2}) (\d{1,2}) (\*|\d{1,2}) \* (\*|[0-6]|1-5)$/.exec(s.expression.trim());
  if (m) {
    const time = `${pad(Number(m[2]))}:${pad(Number(m[1]))}`;
    if (m[3] === "*" && m[4] === "*") return { ...DEFAULT_CHOICE, frequency: "daily", time };
    if (m[3] === "*" && m[4] === "1-5") return { ...DEFAULT_CHOICE, frequency: "weekdays", time };
    if (m[3] === "*") return { ...DEFAULT_CHOICE, frequency: "weekly", time, weekday: Number(m[4]) };
    if (m[4] === "*") return { ...DEFAULT_CHOICE, frequency: "monthly", time, day: Number(m[3]) };
  }
  return { ...DEFAULT_CHOICE, frequency: "custom", custom: s.expression };
}

export function clock(time: string): string {
  const [h, m] = split(time);
  return `${h % 12 === 0 ? 12 : h % 12}:${pad(m)} ${h < 12 ? "AM" : "PM"}`;
}

const ordinal = (n: number) => `${n}${n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th"}`;

/** "Weekdays at 8:00 AM", "Every Friday at 4:00 PM", "Every 2 hours"; a cron it cannot put in words is shown as it is. */
export function describe(s: Schedule): string {
  if (s.kind === "interval") {
    const secs = Number.parseInt(s.expression, 10);
    if (!Number.isFinite(secs) || secs <= 0) return `Every ${s.expression} seconds`;
    const plural = (n: number, unit: string) => (n === 1 ? `Every ${unit}` : `Every ${n} ${unit}s`);
    if (secs % 86400 === 0) return plural(secs / 86400, "day");
    if (secs % 3600 === 0) return plural(secs / 3600, "hour");
    if (secs % 60 === 0) return plural(secs / 60, "minute");
    return plural(secs, "second");
  }
  const c = toChoice(s);
  switch (c.frequency) {
    case "daily":
      return `Daily at ${clock(c.time)}`;
    case "weekdays":
      return `Weekdays at ${clock(c.time)}`;
    case "weekly":
      return `Every ${WEEKDAYS[c.weekday]} at ${clock(c.time)}`;
    case "monthly":
      return `Monthly on the ${ordinal(c.day)} at ${clock(c.time)}`;
    default:
      return `Cron: ${s.expression}`;
  }
}
