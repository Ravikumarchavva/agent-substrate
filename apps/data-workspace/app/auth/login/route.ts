import { bff } from "../../../lib/session";

export const GET = (request: Request) => bff.loginHandler(request);
