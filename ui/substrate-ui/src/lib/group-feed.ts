import { api } from "@/lib/api";
import { FeedClient } from "@/lib/realtime";

/** The one feed for the signed-in person's groups, shared by the page (lists, unread) and whichever group is open. */
export const groupFeed = new FeedClient((since, signal) => api.openGroupFeed(since, signal));
