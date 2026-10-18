import { afterEach, describe, expect, it, vi } from "vitest";
import { toast, visibleToasts } from "@/design/ui/Toast";
import { confirmAction } from "@/design/ui/Confirm";

afterEach(() => {
  visibleToasts().forEach((t) => toast.dismiss(t.id));
  vi.useRealTimers();
});

describe("toast", () => {
  it("shows the same message once, not once per failure", () => {
    toast.error("Couldn't save", "Check your connection");
    toast.error("Couldn't save", "Check your connection");
    expect(visibleToasts()).toHaveLength(1);
  });

  it("keeps at most four on screen, newest last", () => {
    for (let i = 0; i < 6; i++) toast.info(`Message ${i}`);
    expect(visibleToasts().map((t) => t.title)).toEqual(["Message 2", "Message 3", "Message 4", "Message 5"]);
  });

  it("dismisses itself, errors later than successes", () => {
    vi.useFakeTimers();
    toast.success("Renamed");
    toast.error("Couldn't delete");
    vi.advanceTimersByTime(4000);
    expect(visibleToasts().map((t) => t.title)).toEqual(["Couldn't delete"]);
    vi.advanceTimersByTime(5000);
    expect(visibleToasts()).toHaveLength(0);
  });
});

describe("confirmAction", () => {
  it("a second question answers the first with no", async () => {
    const first = confirmAction({ title: "Delete?" });
    void confirmAction({ title: "Really delete?" });
    await expect(first).resolves.toBe(false);
  });
});
