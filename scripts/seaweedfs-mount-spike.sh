#!/bin/sh
# Run inside a privileged container with `weed` and `/dev/fuse` available.
# Usage: seaweedfs-mount-spike.sh <filer-host:port> [mount-dir]
set -eu

filer=${1:?filer host:port is required}
mount_dir=${2:-/mnt/seaweedfs-spike}
root="$mount_dir/gdpr-mount-spike"

mkdir -p "$mount_dir"
weed mount -filer="$filer" -dir="$mount_dir" >/tmp/weed-mount-spike.log 2>&1 &
mount_pid=$!
cleanup() {
  rm -rf "$root" 2>/dev/null || true
  kill "$mount_pid" 2>/dev/null || true
  wait "$mount_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _ in $(seq 1 30); do
  grep -q "mounted" /tmp/weed-mount-spike.log && break
  sleep 1
done
grep -q "mounted" /tmp/weed-mount-spike.log

mkdir -p "$root/a" "$root/b"
printf initial > "$root/a/source"
test "$(cat "$root/a/source")" = initial
printf -- -append >> "$root/a/source"
test "$(cat "$root/a/source")" = initial-append
truncate -s 7 "$root/a/source"
test "$(cat "$root/a/source")" = initial
cp "$root/a/source" "$root/b/copied"
mv "$root/a/source" "$root/b/moved"
test -f "$root/b/moved"
test ! -e "$root/a/source"

# `mv -f` must leave either the old or entirely new file visible, never a
# partial destination. This is the write pattern used by safe office saves.
printf old > "$root/b/atomic"
printf replacement > "$root/a/replacement"
mv -f "$root/a/replacement" "$root/b/atomic"
test "$(cat "$root/b/atomic")" = replacement

# Random-offset writes and a generated-office-file-sized payload (100 MiB).
dd if=/dev/zero of="$root/a/large.docx" bs=1M count=100 status=none
printf marker | dd of="$root/a/large.docx" bs=1 seek=1048576 conv=notrunc status=none
test "$(dd if="$root/a/large.docx" bs=1 skip=1048576 count=6 status=none)" = marker

# Many rapid create/modify/delete operations.
for i in $(seq 1 500); do
  printf '%s' "$i" > "$root/a/small-$i"
  printf x >> "$root/a/small-$i"
  rm "$root/a/small-$i"
done

# Concurrent writers must both complete and leave independently readable files.
(
  for i in $(seq 1 200); do printf a >> "$root/a/writer-a"; done
) &
pid_a=$!
(
  for i in $(seq 1 200); do printf b >> "$root/a/writer-b"; done
) &
pid_b=$!
wait "$pid_a"
wait "$pid_b"
test "$(wc -c < "$root/a/writer-a")" -eq 200
test "$(wc -c < "$root/a/writer-b")" -eq 200

# BetterOffice-shaped concurrency: full-buffer overwrite while another reader
# repeatedly opens the document. A successful test proves no truncated read.
dd if=/dev/zero of="$root/a/concurrent.xlsx" bs=1M count=4 status=none
(
  for i in $(seq 1 40); do cat "$root/a/concurrent.xlsx" >/dev/null; done
) &
reader=$!
for i in $(seq 1 10); do
  dd if=/dev/zero of="$root/a/.save.tmp" bs=1M count=4 status=none
  mv -f "$root/a/.save.tmp" "$root/a/concurrent.xlsx"
done
wait "$reader"
test "$(wc -c < "$root/a/concurrent.xlsx")" -eq 4194304

echo "SeaweedFS Filer mount spike: PASS"
