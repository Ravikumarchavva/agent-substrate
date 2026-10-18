# 0001 — Table format: Apache Iceberg with the SeaweedFS REST catalog

- **Status:** accepted, 2026-10-17.
- **Spike:** `benchmarks/spikes/table_format.py`.

## Decision

v1 stores warehouse tables as **Apache Iceberg**, committed through **SeaweedFS's built-in Iceberg REST catalog** (SeaweedFS ≥ 4.48).

- **Reading:** Polars uses `pl.scan_iceberg`, and workers use DuckDB through the `iceberg` extension with `ATTACH … (TYPE ICEBERG)`.
- **Writing:** Polars writes the Parquet data files and PyIceberg commits them.
- **Delta Lake is not supported**, and we don't build two table formats or two catalog paths.

## Spike setup

- **Data:** NYC yellow taxi 2024, all 12 months, 41,169,720 rows, 661 MB Parquet.
- **Storage:** a throwaway SeaweedFS 4.48 `weed mini`.
- **Versions:** Polars 1.44.2, DuckDB 1.5.6, PyArrow 26.0.0, latest deltalake and PyIceberg.
- **Machine:** 20 cores, loopback S3.
- **Partitioning:** the same scheme for both formats, identity on `pickup_month`.
- **Query:** filter one month plus `passenger_count > 3`, then group by pickup zone. Timings are warm runs 2–3.

| Measure | Delta (delta-rs) | Iceberg (SeaweedFS catalog) |
| --- | --- | --- |
| Write 41.2M rows, 12 commits | 52.2 s | **27.3 s** |
| Open table metadata | ~20 ms | **~5 ms** |
| Partition pruning (files planned) | 12 → 1 | 20 → 2 |
| Filtered aggregate, Polars | ~52 ms | **~44 ms** |
| Filtered aggregate, DuckDB | ~30 ms | **~19 ms** |
| `count(*)` over the whole table, Polars | ~35 ms | **~21 ms** |
| Same answer from Polars and DuckDB | yes | yes |
| 4 concurrent appends | 4 commits; conditional PUT (etag) works on SeaweedFS | 4 commits; catalog compare-and-swap |
| Add a column and append | yes | yes |
| Writer killed mid-append | consistent; no partial commit visible | consistent; only completed commits visible |
| Streaming CSV ingest (688 MB, 6.0M rows, 1M-row batches) | 3.4 s, 2.25 GB peak RSS | 4.1 s, 2.18 GB peak RSS |
| Clustering | `optimize.z_order` (2.3 s per month) | sort in Polars before writing; server-side compaction in SeaweedFS |

## Why Iceberg

1. **Faster on both engines.** Reads were faster on the read paths we use (Polars and DuckDB), and writes were twice as fast.
2. **We don't own the commit catalog.** SeaweedFS provides commits with compare-and-swap, namespaces, views, scan reports and maintenance compaction. The control plane keeps grants, policies and audit, and does not reimplement commit coordination.
3. **Credential vending is already there.** SeaweedFS vends table-scoped credentials (`-s3.iceberg.credentialRole`), which gives Milestone 4 a ready base.
4. **The open standard.** Iceberg REST is what DuckDB, Spark, Trino, Dremio and Unity Catalog all speak, so outside engines can read our tables without an export.

## Consequences and follow-ups

- **Upgrade SeaweedFS.** `deployment/seaweedfs.compose.yml` moves from 3.97 to 4.48, enables the Iceberg port, and removes the 512 MB cap (`volumeSizeLimitMB=128`, `volume.max=4`), which can't hold real tables.
- **Normalize schemas at ingest.** Real data drifts: TLC months switch between `ns` and `us` timestamps. Iceberg v2 stores `us`, so ingest casts timestamps to `us`, and the ingest job rejects or coerces drift explicitly.
- **Pass storage options explicitly.** `pl.scan_iceberg` does not inherit the catalog's S3 endpoint and otherwise falls back to the EC2 metadata service. Workers pass `storage_options` (or vended credentials) explicitly.
- **Large ingests must not commit per batch.** PyIceberg `append` commits every call, so a 500 GB CSV must not become thousands of snapshots. Milestone 1 ingest:
  1. Polars streams `scan_csv` → partitioned `sink_parquet` straight into the table location.
  2. Register the written files with **one** `add_files` / fast-append commit.
  3. Verify that peak memory stays flat at full-dataset scale; the spike's 2.2 GB peak was measured on a 688 MB file only.
- **Clustering:** PyIceberg has no Z-order. Writers sort within each partition (for example by `PULocationID`) before writing, and compaction relies on SeaweedFS's Iceberg maintenance. Measure the effect on point lookups in Milestone 3.
- **Orphan files:** a killed writer leaves uncommitted data files behind. Orphan-file cleanup is a scheduled maintenance task.
