"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { ApiError, api, isComputeUnavailable, post } from "../components/api";
import { number } from "../lib/format";
import {
  type CatalogTree,
  type CatalogAsset,
  type Chunk,
  type Collection,
  type Dataset,
  type Import,
  type Me,
  type Overview,
  type Pipeline,
  type Profile,
  type QueryRecord,
  type Result,
  type Run,
  type Securable,
  type StatementResponse,
  type Table,
  datasetFrom,
  resultFrom,
} from "../lib/types";

const hex = () => crypto.randomUUID().replaceAll("-", "");

/** The catalog tree the caller may browse, plus every visible table, in one pass. */
export async function loadCatalog(): Promise<CatalogTree> {
  let assetsAvailable = true;
  // A newer web client may connect to an API process started before asset routes existed.
  // Only a missing optional endpoint falls back; authorization and storage errors still surface.
  async function assets<T>(path: string, fallback: T): Promise<T> {
    try {
      return await api<T>(path);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        assetsAvailable = false;
        return fallback;
      }
      throw error;
    }
  }
  const { catalogs } = await api<{ catalogs: Securable[] }>(
    "/catalogs?page_size=1000",
  );
  const tree = await Promise.all(
    catalogs.map(async (catalog) => {
      const { schemas } = await api<{ schemas: Securable[] }>(
        `/catalogs/${catalog.name}/schemas?page_size=1000`,
      );
      const withTables = await Promise.all(
        schemas.map(async (schema) => {
          const base = `/catalogs/${catalog.name}/schemas/${schema.name}`;
          const [{ tables }, { views }, { volumes }, { models }] =
            await Promise.all([
              api<{ tables: Table[] }>(`${base}/tables?page_size=1000`),
              api<{
                views: (Securable & {
                  sql: string;
                  columns: { name: string; type: string }[];
                  created_at: string;
                })[];
              }>(`${base}/views?page_size=1000`),
              assets<{ volumes: CatalogAsset[] }>(
                `${base}/volumes?page_size=1000`,
                { volumes: [] },
              ),
              assets<{ models: CatalogAsset[] }>(
                `${base}/models?page_size=1000`,
                { models: [] },
              ),
            ]);
          return {
            name: schema.name,
            owner: schema.owner,
            volumes,
            models,
            tables: tables.map(datasetFrom),
            views: views.map((v) => ({ ...v, id: v.full_name })),
          };
        }),
      );
      return { name: catalog.name, owner: catalog.owner, schemas: withTables };
    }),
  );
  return {
    assetsAvailable,
    catalogs: tree,
    datasets: tree.flatMap((c) => c.schemas.flatMap((s) => s.tables)),
  };
}

function tablePath(fullName: string) {
  const [catalog, schema, table] = fullName.split(".");
  return `/catalogs/${catalog}/schemas/${schema}/tables/${table}`;
}

/** Direct-to-storage upload of one file with presigned URLs; returns multipart ETags. */
async function uploadFile(
  file: File,
  upload: {
    key: string;
    method: string;
    url?: string;
    part_size?: number;
    parts?: { part_number: number; url: string }[];
  },
) {
  if (upload.method === "PUT") {
    const response = await fetch(upload.url!, { method: "PUT", body: file });
    if (!response.ok)
      throw new Error(`Upload of ${file.name} failed (${response.status})`);
    return { key: upload.key, parts: [] };
  }
  const size = upload.part_size!;
  const parts = await Promise.all(
    upload.parts!.map(async (part) => {
      const start = (part.part_number - 1) * size;
      const response = await fetch(part.url, {
        method: "PUT",
        body: file.slice(start, start + size),
      });
      if (!response.ok)
        throw new Error(`Upload of ${file.name} failed (${response.status})`);
      return {
        part_number: part.part_number,
        etag: (response.headers.get("ETag") || "").replaceAll('"', ""),
      };
    }),
  );
  return { key: upload.key, parts };
}

export function useWorkspace() {
  const client = useQueryClient();
  const [section, setSection] = useState("Data explorer");
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState("Preview");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [sort, setSort] = useState("");
  const [descending, setDescending] = useState(false);
  const [sql, setSql] = useState(
    "-- Select a table from the catalog to get started\nSELECT 1 AS ready;",
  );
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [queryId, setQueryId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [collectionId, setCollectionId] = useState("");
  const [collectionName, setCollectionName] = useState("");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [term, setTerm] = useState("");
  const [matches, setMatches] = useState<Chunk[] | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<Me>("/me"),
    staleTime: 60_000,
  });
  const catalog = useQuery({ queryKey: ["datasets"], queryFn: loadCatalog });
  // Screens treat `datasets` as the flat list of visible tables.
  const datasets = {
    ...catalog,
    data: catalog.data?.datasets,
    refetch: async () => {
      const result = await catalog.refetch();
      return { ...result, data: result.data?.datasets };
    },
  };
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: async (): Promise<Overview> => {
      const value = await api<{
        tables: number;
        rows: number;
        size_bytes: number;
        recent_statements: QueryRecord[];
      }>("/overview");
      return {
        datasets: value.tables,
        rows: value.rows,
        size_bytes: value.size_bytes,
        queries: value.recent_statements,
        runs: [],
        activity: [],
      };
    },
  });
  const history = useQuery({
    queryKey: ["history"],
    queryFn: () =>
      api<{ statements: QueryRecord[] }>("/statements?page_size=100").then(
        (r) => r.statements,
      ),
    enabled: section === "SQL workspace",
  });
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: async () => {
      const value = await api<{ pipelines: Pipeline[]; runs: Run[] }>(
        "/pipelines",
      );
      const byId = new Map(value.pipelines.map((p) => [p.id, p]));
      return {
        definitions: value.pipelines,
        runs: value.runs.map((r) => ({
          ...r,
          name: byId.get(r.pipeline_id)?.name || "pipeline",
          output_id:
            r.status === "succeeded"
              ? byId.get(r.pipeline_id)?.target
              : undefined,
        })),
      };
    },
    enabled: section === "Pipelines",
    refetchInterval: 3000,
  });
  const collections = useQuery({
    queryKey: ["collections"],
    queryFn: () =>
      api<{ collections: Collection[] }>("/collections").then(
        (r) => r.collections,
      ),
    enabled: section === "AI & retrieval",
  });
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: async () => ({
      collections: true,
      text_chunking: true,
      lexical_search: true,
      embeddings: false,
      vector_search: false,
      note: "Documents are chunked on attached compute; lexical search runs in the control plane.",
    }),
    enabled: section === "AI & retrieval",
  });
  const current = datasets.data?.find((d) => d.id === selected);
  const preview = useQuery({
    queryKey: ["preview", selected, offset, sort, descending],
    queryFn: async () =>
      resultFrom(
        await api<StatementResponse>(
          `${tablePath(selected!)}/preview?offset=${offset}&limit=100${
            sort
              ? `&order_by=${encodeURIComponent(sort)}&descending=${descending}`
              : ""
          }`,
        ),
      ),
    enabled: !!selected && section === "Data explorer" && tab === "Preview",
    retry: (count, failure) => !isComputeUnavailable(failure) && count < 1,
  });
  const profile = useQuery({
    queryKey: ["profile", selected],
    queryFn: async (): Promise<Profile> => {
      const value = await api<{
        rows_examined: number;
        sampled: boolean;
        columns: (Omit<Profile["columns"][number], "dtype" | "distinct"> & {
          type: string;
          distinct_approx: number;
        })[];
      }>(`${tablePath(selected!)}/profile`);
      return {
        rows_examined: value.rows_examined,
        sampled: value.sampled,
        sampling: "First rows of the table, up to 100,000",
        duplicates: 0,
        columns: value.columns.map(({ type, distinct_approx, ...c }) => ({
          ...c,
          dtype: type,
          distinct: distinct_approx,
        })),
      };
    },
    enabled: !!selected && section === "Data explorer" && tab === "Profile",
  });
  const chunks = useQuery({
    queryKey: ["chunks", collectionId],
    queryFn: () =>
      api<{ chunks: Chunk[] }>(`/collections/${collectionId}/chunks`).then(
        (r) => r.chunks,
      ),
    enabled: !!collectionId && section === "AI & retrieval",
    refetchInterval: 3000,
  });
  const visible = (datasets.data || []).filter((d) =>
    d.name.toLowerCase().includes(search.toLowerCase()),
  );
  const activeError =
    error ||
    [
      me,
      datasets,
      overview,
      history,
      pipelines,
      collections,
      preview,
      profile,
      chunks,
    ].find((q) => q.isError && !isComputeUnavailable(q.error))?.error?.message;

  async function act(name: string, work: () => Promise<void>) {
    setBusy(name);
    setError("");
    setNotice("");
    try {
      await work();
      await client.invalidateQueries({ queryKey: ["overview"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }
  function open(d: Dataset) {
    setSelected(d.id);
    setOffset(0);
    setSort("");
    setTab("Preview");
    setSection("Data explorer");
  }
  function queryTable(d: Dataset) {
    setSql(`SELECT *\nFROM ${d.id}\nLIMIT 100;`);
    setSection("SQL workspace");
  }
  async function execute(
    computeId = "",
    statementSql = sql,
    namespace: { catalog: string; schema: string } = {
      catalog: "main",
      schema: "default",
    },
  ) {
    const id = hex();
    setQueryId(id);
    await act("query", async () => {
      setResult(null);
      try {
        setResult(
          resultFrom(
            await api<StatementResponse>(
              "/statements",
              post({
                ...namespace,
                sql: statementSql,
                statement_id: id,
                compute_id: computeId || null,
              }),
            ),
          ),
        );
      } finally {
        await client.invalidateQueries({ queryKey: ["history"] });
      }
    });
    setQueryId(null);
  }
  async function download(
    format: "csv" | "arrow",
    computeId = "",
    namespace: { catalog: string; schema: string } = {
      catalog: "main",
      schema: "default",
    },
  ) {
    await act("export", async () => {
      let blob: Blob;
      if (format === "arrow") {
        const response = await fetch("/api/v1/statements", {
          ...post({ ...namespace, sql, compute_id: computeId || null }),
          headers: {
            "Content-Type": "application/json",
            Accept: "application/vnd.apache.arrow.stream",
          },
        });
        if (!response.ok) throw new Error((await response.json()).detail);
        blob = await response.blob();
      } else {
        const value = await api<StatementResponse>(
          "/statements",
          post({
            ...namespace,
            sql,
            compute_id: computeId || null,
            max_rows: 100000,
          }),
        );
        const escape = (v: unknown) => {
          if (v == null) return "";
          let text = typeof v === "object" ? JSON.stringify(v) : String(v);
          // Neutralize spreadsheet formula triggers in text cells.
          if (typeof v === "string" && /^[=+\-@]/.test(text)) text = "'" + text;
          return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
        };
        blob = new Blob(
          [
            [
              value.columns.map((c) => escape(c.name)).join(","),
              ...value.rows.map((r) => r.map(escape).join(",")),
            ].join("\n"),
          ],
          { type: "text/csv" },
        );
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `results.${format}`;
      a.click();
      URL.revokeObjectURL(url);
      setNotice(
        format === "arrow"
          ? "Downloaded the full result as Arrow."
          : "Downloaded up to 100,000 rows as CSV.",
      );
    });
  }
  async function importFile(
    file: File,
    table?: string,
    partitioning: { column: string; transform: string }[] = [],
  ) {
    await act("import", async () => {
      const name =
        file.name
          .replace(/\.[^.]+$/, "")
          .toLowerCase()
          .replace(/[^a-z0-9_]/g, "_")
          .replace(/^([0-9])/, "t_$1")
          .slice(0, 64) || "upload";
      const target = table || `main.default.${name}`;
      const created = await api<{
        import: Import;
        uploads: Parameters<typeof uploadFile>[1][];
      }>(
        "/imports",
        post({
          table: target,
          mode: "create",
          partitioning,
          files: [{ name: file.name, size: file.size }],
        }),
      );
      const completed = await uploadFile(file, created.uploads[0]);
      let record = await api<Import>(
        `/imports/${created.import.id}:complete`,
        post({ files: [completed] }),
      );
      setNotice(`Uploaded ${file.name}; ingesting on attached compute…`);
      while (!["succeeded", "failed"].includes(record.status)) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        record = await api<Import>(`/imports/${record.id}`);
      }
      if (record.status === "failed")
        throw new Error(record.error || "Import failed");
      await client.invalidateQueries({ queryKey: ["datasets"] });
      const loaded = await client.fetchQuery({
        queryKey: ["datasets"],
        queryFn: loadCatalog,
        staleTime: 0,
      });
      const created_table = loaded.datasets.find((d) => d.id === target);
      if (created_table) open(created_table);
      setNotice(`Imported ${number(record.rows || 0)} rows into ${target}.`);
    });
  }
  const titleForSection =
    section === "Data explorer" && current ? current.name : section;

  return {
    client,
    section,
    setSection,
    selected,
    setSelected,
    tab,
    setTab,
    search,
    setSearch,
    offset,
    setOffset,
    sort,
    setSort,
    descending,
    setDescending,
    sql,
    setSql,
    result,
    setResult,
    busy,
    setBusy,
    queryId,
    setQueryId,
    error,
    setError,
    notice,
    setNotice,
    collectionId,
    setCollectionId,
    collectionName,
    setCollectionName,
    title,
    setTitle,
    text,
    setText,
    term,
    setTerm,
    matches,
    setMatches,
    fileInput,
    me,
    catalog,
    datasets,
    overview,
    history,
    pipelines,
    collections,
    capabilities,
    current,
    preview,
    profile,
    chunks,
    visible,
    activeError,
    act,
    open,
    queryTable,
    execute,
    download,
    importFile,
    titleForSection,
  };
}
export type WorkspaceModel = ReturnType<typeof useWorkspace>;
