#!/usr/bin/env bash
# update_agent.sh — fast rootfs update (no full rebuild needed)
#
# Mounts the code-interpreter rootfs, overwrites /opt/agent/agent.py
# with the latest version, then unmounts.  Much faster than build_rootfs.sh.
#
# Usage:
#   sudo ./update_agent.sh
#   sudo ./update_agent.sh /custom/rootfs.ext4   # explicit path

set -euo pipefail

ROOTFS="${1:-/home/administrator/code-interpreter-rootfs.ext4}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_SRC="${SCRIPT_DIR}/guest_agent/agent.py"

if [[ ! -f "${ROOTFS}" ]]; then
    echo "❌  Rootfs not found: ${ROOTFS}"
    exit 1
fi

if [[ ! -f "${AGENT_SRC}" ]]; then
    echo "❌  Agent source not found: ${AGENT_SRC}"
    exit 1
fi

MOUNT=$(mktemp -d)
echo "📂  Mounting ${ROOTFS} → ${MOUNT}"
mount -o loop "${ROOTFS}" "${MOUNT}"

echo "📝  Copying agent.py → /opt/agent/agent.py"
cp "${AGENT_SRC}" "${MOUNT}/opt/agent/agent.py"
chmod 755 "${MOUNT}/opt/agent/agent.py"

echo "🔍  Verifying..."
python3 -c "
import ast, sys
with open('${MOUNT}/opt/agent/agent.py') as f:
    src = f.read()
ast.parse(src)
print('   ✅  Syntax OK —', len(src.splitlines()), 'lines')
"

umount "${MOUNT}"
rmdir "${MOUNT}"
echo "✅  Rootfs updated: ${ROOTFS}"
