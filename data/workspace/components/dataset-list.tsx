"use client";
import { ArrowUpRight, Database } from "lucide-react";
import { bytes, datasetLabel, number } from "../lib/format";
import { type Dataset } from "../lib/types";
export function DatasetList({
  items,
  open,
  query,
}: {
  items: Dataset[];
  open: (d: Dataset) => void;
  query: (d: Dataset) => void;
}) {
  return (
    <div className="table-overflow">
      <table className="standard-table catalog-table">
        <thead>
          <tr>
            <th>Dataset</th>
            <th>Format</th>
            <th>Rows</th>
            <th>Size</th>
            <th aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {items.map((d) => (
            <tr key={d.id}>
              <td>
                <button
                  className="dataset-link"
                  data-table-name={d.name}
                  onClick={() => open(d)}
                >
                  <div className="table-icon">
                    <Database size={16} />
                  </div>
                  <span>
                    {datasetLabel(d)}
                    <small
                      className={
                        d.source_format === "generated-sample"
                          ? "sample-note"
                          : undefined
                      }
                    >
                      {d.source_format === "generated-sample"
                        ? "Generated sample dataset"
                        : `${d.columns.length} columns · ${d.source_format}`}
                    </small>
                  </span>
                </button>
              </td>
              <td>
                <span className="badge">{d.format}</span>
              </td>
              <td>{number(d.row_count)}</td>
              <td className="muted">{bytes(d.size_bytes)}</td>
              <td>
                <button
                  className="icon-button"
                  aria-label={`Query ${datasetLabel(d)}`}
                  onClick={() => query(d)}
                >
                  <ArrowUpRight size={16} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
