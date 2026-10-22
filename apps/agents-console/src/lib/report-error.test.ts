import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/_client";
import { toast } from "@/design";
import { reportError } from "./report-error";

afterEach(() => vi.restoreAllMocks());

describe("reportError", () => {
  it("says why when the server gave a reason", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const error = vi.spyOn(toast, "error");
    reportError("Couldn't delete the task", new ApiError("You don't have permission to do that.", 403));
    expect(error).toHaveBeenCalledWith("Couldn't delete the task", "You don't have permission to do that.");
  });

  it("suggests checking the connection when the request never arrived", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const error = vi.spyOn(toast, "error");
    reportError("Couldn't load your conversations", new TypeError("Failed to fetch"));
    expect(error).toHaveBeenCalledWith("Couldn't load your conversations", "Check your connection and try again.");
  });

  it("never shows an unknown error's text", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const error = vi.spyOn(toast, "error");
    reportError("Couldn't do that", new Error("ECONNRESET at 10.0.0.4:5432"));
    expect(error).toHaveBeenCalledWith("Couldn't do that");
  });
});
