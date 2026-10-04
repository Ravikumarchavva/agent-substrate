import { beforeEach, describe, expect, it } from "vitest";
import { Segmented } from "@/design/ui/Segmented";
import { CHAT_WIDTH_KEY, MOTION_KEY, applyAppearance, readChatWidth, readMotion, setChatWidth, setMotion } from "./appearance";

function fakeBrowser() {
  const store = new Map<string, string>();
  const classes = new Set<string>();
  const props = new Map<string, string>();
  Object.assign(globalThis, {
    window: {},
    localStorage: { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k) },
    document: { documentElement: { style: { setProperty: (k: string, v: string) => void props.set(k, v) }, classList: { toggle: (c: string, on: boolean) => void (on ? classes.add(c) : classes.delete(c)) } } },
  });
  return { store, classes, props };
}

describe("appearance", () => {
  let env: ReturnType<typeof fakeBrowser>;
  beforeEach(() => {
    env = fakeBrowser();
  });

  it("defaults to a medium chat and system motion, storing nothing", () => {
    expect([readChatWidth(), readMotion()]).toEqual(["medium", "system"]);
    applyAppearance();
    expect(env.props.get("--chat-width")).toBe("54rem");
    expect(env.classes.has("reduce-motion")).toBe(false);
  });

  it("applies a chosen width and reduced motion at once, and forgets the default", () => {
    setChatWidth("wide");
    setMotion("reduced");
    expect(env.props.get("--chat-width")).toBe("68rem");
    expect(env.classes.has("reduce-motion")).toBe(true);
    setChatWidth("medium");
    setMotion("system");
    expect(env.store.has(CHAT_WIDTH_KEY) || env.store.has(MOTION_KEY)).toBe(false);
    expect(env.classes.has("reduce-motion")).toBe(false);
  });

  it("ignores a stored value it does not know", () => {
    env.store.set(CHAT_WIDTH_KEY, "gigantic");
    expect(readChatWidth()).toBe("medium");
  });

  it("exports the segmented control", () => {
    expect(typeof Segmented).toBe("function");
  });
});
