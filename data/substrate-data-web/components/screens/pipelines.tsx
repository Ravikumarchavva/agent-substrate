"use client";
import {
  ArrowRight,
  ArrowUpRight,
  Database,
  Play,
  Workflow,
} from "lucide-react";
import type { WorkspaceModel } from "../../hooks/use-workspace";
import { time } from "../../lib/format";
import { type Pipeline, type Run } from "../../lib/types";
import { api, post } from "../api";
import { Empty } from "../empty";
import { PipelineForm } from "../pipeline-form";
import { Button } from "../ui/button";
export function PipelinesScreen({ model }: { model: WorkspaceModel }) {
  const { client, busy, setNotice, datasets, pipelines, act, open } = model;
  return (
    <>
      <section className="panel">
        <div className="panel-heading">
          <h2>New transformation</h2>
          <span className="badge">Polars streaming</span>
        </div>
        <PipelineForm
          datasets={datasets.data || []}
          busy={!!busy}
          create={(definition) =>
            act("pipeline", async () => {
              const source = String(definition.source_id);
              const [catalog, schema] = source.split(".");
              const target = `${catalog}.${schema}.${String(definition.name)
                .toLowerCase()
                .replace(/[^a-z0-9_]/g, "_")
                .replace(/^([0-9])/, "t_$1")
                .slice(0, 64)}`;
              const p = await api<Pipeline>(
                "/pipelines",
                post({
                  name: definition.name,
                  source,
                  target,
                  definition: {
                    columns: definition.columns,
                    filter_column: definition.filter_column,
                    operator: definition.operator,
                    value: definition.value,
                    drop_nulls: definition.drop_nulls,
                    unique: definition.unique,
                  },
                }),
              );
              await api<Run>(`/pipelines/${p.id}:run`, post({}));
              await client.invalidateQueries({ queryKey: ["pipelines"] });
              setNotice(
                `Pipeline started on attached compute; output goes to ${target}.`,
              );
            })
          }
        />
      </section>
      <section className="panel section-gap">
        <div className="panel-heading">
          <h2>Saved pipelines</h2>
        </div>
        {pipelines.data?.definitions.map((p) => (
          <div className="pipeline-row" key={p.id}>
            <div className="pipeline-path">
              <Database size={16} />
              <span>{p.source}</span>
              <ArrowRight size={14} />
              <Workflow size={16} />
              <strong>{p.name}</strong>
            </div>
            <Button
              variant="secondary"
              disabled={!!busy}
              onClick={() =>
                void act("pipeline", async () => {
                  await api<Run>(`/pipelines/${p.id}:run`, post({}));
                  await client.invalidateQueries({ queryKey: ["pipelines"] });
                  setNotice("Pipeline started on attached compute.");
                })
              }
            >
              <Play size={13} />
              Run again
            </Button>
          </div>
        ))}
        {!pipelines.data?.definitions.length && (
          <Empty text="Define and run a transformation to save a pipeline." />
        )}
      </section>
      <section className="panel section-gap">
        <div className="panel-heading">
          <h2>Run history</h2>
        </div>
        {pipelines.data?.runs.map((r) => (
          <div className="pipeline-row" key={r.id}>
            <div>
              <strong>{r.name}</strong>
              <small>
                {time(r.created_at)} · {r.duration_ms ?? "—"} ms
              </small>
              {r.error && <p className="error-text">{r.error}</p>}
            </div>
            <span className="badge">{r.status}</span>
            {r.output_id && (
              <Button
                variant="secondary"
                onClick={async () => {
                  const tables = await datasets.refetch();
                  const d = tables.data?.find((d) => d.id === r.output_id);
                  if (d) open(d);
                }}
              >
                Inspect output <ArrowUpRight size={13} />
              </Button>
            )}
          </div>
        ))}
      </section>
    </>
  );
}
