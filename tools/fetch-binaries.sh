#!/usr/bin/env bash
# Download the pinned release binaries used by scripts/check.py and the native
# harness (harness/stack.py), and verify every archive against its SHA-256.
# Usage: tools/fetch-binaries.sh            (installs into ./.bin, idempotent)
#        GC_BIN_DIR=/path tools/fetch-binaries.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=tools/versions.env
. "$ROOT/tools/versions.env"
BIN="${GC_BIN_DIR:-$ROOT/.bin}"
WORK="$BIN/.work"
GH="https://github.com"

if [ "$(uname -s)" != "Linux" ] || [ "$(uname -m)" != "x86_64" ]; then
  echo "fetch-binaries: only linux/x86_64 is supported (got $(uname -s)/$(uname -m))" >&2
  exit 1
fi
for tool in curl sha256sum tar unzip; do
  command -v "$tool" >/dev/null || { echo "fetch-binaries: missing $tool" >&2; exit 1; }
done
mkdir -p "$BIN" "$WORK"

download() { # url destination
  curl -fsSL --retry 3 -o "$2" "$1"
}

verify_sha() { # file expected_sha256
  local actual
  actual="$(sha256sum "$1" | cut -d' ' -f1)"
  if [ "$actual" != "$2" ]; then
    echo "fetch-binaries: checksum mismatch for $(basename "$1"): got $actual, want $2" >&2
    exit 1
  fi
}

verify_from_sums() { # archive sums_file
  local name expected
  name="$(basename "$1")"
  expected="$(awk -v n="$name" '$2 == n || $2 == "*" n { print $1; exit }' "$2")"
  if [ -z "$expected" ]; then
    echo "fetch-binaries: no checksum for $name in $(basename "$2")" >&2
    exit 1
  fi
  verify_sha "$1" "$expected"
}

is_installed() { # marker version
  [ "$(cat "$BIN/.$1.version" 2>/dev/null || true)" = "$2" ]
}

mark_installed() { # marker version
  printf '%s\n' "$2" > "$BIN/.$1.version"
}

fetch_zip() { # marker version base_url asset sums_asset member target
  local marker=$1 version=$2 base=$3 asset=$4 sums=$5 member=$6 target=$7
  if is_installed "$marker" "$version" && [ -x "$BIN/$target" ]; then return; fi
  download "$base/$asset" "$WORK/$asset"
  download "$base/$sums" "$WORK/$marker.sums"
  verify_from_sums "$WORK/$asset" "$WORK/$marker.sums"
  unzip -o -q "$WORK/$asset" "$member" -d "$WORK/$marker"
  install -m 0755 "$WORK/$marker/$member" "$BIN/$target"
  mark_installed "$marker" "$version"
}

fetch_tar() { # marker version base_url asset sums_asset (member:target)...
  local marker=$1 version=$2 base=$3 asset=$4 sums=$5 pair
  shift 5
  if is_installed "$marker" "$version"; then return; fi
  download "$base/$asset" "$WORK/$asset"
  download "$base/$sums" "$WORK/$marker.sums"
  verify_from_sums "$WORK/$asset" "$WORK/$marker.sums"
  rm -rf "${WORK:?}/$marker" && mkdir -p "$WORK/$marker"
  tar -xzf "$WORK/$asset" -C "$WORK/$marker"
  for pair in "$@"; do
    install -m 0755 "$WORK/$marker/${pair%%:*}" "$BIN/${pair#*:}"
  done
  mark_installed "$marker" "$version"
}

# Grafana Alloy
fetch_zip alloy "$ALLOY_VERSION" "$GH/grafana/alloy/releases/download/$ALLOY_VERSION" \
  alloy-linux-amd64.zip SHA256SUMS alloy-linux-amd64 alloy

# Loki
fetch_zip loki "$LOKI_VERSION" "$GH/grafana/loki/releases/download/v$LOKI_VERSION" \
  loki-linux-amd64.zip SHA256SUMS loki-linux-amd64 loki

# Tempo
fetch_tar tempo "$TEMPO_VERSION" "$GH/grafana/tempo/releases/download/v$TEMPO_VERSION" \
  "tempo_${TEMPO_VERSION}_linux_amd64.tar.gz" SHA256SUMS tempo:tempo

# Prometheus + promtool
fetch_tar prometheus "$PROMETHEUS_VERSION" "$GH/prometheus/prometheus/releases/download/v$PROMETHEUS_VERSION" \
  "prometheus-$PROMETHEUS_VERSION.linux-amd64.tar.gz" sha256sums.txt \
  "prometheus-$PROMETHEUS_VERSION.linux-amd64/prometheus:prometheus" \
  "prometheus-$PROMETHEUS_VERSION.linux-amd64/promtool:promtool"

# node-exporter
fetch_tar node_exporter "$NODE_EXPORTER_VERSION" "$GH/prometheus/node_exporter/releases/download/v$NODE_EXPORTER_VERSION" \
  "node_exporter-$NODE_EXPORTER_VERSION.linux-amd64.tar.gz" sha256sums.txt \
  "node_exporter-$NODE_EXPORTER_VERSION.linux-amd64/node_exporter:node_exporter"

# Traefik (harness edge only)
fetch_tar traefik "$TRAEFIK_VERSION" "$GH/traefik/traefik/releases/download/$TRAEFIK_VERSION" \
  "traefik_${TRAEFIK_VERSION}_linux_amd64.tar.gz" "traefik_${TRAEFIK_VERSION}_checksums.txt" traefik:traefik

# ruff (publishes one .sha256 file per asset)
fetch_tar ruff "$RUFF_VERSION" "$GH/astral-sh/ruff/releases/download/$RUFF_VERSION" \
  ruff-x86_64-unknown-linux-gnu.tar.gz ruff-x86_64-unknown-linux-gnu.tar.gz.sha256 \
  ruff-x86_64-unknown-linux-gnu/ruff:ruff

# ShellCheck binary (no checksum file upstream: pinned hash in tools/versions.env)
if ! is_installed shellcheck "$SHELLCHECK_VERSION"; then
  asset="shellcheck-$SHELLCHECK_VERSION.linux.x86_64.tar.gz"
  download "$GH/koalaman/shellcheck/releases/download/$SHELLCHECK_VERSION/$asset" "$WORK/$asset"
  verify_sha "$WORK/$asset" "$SHELLCHECK_SHA256"
  rm -rf "${WORK:?}/shellcheck" && mkdir -p "$WORK/shellcheck"
  tar -xzf "$WORK/$asset" -C "$WORK/shellcheck"
  install -m 0755 "$WORK/shellcheck/shellcheck-$SHELLCHECK_VERSION/shellcheck" "$BIN/shellcheck"
  mark_installed shellcheck "$SHELLCHECK_VERSION"
fi

# BusyBox, to test config-guard under busybox semantics (no checksum file upstream: pinned hash).
# Every applet gets a relative symlink in busybox-applets/, used as the only PATH entry.
if ! is_installed busybox "$BUSYBOX_VERSION"; then
  download "https://busybox.net/downloads/binaries/$BUSYBOX_VERSION-x86_64-linux-musl/busybox" "$WORK/busybox"
  verify_sha "$WORK/busybox" "$BUSYBOX_SHA256"
  install -m 0755 "$WORK/busybox" "$BIN/busybox"
  rm -rf "${BIN:?}/busybox-applets" && mkdir -p "$BIN/busybox-applets"
  for applet in $("$BIN/busybox" --list); do
    ln -s ../busybox "$BIN/busybox-applets/$applet"
  done
  mark_installed busybox "$BUSYBOX_VERSION"
fi

# Grafana OSS (harness only; the .sha256 file holds the bare hash)
if ! is_installed grafana "$GRAFANA_VERSION"; then
  asset="grafana-$GRAFANA_VERSION.linux-amd64.tar.gz"
  base="https://dl.grafana.com/oss/release"
  download "$base/$asset" "$WORK/$asset"
  download "$base/$asset.sha256" "$WORK/grafana.sha256"
  verify_sha "$WORK/$asset" "$(tr -d '[:space:]' < "$WORK/grafana.sha256")"
  rm -rf "${BIN:?}/grafana" "${WORK:?}/grafana" && mkdir -p "$WORK/grafana"
  tar -xzf "$WORK/$asset" -C "$WORK/grafana"
  mv "$WORK/grafana/grafana-$GRAFANA_VERSION" "$BIN/grafana"
  mark_installed grafana "$GRAFANA_VERSION"
fi

rm -rf "$WORK"
echo "fetch-binaries: all binaries installed in $BIN"
