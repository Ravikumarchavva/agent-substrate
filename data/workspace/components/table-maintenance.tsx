"use client";

// Maintenance tab of a table: materialized-view freshness, optimize (compact / sort / Z-order), policies, history.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "./ui/button";
import { ApiError, api, post } from "./api";
import type { Dataset, MaintenanceRun, Policy } from "../lib/types";

const base = (d: Dataset) => {
  const [c, s, t] = d.id.split(".");
  return `/catalogs/${c}/schemas/${s}/tables/${t}`;
};
const when = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "never";
const fail = (error: unknown) =>
  error instanceof ApiError || error instanceof Error
    ? error.message
    : "Request failed";

export function TableMaintenance({
  dataset,
  isOwner,
}: {
  dataset: Dataset;
  isOwner: boolean;
}) {
  const client = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const [strategy, setStrategy] = useState("compact");
  const [columns, setColumns] = useState("");
  const [bloom, setBloom] = useState("");
  const names = dataset.columns.map((c) => c.name);

  const runs = useQuery({
    queryKey: ["maintenance", dataset.id],
    queryFn: () =>
      api<{ runs: MaintenanceRun[] }>(`${base(dataset)}/maintenance-runs`).then(
        (r) => r.runs,
      ),
    refetchInterval: (query) =>
      query.state.data?.some((r) => r.status === "running") ? 2000 : false,
  });
  const policy = useQuery({
    queryKey: ["policy", dataset.id],
    enabled: isOwner,
    retry: false,
    queryFn: () =>
      api<Policy>(`${base(dataset)}/policy`).catch((error) => {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }),
  });

  async function run(label: string, action: () => Promise<unknown>) {
    setMessage(null);
    try {
      await action();
      setMessage(`${label} started`);
      await client.invalidateQueries({ queryKey: ["maintenance", dataset.id] });
      await client.invalidateQueries({ queryKey: ["table", dataset.id] });
      await client.invalidateQueries({ queryKey: ["datasets"] });
    } catch (error) {
      setMessage(fail(error));
    }
  }

  const split = (text: string) =>
    text
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);

  // The catalog list is a snapshot; a view that is refreshing must show its own live state.
  const live = useQuery({
    queryKey: ["table", dataset.id],
    queryFn: () =>
      api<{ materialized?: Dataset["materialized"] }>(base(dataset)),
    initialData: { materialized: dataset.materialized },
    refetchInterval: (query) =>
      query.state.data?.materialized?.status === "refreshing" ? 2000 : false,
  });
  const mv = live.data?.materialized ?? null;
  return (
    <div className="unity-card" data-testid="maintenance">
      {message && (
        <div role="status" className="unity-notice">
          {message}
        </div>
      )}

      {mv && (
        <section>
          <h3>Materialized view</h3>
          <div className="unity-details-grid">
            <div className="unity-detail-label">Status</div>
            <div className="unity-detail-value">
              {mv.status}
              {mv.stale && mv.status !== "refreshing" ? " · stale" : ""}
            </div>
            <div className="unity-detail-label">Last refreshed</div>
            <div className="unity-detail-value">{when(mv.refreshed_at)}</div>
            <div className="unity-detail-label">Reads</div>
            <div className="unity-detail-value">{mv.sources.join(", ")}</div>
            <div className="unity-detail-label">Refresh</div>
            <div className="unity-detail-value">
              {mv.refresh_every_minutes
                ? `every ${mv.refresh_every_minutes} min when a source changed`
                : "on demand"}
              {mv.incremental ? " · incremental" : ""}
            </div>
            {mv.error && (
              <>
                <div className="unity-detail-label">Error</div>
                <div className="unity-detail-value">{mv.error}</div>
              </>
            )}
          </div>
          <pre className="unity-code">{mv.sql}</pre>
          <Button
            variant="secondary"
            disabled={mv.status === "refreshing"}
            onClick={() =>
              run("Refresh", () => api(`${base(dataset)}:refresh`, post({})))
            }
          >
            Refresh now
          </Button>
          {mv.incremental && (
            <Button
              variant="secondary"
              disabled={mv.status === "refreshing"}
              onClick={() =>
                run("Full refresh", () =>
                  api(`${base(dataset)}:refresh?full=true`, post({})),
                )
              }
            >
              Full refresh
            </Button>
          )}
        </section>
      )}

      <section>
        <h3>Optimize files</h3>
        <p className="unity-hint">
          Rewrites files on attached compute and swaps them in one atomic
          commit. Sorting clusters rows so queries skip more files; bloom
          filters speed up equality lookups.
        </p>
        <label>
          Strategy{" "}
          <select
            aria-label="Strategy"
            value={strategy}
            onChange={(e) => setStrategy(e.target.value)}
          >
            <option value="compact">Compact small files</option>
            <option value="sort">Sort by columns</option>
            <option value="zorder">Z-order (2–4 columns)</option>
          </select>
        </label>
        {strategy !== "compact" && (
          <label>
            Columns{" "}
            <input
              aria-label="Sort columns"
              list="maintenance-columns"
              placeholder={names.slice(0, 2).join(", ")}
              value={columns}
              onChange={(e) => setColumns(e.target.value)}
            />
          </label>
        )}
        <label>
          Bloom filters on{" "}
          <input
            aria-label="Bloom columns"
            list="maintenance-columns"
            placeholder="optional, e.g. id"
            value={bloom}
            onChange={(e) => setBloom(e.target.value)}
          />
        </label>
        <datalist id="maintenance-columns">
          {names.map((n) => (
            <option key={n} value={n} />
          ))}
        </datalist>
        <Button
          onClick={() =>
            run("Optimize", () =>
              api(
                `${base(dataset)}:optimize`,
                post({
                  strategy,
                  sort_by: strategy === "compact" ? [] : split(columns),
                  bloom_columns: split(bloom),
                }),
              ),
            )
          }
        >
          Optimize
        </Button>
        <Button
          variant="secondary"
          onClick={() =>
            run("Clean-up", () =>
              api(
                `${base(dataset)}:expire`,
                post({ older_than_hours: 168, retain_last: 3 }),
              ),
            )
          }
        >
          Expire old snapshots
        </Button>
      </section>

      {isOwner && (
        <PolicyEditor
          dataset={dataset}
          policy={policy.data ?? null}
          run={run}
        />
      )}

      <section>
        <h3>History</h3>
        <table className="unity-table">
          <thead>
            <tr>
              <th>WHEN</th>
              <th>WHAT</th>
              <th>STATUS</th>
              <th>FILES</th>
              <th>BY</th>
            </tr>
          </thead>
          <tbody>
            {(runs.data ?? []).map((r) => (
              <tr key={r.id} title={r.error ?? undefined}>
                <td>{when(r.created_at)}</td>
                <td>
                  {r.kind}
                  {r.params?.mode ? ` (${String(r.params.mode)})` : ""}
                </td>
                <td>{r.status}</td>
                <td>
                  {r.files_in ?? "–"} → {r.files_out ?? "–"}
                </td>
                <td>{r.principal}</td>
              </tr>
            ))}
            {!runs.data?.length && (
              <tr>
                <td colSpan={5}>No maintenance yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function PolicyEditor({
  dataset,
  policy,
  run,
}: {
  dataset: Dataset;
  policy: Policy | null;
  run: (label: string, action: () => Promise<unknown>) => Promise<void>;
}) {
  const [rowFilter, setRowFilter] = useState(policy?.row_filter ?? "");
  const [masks, setMasks] = useState(policy?.column_masks ?? []);
  const [exempt, setExempt] = useState((policy?.exempt ?? []).join(", "));
  const [column, setColumn] = useState(dataset.columns[0]?.name ?? "");
  const [mask, setMask] = useState<"null" | "redact" | "hash" | "expression">(
    "redact",
  );
  const [expression, setExpression] = useState("");

  return (
    <section data-testid="policy">
      <h3>Access policy</h3>
      <p className="unity-hint">
        Applies to everyone except the principals listed as exempt, including
        readers who reach this table through a view. Expressions may use{" "}
        <code>is_member(&apos;group&apos;)</code>, <code>current_user()</code>{" "}
        and <code>current_tenant()</code>.
      </p>
      <label>
        Row filter{" "}
        <input
          aria-label="Row filter"
          value={rowFilter}
          placeholder="region = 'EU' OR is_member('global-analysts')"
          onChange={(e) => setRowFilter(e.target.value)}
        />
      </label>
      <ul>
        {masks.map((m) => (
          <li key={m.column}>
            <code>{m.column}</code> → {m.mask}
            {m.mask === "expression" ? ` (${m.expression})` : ""}{" "}
            <Button
              variant="secondary"
              onClick={() =>
                setMasks(masks.filter((x) => x.column !== m.column))
              }
            >
              Remove
            </Button>
          </li>
        ))}
      </ul>
      <select
        aria-label="Mask column"
        value={column}
        onChange={(e) => setColumn(e.target.value)}
      >
        {dataset.columns.map((c) => (
          <option key={c.name}>{c.name}</option>
        ))}
      </select>
      <select
        aria-label="Mask kind"
        value={mask}
        onChange={(e) => setMask(e.target.value as typeof mask)}
      >
        <option value="redact">Redact (***)</option>
        <option value="null">Null</option>
        <option value="hash">Hash (salted)</option>
        <option value="expression">Expression</option>
      </select>
      {mask === "expression" && (
        <input
          aria-label="Mask expression"
          value={expression}
          placeholder="round(salary, -3)"
          onChange={(e) => setExpression(e.target.value)}
        />
      )}
      <Button
        variant="secondary"
        onClick={() =>
          setMasks([
            ...masks.filter((m) => m.column !== column),
            {
              column,
              mask,
              expression: mask === "expression" ? expression : null,
            },
          ])
        }
      >
        Add mask
      </Button>
      <label>
        Exempt{" "}
        <input
          aria-label="Exempt principals"
          value={exempt}
          placeholder="group:auditors, user:alice"
          onChange={(e) => setExempt(e.target.value)}
        />
      </label>
      <Button
        onClick={() =>
          run("Policy saved", () =>
            api(
              `${base(dataset)}/policy`,
              post(
                {
                  row_filter: rowFilter.trim() || null,
                  column_masks: masks,
                  exempt: exempt
                    .split(",")
                    .map((v) => v.trim())
                    .filter(Boolean),
                },
                "PUT",
              ),
            ),
          )
        }
      >
        Save policy
      </Button>
      {policy && (
        <Button
          variant="secondary"
          onClick={() =>
            run("Policy removed", () =>
              api(`${base(dataset)}/policy`, { method: "DELETE" }),
            )
          }
        >
          Remove policy
        </Button>
      )}
    </section>
  );
}
