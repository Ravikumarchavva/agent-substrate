#!/bin/sh
# Start the Loamz agent server.
# If LOAMZ_ROLES is set, mounts only those feature slices (role-split mode).
# Otherwise mounts all roles.
set -eu

ROLES_ARG=""
if [ -n "${LOAMZ_ROLES:-}" ]; then
  ROLES_ARG="--roles ${LOAMZ_ROLES}"
fi

exec loamz-server agents serve ${ROLES_ARG} --host 0.0.0.0 --port "${PORT:-8000}"

