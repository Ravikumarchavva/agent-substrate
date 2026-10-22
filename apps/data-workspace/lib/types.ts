import type { Row } from "../components/grid";

// ---- /api/v1 shapes
export type Column = { name: string; type: string; nullable?: boolean };
export type Table = {
  name: string;
  full_name: string;
  catalog_name: string;
  schema_name: string;
  owner: string;
  comment: string | null;
  format: string;
  columns: Column[];
  partitioning: { column: string; transform: string }[];
  row_count: number;
  size_bytes: number;
  file_count: number;
  snapshot_id: string | null;
  parent_table: string | null;
  materialized?: Materialized | null;
  created_at: string;
  updated_at: string;
};
export type Materialized = {
  sql: string;
  status: "fresh" | "refreshing" | "failed";
  refreshed_at: string | null;
  stale: boolean;
  sources: string[];
  error: string | null;
  incremental: boolean;
  refresh_every_minutes: number | null;
};
export type MaintenanceRun = {
  id: string;
  kind: "optimize" | "refresh" | "expire";
  status: "running" | "succeeded" | "failed" | "skipped";
  params: Record<string, unknown>;
  files_in: number | null;
  files_out: number | null;
  rows: number | null;
  bytes_before: number | null;
  bytes_after: number | null;
  error: string | null;
  principal: string;
  created_at: string;
  finished_at: string | null;
};
export type Policy = {
  row_filter: string | null;
  column_masks: {
    column: string;
    mask: "null" | "redact" | "hash" | "expression";
    expression?: string | null;
  }[];
  exempt: string[];
  updated_by: string;
  updated_at: string;
  version: number;
};
export type Securable = {
  name: string;
  full_name: string;
  owner: string;
  comment: string | null;
};
export type ViewDef = {
  id: string; // full name: catalog.schema.view
  name: string;
  full_name: string;
  owner: string;
  comment: string | null;
  sql: string;
  columns: { name: string; type: string }[];
  created_at: string;
};
export type CatalogAsset = {
  id: string;
  kind: "volume" | "model";
  name: string;
  full_name: string;
  owner: string;
  comment: string | null;
  created_at: string;
  properties: {
    bucket?: string;
    prefix?: string;
    artifact_uri?: string;
    framework?: string;
    model_version?: number;
  };
};
export type CatalogTree = {
  assetsAvailable?: boolean;
  catalogs: {
    name: string;
    owner: string;
    schemas: {
      name: string;
      owner: string;
      tables: Dataset[];
      views: ViewDef[];
      volumes: CatalogAsset[];
      models: CatalogAsset[];
    }[];
  }[];
  datasets: Dataset[];
};
export type GrantAssignment = { principal: string; privileges: string[] };
export type StatementResponse = {
  statement_id: string;
  duration_ms: number;
  files_scanned: number;
  timing_ms?: { plan: number; compute: number };
  columns: Column[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  total?: number;
  offset?: number;
};
export type Import = {
  id: string;
  table: string;
  mode: string;
  format: string;
  status: string;
  rows: number | null;
  error: string | null;
  created_at: string;
};
export type Me = {
  principal: string;
  name: string;
  email: string | null;
  groups: string[];
  is_admin: boolean;
};

// ---- view models the screens render
export type Dataset = {
  id: string; // full name: catalog.schema.table
  catalog_name: string;
  schema_name: string;
  comment: string | null;
  snapshot_id: string | null;
  updated_at: string;
  name: string;
  display_name: string;
  format: string;
  source_format: string;
  size_bytes: number;
  row_count: number;
  file_count: number;
  columns: { name: string; dtype: string }[];
  partitioning: { column: string; transform: string }[];
  parent_id: string | null;
  materialized: Materialized | null;
  owner: string;
  created_at: string;
};
export type Result = {
  id: string;
  columns: string[];
  rows: Row[];
  row_count: number;
  duration_ms: number;
  truncated: boolean;
  files_scanned?: number;
  total?: number;
};
export type QueryRecord = {
  id: string;
  sql: string;
  status: string;
  row_count?: number;
  duration_ms: number;
  error?: string;
  created_at: string;
};
export type Run = {
  id: string;
  pipeline_id: string;
  name: string;
  status: string;
  rows?: number;
  output_id?: string;
  duration_ms?: number;
  error?: string;
  created_at: string;
};
export type Pipeline = {
  id: string;
  name: string;
  source: string;
  target: string;
  definition: { columns: string[]; drop_nulls: boolean; unique: boolean };
};
export type Overview = {
  datasets: number;
  rows: number;
  size_bytes: number;
  queries: QueryRecord[];
  runs: Run[];
  activity: { id: string; message: string; created_at: string }[];
};
export type Profile = {
  rows_examined: number;
  sampled: boolean;
  sampling: string;
  duplicates: number;
  columns: {
    name: string;
    dtype: string;
    nulls: number;
    null_percent: number;
    distinct: number;
    min?: number | string;
    max?: number | string;
    mean?: number;
    std?: number;
    min_length?: number;
    max_length?: number;
  }[];
};
export type Collection = { id: string; name: string };
export type Chunk = {
  title: string;
  text: string;
  document_id: string;
  score?: number;
};

export function datasetFrom(table: Table): Dataset {
  return {
    id: table.full_name,
    name: table.full_name,
    catalog_name: table.catalog_name,
    schema_name: table.schema_name,
    comment: table.comment,
    snapshot_id: table.snapshot_id,
    updated_at: table.updated_at,
    display_name: table.name,
    format: table.format,
    source_format: table.format,
    size_bytes: table.size_bytes,
    row_count: table.row_count,
    file_count: table.file_count,
    columns: table.columns.map((c) => ({ name: c.name, dtype: c.type })),
    partitioning: table.partitioning,
    parent_id: table.parent_table,
    materialized: table.materialized ?? null,
    owner: table.owner,
    created_at: table.created_at,
  };
}

export function resultFrom(response: StatementResponse): Result {
  const names = response.columns.map((c) => c.name);
  return {
    id: response.statement_id,
    columns: names,
    rows: response.rows.map((values) =>
      Object.fromEntries(names.map((name, i) => [name, values[i]])),
    ),
    row_count: response.row_count,
    duration_ms: response.duration_ms,
    truncated: response.truncated,
    files_scanned: response.files_scanned,
    total: response.total,
  };
}
