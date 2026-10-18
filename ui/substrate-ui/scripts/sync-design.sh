#!/usr/bin/env bash
# Copy the design system (src/design) into agent-substrate-platform. This repo is the source of truth; the platform's copy is
# never edited by hand. Run after changing anything under src/design, then commit both repos.
#
#   scripts/sync-design.sh [path-to-platform-repo]     default: ../agent-substrate-platform
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
platform="${1:-$here/../agent-substrate-platform}"
[ -d "$platform/src" ] || { echo "platform repo not found at $platform" >&2; exit 1; }
rsync -a --delete "$here/src/design/" "$platform/src/design/"
echo "synced $here/src/design -> $platform/src/design"
