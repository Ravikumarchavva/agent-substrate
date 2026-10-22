"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { Me } from "../lib/types";

// Applications and agents connect as their own identity (OIDC); there are no shared endpoint keys.
export function ComputeApplicationAccess({
  resourceId,
}: {
  resourceId: string;
}) {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me") });
  const origin =
    typeof window === "undefined"
      ? ""
      : window.location.origin.replace(":3011", ":8020");
  const python = `from substrate_data_client import DataClient, ClientCredentials

client = DataClient(
    "${origin}",
    token=ClientCredentials(ISSUER, CLIENT_ID, CLIENT_SECRET),  # a service principal
)
df = client.sql("SELECT * FROM main.default.my_table LIMIT 10", compute_id="${resourceId}")`;
  const curl = `curl -X POST ${origin}/api/v1/statements \\
  -H "Authorization: Bearer $ACCESS_TOKEN" \\
  -H "Accept: application/vnd.apache.arrow.stream" \\
  -d '{"sql": "SELECT 1", "compute_id": "${resourceId}"}' -o result.arrow`;
  return (
    <div className="compute-access">
      <p className="muted">
        Clients authenticate with their own OIDC access token (users or service
        principals) and get exactly the tables their grants allow. Results
        stream as Arrow; nothing is shared.
        {me.data ? ` You are signed in as ${me.data.principal}.` : ""}
      </p>
      <h3>Python (Polars / Arrow)</h3>
      <pre className="code-block">{python}</pre>
      <h3>HTTP</h3>
      <pre className="code-block">{curl}</pre>
    </div>
  );
}
