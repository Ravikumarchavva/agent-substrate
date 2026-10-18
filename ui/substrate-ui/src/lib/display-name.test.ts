import { beforeEach, describe, expect, it, vi } from "vitest";

const store = new Map<string, string>();
vi.stubGlobal("localStorage", {
  getItem: (k: string) => store.get(k) ?? null,
  setItem: (k: string, v: string) => void store.set(k, v),
  removeItem: (k: string) => void store.delete(k),
});
vi.stubGlobal("window", { dispatchEvent: vi.fn() });

import { getDisplayName, nameToShow, setDisplayName } from "./display-name";

describe("display name", () => {
  beforeEach(() => store.clear());

  it("shows the chosen name, else the account's, else the fallback", () => {
    expect(nameToShow("Ravikumar Chavva", "User")).toBe("Ravikumar Chavva");
    expect(nameToShow(null, "User")).toBe("User");
    setDisplayName("Ravi");
    expect(nameToShow("Ravikumar Chavva", "User")).toBe("Ravi");
  });

  it("keeps it on one line, caps its length and lets it be cleared", () => {
    setDisplayName("  Ravi \n  Kumar ");
    expect(getDisplayName()).toBe("Ravi Kumar");
    setDisplayName("x".repeat(100));
    expect(getDisplayName()).toHaveLength(60);
    setDisplayName("   ");
    expect(getDisplayName()).toBe("");
  });
});
