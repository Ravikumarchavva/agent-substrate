#!/usr/bin/env bash
#
# build_rootfs.sh — Prepare a Firecracker rootfs with Python + guest agent
#
# Usage:
#   sudo ./build_rootfs.sh [BASE_ROOTFS] [OUTPUT_ROOTFS]
#
# Defaults:
#   BASE_ROOTFS  = /home/administrator/ubuntu-24.04.ext4
#   OUTPUT_ROOTFS = /home/administrator/code-interpreter-rootfs.ext4
#
set -euo pipefail

BASE_ROOTFS="${1:-/home/administrator/ubuntu-24.04.ext4}"
OUTPUT_ROOTFS="${2:-/home/administrator/code-interpreter-rootfs.ext4}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GUEST_AGENT="${SCRIPT_DIR}/guest_agent/agent.py"
MOUNT_POINT="$(mktemp -d /tmp/rootfs-mount-XXXXXX)"

echo "=== Firecracker Code Interpreter Rootfs Builder ==="
echo "  Base rootfs : ${BASE_ROOTFS}"
echo "  Output      : ${OUTPUT_ROOTFS}"
echo "  Guest agent : ${GUEST_AGENT}"
echo ""

# ── Sanity checks ────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Must run as root (sudo)."
    exit 1
fi

if [ ! -f "${BASE_ROOTFS}" ]; then
    echo "ERROR: Base rootfs not found: ${BASE_ROOTFS}"
    exit 1
fi

if [ ! -f "${GUEST_AGENT}" ]; then
    echo "ERROR: Guest agent not found: ${GUEST_AGENT}"
    exit 1
fi

# ── Copy base rootfs ────────────────────────────────────────────────────────
echo "[1/6] Copying base rootfs..."
cp "${BASE_ROOTFS}" "${OUTPUT_ROOTFS}"

# Resize to 2 GB to have room for Python packages
echo "[2/6] Resizing to 2 GB..."
truncate -s 2G "${OUTPUT_ROOTFS}"
e2fsck -f -y "${OUTPUT_ROOTFS}" || true
resize2fs "${OUTPUT_ROOTFS}"

# ── Mount ────────────────────────────────────────────────────────────────────
echo "[3/6] Mounting rootfs..."
mount -o loop "${OUTPUT_ROOTFS}" "${MOUNT_POINT}"

cleanup() {
    echo "Cleaning up..."
    umount "${MOUNT_POINT}" 2>/dev/null || true
    rmdir "${MOUNT_POINT}" 2>/dev/null || true
}
trap cleanup EXIT

# Mount required virtual filesystems for chroot
mount -t proc proc "${MOUNT_POINT}/proc"
mount -t sysfs sys "${MOUNT_POINT}/sys"
mount --bind /dev "${MOUNT_POINT}/dev"
mount --bind /dev/pts "${MOUNT_POINT}/dev/pts"

# Copy resolv.conf for network access during build
cp /etc/resolv.conf "${MOUNT_POINT}/etc/resolv.conf"

# ── Install packages inside chroot ──────────────────────────────────────────
echo "[4/6] Installing Python and dependencies in rootfs..."

# Fix GPG / tmp permissions inside chroot
chmod 1777 "${MOUNT_POINT}/tmp"
mkdir -p "${MOUNT_POINT}/etc/apt/apt.conf.d"
echo 'Acquire::AllowInsecureRepositories "true";' > "${MOUNT_POINT}/etc/apt/apt.conf.d/99allow-insecure"
echo 'APT::Get::AllowUnauthenticated "true";'    >> "${MOUNT_POINT}/etc/apt/apt.conf.d/99allow-insecure"
echo 'Dir::Cache::archives "/var/cache/apt/archives";' >> "${MOUNT_POINT}/etc/apt/apt.conf.d/99allow-insecure"

chroot "${MOUNT_POINT}" /bin/bash -c '
    export DEBIAN_FRONTEND=noninteractive
    export HOME=/root
    export TMPDIR=/tmp

    apt-get update -qq --allow-insecure-repositories 2>&1 | tail -5
    apt-get install -y -qq --allow-unauthenticated --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        2>&1 | tail -5

    echo "Python installed: $(python3 --version 2>&1)"

    # Install common data-science packages (lightweight set)
    python3 -m pip install --break-system-packages --no-cache-dir -q \
        numpy \
        pandas \
        matplotlib \
        scipy \
        sympy \
        requests \
        2>&1 | tail -5 || echo "WARNING: Some pip packages failed to install"

    # Clean up to save space
    apt-get clean
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
'

# ── Install guest agent ─────────────────────────────────────────────────────
echo "[5/6] Installing guest agent..."
mkdir -p "${MOUNT_POINT}/opt/agent"
cp "${GUEST_AGENT}" "${MOUNT_POINT}/opt/agent/agent.py"
chmod 755 "${MOUNT_POINT}/opt/agent/agent.py"

# Create systemd service for the guest agent
cat > "${MOUNT_POINT}/etc/systemd/system/guest-agent.service" << 'EOF'
[Unit]
Description=Code Interpreter Guest Agent
After=network.target
DefaultDependencies=no

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/agent/agent.py
StandardOutput=journal+console
StandardError=journal+console
Restart=no
TimeoutStartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Enable the service
chroot "${MOUNT_POINT}" systemctl enable guest-agent.service 2>/dev/null || true

# Also add to rc.local as fallback (simpler boot paths)
cat > "${MOUNT_POINT}/etc/rc.local" << 'RCEOF'
#!/bin/sh
/usr/bin/python3 /opt/agent/agent.py &
exit 0
RCEOF
chmod 755 "${MOUNT_POINT}/etc/rc.local"

# ── Unmount ──────────────────────────────────────────────────────────────────
echo "[6/6] Finalizing..."
umount "${MOUNT_POINT}/dev/pts" 2>/dev/null || true
umount "${MOUNT_POINT}/dev" 2>/dev/null || true
umount "${MOUNT_POINT}/proc" 2>/dev/null || true
umount "${MOUNT_POINT}/sys" 2>/dev/null || true
umount "${MOUNT_POINT}"
rmdir "${MOUNT_POINT}"

# Disable the EXIT trap since we cleaned up manually
trap - EXIT

echo ""
echo "✅ Rootfs ready: ${OUTPUT_ROOTFS}"
echo "   Size: $(du -h "${OUTPUT_ROOTFS}" | cut -f1)"
echo ""
echo "Usage:"
echo "   Update config.py → base_rootfs_path = '${OUTPUT_ROOTFS}'"
