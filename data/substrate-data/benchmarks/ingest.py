"""Ingest a large CSV end to end and report throughput, memory and correctness.

Generates ROWS rows of taxi-like CSV in a scratch directory (streamed, never held in memory), uploads
it straight to object storage with presigned multipart URLs, has attached compute convert it to a
month-partitioned Iceberg table, then checks the row count and a partition-pruned query. Run the
compute with a job memory ceiling to prove memory is bounded regardless of input size:

    SUBSTRATE_DATA_WORKER_JOB_MEMORY_MB=4096 make dev-compute
    ROWS=100000000 uv run python benchmarks/ingest.py

The table is dropped at the end. Needs about 60 bytes of scratch disk per row.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

import polars as pl
from dotenv import dotenv_values
from substrate_data_client import ClientCredentials, DataClient

ENV = {**dotenv_values(".env"), **os.environ}
ROWS = int(ENV.get("ROWS", 20_000_000))
CHUNK = 5_000_000
FILES = int(ENV.get("FILES", 4))
TABLE = "main.nyc.bench_ingest"
client = DataClient(
    "http://127.0.0.1:8020",
    token=ClientCredentials(
        ENV["SUBSTRATE_DATA_OIDC_ISSUER"], "substrate-data-bench", ENV["BENCH_CLIENT_SECRET"]
    ),
    timeout=3600,
)


def used_mb() -> int:
    with open("/proc/meminfo") as meminfo:
        values = {line.split(":")[0]: int(line.split()[1]) for line in meminfo}
    return (values["MemTotal"] - values["MemAvailable"]) // 1024


def jobs_worker_pid(port: int = 8012) -> int | None:
    """The process listening on the jobs worker's port."""
    import re
    import subprocess

    out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if f":{port} " in line and (match := re.search(r"pid=(\d+)", line)):
            return int(match.group(1))
    return None


def tree_rss_mb(root: int) -> int:
    """Resident memory of a process and all its descendants (job children are forked from it)."""
    parents, rss = {}, {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            parents[int(entry.name)] = int(stat[1])
            rss[int(entry.name)] = int(stat[21]) * os.sysconf("SC_PAGE_SIZE")
        except OSError, IndexError, ValueError:
            continue
    members, grew = {root}, True
    while grew:
        grew = False
        for pid, parent in parents.items():
            if parent in members and pid not in members:
                members.add(pid)
                grew = True
    return sum(rss.get(pid, 0) for pid in members) // 2**20


class Sampler(threading.Thread):
    """Peak memory while the ingest runs: of the jobs worker's process tree (what the ceiling
    limits) and, for context, of the whole machine (which also holds storage, API and this script)."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.root = jobs_worker_pid()
        self.peak, self.worker_peak, self.stop = used_mb(), 0, threading.Event()

    def run(self) -> None:
        while not self.stop.wait(0.25):
            self.peak = max(self.peak, used_mb())
            if self.root:
                self.worker_peak = max(self.worker_peak, tree_rss_mb(self.root))


def generate(directory: Path) -> list[Path]:
    paths = []
    per_file = ROWS // FILES
    start = time.perf_counter()
    for index in range(FILES):
        path = directory / f"part-{index}.csv"
        with path.open("wb") as out:
            written = 0
            while written < per_file:
                n = min(CHUNK, per_file - written)
                base = index * per_file + written
                frame = pl.DataFrame(
                    {
                        "id": pl.int_range(base, base + n, eager=True),
                        "pickup": pl.datetime_range(
                            pl.datetime(2025, 1, 1), pl.datetime(2025, 12, 31, 23), "1s", eager=True
                        )
                        .sample(n, with_replacement=True, seed=base)
                        .sort(),
                        "zone": pl.int_range(0, n, eager=True) % 263,
                        "fare": (pl.int_range(0, n, eager=True) % 9000).cast(pl.Float64) / 100,
                        "flag": pl.Series(["Y", "N", "N", "U"]).sample(
                            n, with_replacement=True, seed=base
                        ),
                    }
                )
                frame.write_csv(out, include_header=written == 0)
                written += n
        paths.append(path)
    size = sum(p.stat().st_size for p in paths)
    print(f"generated {ROWS:,} rows, {size / 1e9:.2f} GB in {time.perf_counter() - start:.0f} s")
    return paths


def main() -> None:
    directory = Path(tempfile.mkdtemp(prefix="substrate-ingest-", dir=ENV.get("SCRATCH") or None))
    try:
        files = generate(directory)
        size = sum(p.stat().st_size for p in files)
        baseline = used_mb()
        sampler = Sampler()
        sampler.start()
        start = time.perf_counter()
        client.upload(
            TABLE,
            files,
            partitioning=[{"column": "pickup", "transform": "month"}],
            parallelism=8,
            poll_seconds=2,
        )
        elapsed = time.perf_counter() - start
        sampler.stop.set()
        table = client.table(TABLE)
        print(
            f"ingested {table['row_count']:,} rows in {elapsed:.0f} s "
            f"({size / 1e6 / elapsed:.0f} MB/s CSV, {table['row_count'] / elapsed / 1e6:.2f} M rows/s)"
        )
        print(
            f"parquet {table['size_bytes'] / 1e9:.2f} GB in {table['file_count']} files "
            f"({size / table['size_bytes']:.1f}x smaller than the CSV)"
        )
        print(
            f"jobs worker (process tree) peak: {sampler.worker_peak} MB; whole machine: "
            f"baseline {baseline} MB, peak {sampler.peak} MB (+{sampler.peak - baseline} MB)"
        )
        assert table["row_count"] == ROWS, f"row count {table['row_count']} != {ROWS}"
        counted = client.sql(f"SELECT count(*) AS n, count(DISTINCT id) AS ids FROM {TABLE}")
        assert counted["n"][0] == ROWS and counted["ids"][0] == ROWS, counted
        plan = client.request(
            "POST",
            "/statements",
            json={"sql": f"SELECT count(*) FROM {TABLE} WHERE pickup >= '2025-12-01'"},
        )
        print(f"December only: scans {plan['files_scanned']} of {table['file_count']} files")
        print("verified: every row present exactly once")
    finally:
        shutil.rmtree(directory, ignore_errors=True)
        try:
            client.request("DELETE", f"/catalogs/main/schemas/nyc/tables/{TABLE.split('.')[-1]}")
        except Exception as exc:  # noqa: BLE001
            print("cleanup:", exc)


if __name__ == "__main__":
    main()
