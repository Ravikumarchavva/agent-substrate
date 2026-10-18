import { toast } from "@/design";
import { ApiError } from "@/lib/api/_client";

/**
 * Tell the user an action failed, and why in words they can act on. `title` says what they were trying to do ("Couldn't delete the thread");
 * the reason comes from the server's own message for an API failure, or is a plain network hint when the request never arrived.
 * The error is still logged for developers.
 */
export function reportError(title: string, err: unknown): void {
  console.error(title, err);
  if (err instanceof ApiError) return void toast.error(title, err.message);
  if (err instanceof TypeError) return void toast.error(title, "Check your connection and try again.");
  toast.error(title);
}
