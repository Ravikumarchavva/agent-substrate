import { bffFor } from "@/lib/session";

/** Start sign-in. `?next=/path` returns there afterwards; `?org=<alias>` signs in to that organization (project). */
export const GET = (request: Request) => bffFor(new URL(request.url).searchParams.get("org")).loginHandler(request);
