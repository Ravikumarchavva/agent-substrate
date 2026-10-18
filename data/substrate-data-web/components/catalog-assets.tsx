"use client";
import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Box, Download, Folder, FolderPlus, Plus, Upload } from "lucide-react";
import { useRef, useState } from "react";
import type { CatalogAsset } from "../lib/types";
import { bytes, time } from "../lib/format";
import { api, post } from "./api";
import { Button } from "./ui/button";

const assetPath = (asset: CatalogAsset) => {
  const [c, s, n] = asset.full_name.split(".");
  return `/catalogs/${c}/schemas/${s}/${asset.kind === "volume" ? "volumes" : "models"}/${n}`;
};
export function SchemaAssets({
  catalog,
  schema,
  kind,
  items,
  onSelect,
  onCreated,
}: {
  catalog: string;
  schema: string;
  kind: "volumes" | "models";
  items: CatalogAsset[];
  onSelect: (asset: CatalogAsset) => void;
  onCreated: () => Promise<unknown>;
}) {
  const [create, setCreate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <section className="catalog-assets-panel">
      <div className="catalog-section-heading">
        <div>
          <h2>{kind === "volumes" ? "Volumes" : "Registered models"}</h2>
          <p className="muted">
            {kind === "volumes"
              ? "Files and folders, stored independently of tables."
              : "Model artifact references, framework, and ownership."}
          </p>
        </div>
        <Button onClick={() => setCreate(!create)}>
          <Plus size={14} />
          {kind === "volumes" ? "Create volume" : "Register model"}
        </Button>
      </div>
      {error && (
        <p role="alert" className="alert error">
          {error}
        </p>
      )}
      {create && (
        <form
          className="catalog-create-form"
          onSubmit={async (event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            setBusy(true);
            setError("");
            try {
              await api(
                `/catalogs/${catalog}/schemas/${schema}/${kind}`,
                post({
                  name: data.get("name"),
                  comment: data.get("comment") || null,
                  ...(kind === "models"
                    ? {
                        artifact_uri: data.get("artifact_uri"),
                        framework: data.get("framework") || null,
                      }
                    : {}),
                }),
              );
              await onCreated();
              setCreate(false);
            } catch (error) {
              setError(String(error));
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            Name
            <input
              name="name"
              required
              pattern="[a-z_][a-z0-9_]{0,63}"
              placeholder={kind === "volumes" ? "documents" : "embedding_model"}
            />
          </label>
          <label>
            Description
            <input name="comment" maxLength={1000} />
          </label>
          {kind === "models" && (
            <>
              <label>
                Model artifact URI
                <input
                  name="artifact_uri"
                  required
                  placeholder="s3://model-artifacts/model-v1/"
                />
              </label>
              <label>
                Framework
                <input
                  name="framework"
                  placeholder="e.g. transformers, sklearn"
                />
              </label>
            </>
          )}
          <div className="result-actions">
            <Button disabled={busy}>{busy ? "Saving…" : "Save"}</Button>
            <Button
              variant="secondary"
              type="button"
              onClick={() => setCreate(false)}
            >
              Cancel
            </Button>
          </div>
        </form>
      )}
      <table className="unity-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Description</th>
            <th>Owner</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>
                <button className="text-link" onClick={() => onSelect(item)}>
                  {item.kind === "volume" ? (
                    <Folder size={15} />
                  ) : (
                    <Box size={15} />
                  )}{" "}
                  {item.name}
                </button>
              </td>
              <td>{item.comment || "—"}</td>
              <td>{item.owner}</td>
              <td>{time(item.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!items.length && (
        <div className="catalog-empty-state">
          {kind === "volumes" ? <FolderPlus size={28} /> : <Box size={28} />}
          <h3>No {kind} in this schema</h3>
          <p>
            {kind === "volumes"
              ? "Create a volume to organize documents, images, and other files."
              : "Register a model artifact to make its owner and location discoverable."}
          </p>
        </div>
      )}
    </section>
  );
}

type VolumeEntry = {
  path: string;
  name?: string;
  size_bytes?: number;
  directory: boolean;
  modified_at?: string;
};
export function CatalogAssetDetail({
  asset,
  onBack,
}: {
  asset: CatalogAsset;
  onBack: () => void;
}) {
  const client = useQueryClient();
  const chooser = useRef<HTMLInputElement>(null);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const base = assetPath(asset);
  const files = useInfiniteQuery({
    queryKey: ["volume-files", asset.id, path],
    initialPageParam: "",
    queryFn: ({ pageParam }) =>
      api<{
        files: VolumeEntry[];
        directories: VolumeEntry[];
        continuation: string | null;
      }>(
        `${base}/files?path=${encodeURIComponent(path)}${pageParam ? `&continuation=${encodeURIComponent(pageParam)}` : ""}`,
      ),
    getNextPageParam: (lastPage) => lastPage.continuation || undefined,
    enabled: asset.kind === "volume",
  });
  async function upload(file: File) {
    if (file.size > 64 * 1024 * 1024) {
      setError("Volume uploads support files up to 64 MiB.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const access = await api<{ url: string }>(
        `${base}/access`,
        post({
          path: (path ? path + "/" : "") + file.name,
          action: "upload",
          size_bytes: file.size,
        }),
      );
      const response = await fetch(access.url, { method: "PUT", body: file });
      if (!response.ok) throw new Error("File upload failed");
      await client.invalidateQueries({ queryKey: ["volume-files", asset.id] });
    } catch (error) {
      setError(String(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="catalog-asset-detail">
      <button className="text-link" onClick={onBack}>
        ← Back to schema
      </button>
      <div className="unity-entity-header">
        <div className="unity-title-group">
          {asset.kind === "volume" ? <Folder size={24} /> : <Box size={24} />}
          <h1 className="unity-entity-title">{asset.name}</h1>
          <span className="badge">
            {asset.kind === "volume" ? "Volume" : "Registered model"}
          </span>
        </div>
        {asset.kind === "volume" && (
          <Button disabled={busy} onClick={() => chooser.current?.click()}>
            <Upload size={14} />
            {busy ? "Uploading…" : "Upload file"}
          </Button>
        )}
      </div>
      <p className="muted catalog-asset-description">
        {asset.full_name} · {asset.comment || "No description"}
      </p>
      <dl className="catalog-property-list">
        <div>
          <dt>Owner</dt>
          <dd>{asset.owner}</dd>
        </div>
        <div>
          <dt>Created</dt>
          <dd>{time(asset.created_at)}</dd>
        </div>
        <div>
          <dt>
            {asset.kind === "volume" ? "Storage location" : "Artifact URI"}
          </dt>
          <dd>
            <code>
              {asset.kind === "volume"
                ? `s3://${asset.properties.bucket}/${asset.properties.prefix}`
                : asset.properties.artifact_uri}
            </code>
          </dd>
        </div>
        {asset.kind === "model" && (
          <>
            <div>
              <dt>Version</dt>
              <dd>{asset.properties.model_version}</dd>
            </div>
            <div>
              <dt>Framework</dt>
              <dd>{asset.properties.framework || "Unspecified"}</dd>
            </div>
          </>
        )}
      </dl>
      <AssetPermissions asset={asset} />
      {(error || files.error) && (
        <p role="alert" className="alert error">
          {error || files.error?.message}
        </p>
      )}
      {asset.kind === "model" ? (
        <p className="muted">
          This registration points to a model artifact. Model serving and
          inference deployment are configured separately.
        </p>
      ) : (
        <>
          <input
            type="file"
            ref={chooser}
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void upload(file);
              event.target.value = "";
            }}
          />
          <div className="catalog-section-heading">
            <h2>Files</h2>
            <span>{path || "/"}</span>
            {path && (
              <button
                className="text-link"
                onClick={() => setPath(path.split("/").slice(0, -1).join("/"))}
              >
                Parent folder
              </button>
            )}
          </div>
          <table className="unity-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Size</th>
                <th>Modified</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {[
                ...(files.data?.pages.flatMap((page) => page.directories) ??
                  []),
                ...(files.data?.pages.flatMap((page) => page.files) ?? []),
              ].map((entry) => (
                <tr key={entry.path}>
                  <td>
                    {entry.directory ? (
                      <button
                        className="text-link"
                        onClick={() => setPath(entry.path)}
                      >
                        <Folder size={15} />
                        {entry.path.split("/").pop()}
                      </button>
                    ) : (
                      entry.name
                    )}
                  </td>
                  <td>
                    {entry.directory ? "—" : bytes(entry.size_bytes ?? 0)}
                  </td>
                  <td>{entry.modified_at ? time(entry.modified_at) : "—"}</td>
                  <td>
                    {!entry.directory && (
                      <button
                        className="text-link"
                        aria-label={`Download ${entry.name}`}
                        onClick={async () => {
                          try {
                            const access = await api<{ url: string }>(
                              `${base}/access`,
                              post({ path: entry.path, action: "download" }),
                            );
                            window.open(
                              access.url,
                              "_blank",
                              "noopener,noreferrer",
                            );
                          } catch (error) {
                            setError(String(error));
                          }
                        }}
                      >
                        <Download size={15} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {files.isLoading ? (
            <p className="muted">Loading files…</p>
          ) : (
            !files.data?.pages.some((page) => page.files.length) &&
            !files.data?.pages.some((page) => page.directories.length) &&
            !files.error && (
              <div className="catalog-empty-state">
                <FolderPlus size={28} />
                <h3>This volume is empty</h3>
                <p>Upload a file to get started. No compute is needed.</p>
              </div>
            )
          )}
          {files.hasNextPage && (
            <Button
              variant="secondary"
              disabled={files.isFetchingNextPage}
              onClick={() => void files.fetchNextPage()}
            >
              {files.isFetchingNextPage ? "Loading…" : "Load more files"}
            </Button>
          )}
        </>
      )}
    </section>
  );
}

function AssetPermissions({ asset }: { asset: CatalogAsset }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const path = `/grants/${asset.kind}/${asset.full_name}`;
  const grants = useQuery({
    queryKey: ["asset-grants", asset.id],
    queryFn: () =>
      api<{
        privilege_assignments: { principal: string; privileges: string[] }[];
      }>(path),
    enabled: open,
  });
  async function change(principal: string, privilege: string, remove = false) {
    setBusy(true);
    setError("");
    try {
      await api(
        path,
        post(
          {
            changes: [{ principal, [remove ? "remove" : "add"]: [privilege] }],
          },
          "PATCH",
        ),
      );
      await grants.refetch();
    } catch (error) {
      setError(String(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="catalog-asset-permissions">
      <button
        type="button"
        className="text-link"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        Permissions
      </button>
      {open && (
        <div className="unity-card">
          <h2>Access grants</h2>
          <p className="muted">
            Parent USE_CATALOG and USE_SCHEMA grants are also required. Owners
            and administrators manage access.
          </p>
          {(error || grants.error) && (
            <p role="alert">{error || grants.error?.message}</p>
          )}
          {grants.isPending ? (
            <p>Loading grants…</p>
          ) : (
            <table className="unity-table">
              <thead>
                <tr>
                  <th>Principal</th>
                  <th>Explicit privileges</th>
                </tr>
              </thead>
              <tbody>
                {grants.data?.privilege_assignments.map((assignment) => (
                  <tr key={assignment.principal}>
                    <td>{assignment.principal}</td>
                    <td>
                      {assignment.privileges.map((privilege) => (
                        <button
                          key={privilege}
                          type="button"
                          className="badge"
                          disabled={busy}
                          title={`Revoke ${privilege} from ${assignment.principal}`}
                          onClick={() =>
                            void change(assignment.principal, privilege, true)
                          }
                        >
                          {privilege} ×
                        </button>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {!grants.isPending &&
            !grants.error &&
            !grants.data?.privilege_assignments.length && (
              <p className="muted">
                No explicit grants. Access may be inherited from the parent
                namespace.
              </p>
            )}
          <form
            className="catalog-grant-form"
            onSubmit={async (event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              await change(
                String(data.get("principal")),
                String(data.get("privilege")),
              );
            }}
          >
            <label>
              Principal
              <input
                name="principal"
                required
                pattern="(user|group|service):.{1,200}"
                placeholder="group:analysts"
              />
            </label>
            <label>
              Privilege
              <select name="privilege">
                {(asset.kind === "volume"
                  ? ["READ_VOLUME", "WRITE_VOLUME"]
                  : ["USE_MODEL"]
                ).map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
            <Button disabled={busy}>{busy ? "Saving…" : "Grant access"}</Button>
          </form>
        </div>
      )}
    </section>
  );
}
