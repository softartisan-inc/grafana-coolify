#!/bin/sh
# Prometheus entrypoint. Coolify passes an emptied variable as "" and skips the compose
# ${VAR:-default} (spike S5): empty means default here. Keep the defaults equal to .env.example.
set -eu
: "${PROM_RETENTION_TIME:=90d}" "${PROM_RETENTION_SIZE:=100GB}"
set -- "$@" "--storage.tsdb.retention.time=$PROM_RETENTION_TIME" "--storage.tsdb.retention.size=$PROM_RETENTION_SIZE"
if [ -n "${PROM_ENABLE_FEATURES:-}" ]; then
  set -- "$@" "--enable-feature=$PROM_ENABLE_FEATURES"
fi
exec prometheus "$@"
