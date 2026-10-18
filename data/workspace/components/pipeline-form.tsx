"use client";
import { Play } from "lucide-react";
import { useState } from "react";
import { datasetLabel } from "../lib/format";
import type { Dataset } from "../lib/types";
import { ResourcePicker } from "./screens/managed-compute";
import { Button } from "./ui/button";

export function PipelineForm({
  datasets,
  busy,
  create,
}: {
  datasets: Dataset[];
  busy: boolean;
  create: (value: Record<string, unknown>) => Promise<void>;
}) {
  const [computeId, setComputeId] = useState("");
  const [source, setSource] = useState("");
  const [name, setName] = useState("");
  const [columns, setColumns] = useState("");
  const [drop, setDrop] = useState(false);
  const [unique, setUnique] = useState(false);
  const [filter, setFilter] = useState("");
  const [operator, setOperator] = useState("gt");
  const [value, setValue] = useState("");
  return (
    <form
      className="pipeline-form"
      onSubmit={(event) => {
        event.preventDefault();
        void create({
          name,
          compute_id: computeId || null,
          source_id: source,
          columns: columns
            .split(",")
            .map((value) => value.trim())
            .filter(Boolean),
          drop_nulls: drop,
          unique,
          filter_column: filter || null,
          operator,
          value: value || null,
        });
      }}
    >
      <div className="form-grid pipeline-basics">
        <label>
          Pipeline name
          <input
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. clean_orders"
          />
        </label>
        <label>
          Source dataset
          <select
            required
            value={source}
            onChange={(event) => {
              setSource(event.target.value);
              setFilter("");
            }}
          >
            <option value="">Choose a dataset</option>
            {datasets.map((dataset) => (
              <option
                key={dataset.id}
                value={dataset.id}
                data-table-name={dataset.name}
              >
                {datasetLabel(dataset)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <ResourcePicker role="jobs" value={computeId} onChange={setComputeId} />
      <details className="advanced-options">
        <summary>Columns & filters</summary>
        <div className="form-grid">
          <label>
            Output columns
            <input
              value={columns}
              onChange={(event) => setColumns(event.target.value)}
              placeholder="All columns, or comma-separated names"
            />
          </label>
          <label>
            Filter column
            <select
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            >
              <option value="">No filter</option>
              {datasets
                .find((dataset) => dataset.id === source)
                ?.columns.map((column) => (
                  <option key={column.name}>{column.name}</option>
                ))}
            </select>
          </label>
          <label>
            Comparison
            <select
              value={operator}
              onChange={(event) => setOperator(event.target.value)}
            >
              <option value="gt">Greater than</option>
              <option value="lt">Less than</option>
              <option value="eq">Equals</option>
            </select>
          </label>
          <label>
            Filter value
            <input
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder="Value"
              required={!!filter}
            />
          </label>
        </div>
      </details>
      <div className="form-footer">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={drop}
            onChange={(event) => setDrop(event.target.checked)}
          />
          Drop rows with nulls
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={unique}
            onChange={(event) => setUnique(event.target.checked)}
          />
          Remove duplicates
        </label>
        <Button disabled={busy || !datasets.length}>
          <Play size={13} />
          Save & run pipeline
        </Button>
      </div>
    </form>
  );
}
