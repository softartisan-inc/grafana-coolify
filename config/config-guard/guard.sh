#!/bin/sh
# config-guard (spec 4.4): refuse to start the stack when a config file written by Coolify is
# missing, empty, a directory, or differs from its source (SHA-256), or when a runtime variable
# used inside a config would break it. POSIX sh + busybox (alpine).
#
# CONFIG_GUARD_EXPECTED="path=sha256;path=sha256;..." is computed by scripts/render.py.
set -u
set -f

failed=0
# A literal newline: grep matches line by line, so a multi-line value must be rejected first.
NL=$(printf '\n.')
NL=${NL%.}

error() {
  printf 'config-guard: ERROR: %s\n' "$*" >&2
  failed=1
}

matches() { # value extended_regex; the whole value must be one matching line
  case $1 in
    *"$NL"*) return 1 ;;
  esac
  printf '%s\n' "$1" | grep -Eq "$2"
}

check_file() { # path expected_sha256
  if [ -d "$1" ]; then
    error "$1: is a directory (Coolify file-mount regression)"
  elif [ ! -e "$1" ]; then
    error "$1: missing"
  elif [ ! -f "$1" ]; then
    error "$1: not a regular file"
  elif [ ! -s "$1" ]; then
    error "$1: empty"
  else
    actual=$(sha256sum "$1" | cut -d' ' -f1)
    if [ "$actual" = "$2" ]; then
      printf 'config-guard: ok %s\n' "$1"
    else
      error "$1: sha256 $actual differs from source $2"
    fi
  fi
}

expected=${CONFIG_GUARD_EXPECTED:-}
if [ -z "$expected" ]; then
  error "CONFIG_GUARD_EXPECTED is empty"
fi
IFS=';'
for entry in $expected; do
  [ -n "$entry" ] || continue
  path=${entry%%=*}
  sha=${entry#*=}
  if [ "$path" = "$entry" ] || ! matches "$sha" '^[0-9a-f]{64}$'; then
    error "malformed CONFIG_GUARD_EXPECTED entry: $entry"
    continue
  fi
  check_file "$path" "$sha"
done
unset IFS

# The salt is spliced into an OTTL statement and a Go template: letters and digits only.
if ! matches "${IP_HASH_SALT:-}" '^[A-Za-z0-9]{16,}$'; then
  error "IP_HASH_SALT must be at least 16 characters from [A-Za-z0-9]"
fi
# alloy always runs faro.receiver, and an empty api_key disables its check: the key is mandatory.
if ! matches "${FARO_API_KEY:-}" '^[A-Za-z0-9._~+/=-]{16,}$'; then
  error "FARO_API_KEY must be at least 16 characters from [A-Za-z0-9._~+/=-] (openssl rand -hex 24)"
fi
# PROJECTS is split on commas by a Go template (Faro allow-list): no spaces, no empty item.
# Each item: ^[a-z0-9][a-z0-9-]{0,63}$, the rule of grafana-setup (no leading hyphen).
projects=${PROJECTS:-}
if [ -n "$projects" ] && ! matches "$projects" '^[a-z0-9][a-z0-9-]{0,63}(,[a-z0-9][a-z0-9-]{0,63})*$'; then
  error "PROJECTS must look like in-immo,other-project (lowercase, comma separated, no spaces)"
fi
# FARO_SERVICES (Faro service allow-list) is spliced into an OTTL regex and a Go template: same
# rule as PROJECTS.
faro_services=${FARO_SERVICES:-}
if [ -n "$faro_services" ] && ! matches "$faro_services" '^[a-z0-9][a-z0-9-]{0,63}(,[a-z0-9][a-z0-9-]{0,63})*$'; then
  error "FARO_SERVICES must look like web-app,desktop (lowercase, comma separated, no spaces)"
fi
host_map=${HOST_MAP:-}
if [ -n "$host_map" ] && ! matches "$host_map" '^[a-z0-9.-]+=[a-z0-9-]+:(prod|preprod)(,[a-z0-9.-]+=[a-z0-9-]+:(prod|preprod))*$'; then
  error "HOST_MAP must look like host=service:env[,host=service:env...] (lowercase, env prod or preprod)"
fi
reserved=${RESERVED_SUBDOMAINS:-}
if [ -n "$reserved" ] && ! matches "$reserved" '^[a-z0-9-]+(,[a-z0-9-]+)*$'; then
  error "RESERVED_SUBDOMAINS must look like www,api (lowercase, comma separated)"
fi
# env label of the host-level alerts (grafana-setup): same values as the env of the data.
case ${HOST_ENV:-prod} in
  prod | preprod) ;;
  *) error "HOST_ENV must be prod or preprod" ;;
esac
# Retentions and limits: empty takes the default where the value is consumed (Coolify passes an
# emptied variable as "", spike S5); a set value must be well formed and never 0, which Tempo
# reads as "delete every block" (retention) or "no limit" (series).
check_set() { # name value extended_regex message
  if [ -n "$2" ] && { ! matches "$2" "$3" || matches "$2" '^(0+[a-zA-Z]*)+$'; }; then
    error "$1 $4"
  fi
}
check_set TEMPO_RETENTION "${TEMPO_RETENTION:-}" '^([0-9]+(h|m|s))+$' "must be a non-zero Go duration such as 168h (empty: 168h)"
check_set TEMPO_MAX_ACTIVE_SERIES "${TEMPO_MAX_ACTIVE_SERIES:-}" '^[0-9]+$' "must be a positive integer, 0 means no limit (empty: 100000)"
# Loki refuses a retention below 24h at startup ("retention period must be >= 24h") and restarts
# in a loop: a well-formed Loki retention must also add up to at least one day.
at_least_one_day() { # duration already matching ^([0-9]+(y|w|d|h|m|s))+$
  rest=$1
  total=0
  while [ -n "$rest" ]; do
    num=${rest%%[!0-9]*}
    rest=${rest#"$num"}
    unit=${rest%"${rest#?}"}
    rest=${rest#?}
    # Leading zeros would make $(( )) read the number as octal.
    while :; do
      case $num in
        0?*) num=${num#0} ;;
        *) break ;;
      esac
    done
    # 10 digits or more is at least 1e9 seconds whatever the unit (and would overflow below).
    [ "${#num}" -ge 10 ] && return 0
    case $unit in
      y) mult=31536000 ;;
      w) mult=604800 ;;
      d) mult=86400 ;;
      h) mult=3600 ;;
      m) mult=60 ;;
      *) mult=1 ;;
    esac
    total=$((total + num * mult))
    [ "$total" -ge 86400 ] && return 0
  done
  return 1
}
check_loki_retention() { # name value example
  if [ -n "$2" ] && { ! matches "$2" '^([0-9]+(y|w|d|h|m|s))+$' || ! at_least_one_day "$2"; }; then
    error "$1 must be a duration of at least 24h such as $3 (empty: $3)"
  fi
}
check_loki_retention LOKI_RETENTION_PROD "${LOKI_RETENTION_PROD:-}" 720h
check_loki_retention LOKI_RETENTION_DEFAULT "${LOKI_RETENTION_DEFAULT:-}" 168h
check_set PROM_RETENTION_TIME "${PROM_RETENTION_TIME:-}" '^([0-9]+(y|w|d|h|m|s))+$' "must be a non-zero duration such as 90d (empty: 90d)"
check_set PROM_RETENTION_SIZE "${PROM_RETENTION_SIZE:-}" '^[0-9]+(B|KB|MB|GB|TB|PB|KiB|MiB|GiB|TiB|PiB)$' "must be a non-zero size such as 100GB (empty: 100GB)"
case ${ENABLE_EXEMPLARS:-false} in
  true | false) ;;
  *) error "ENABLE_EXEMPLARS must be true or false (empty: false)" ;;
esac
tenant_regex=${TENANT_HOST_REGEX:-}
if [ -n "$tenant_regex" ]; then
  case $tenant_regex in
    *"$NL"*) error "TENANT_HOST_REGEX must be a single line" ;;
    *'(?P<sub>'*) ;;
    *) error "TENANT_HOST_REGEX must define the named group (?P<sub>...)" ;;
  esac
fi

if [ "$failed" -ne 0 ]; then
  echo "config-guard: FAILED - no service will start" >&2
  exit 1
fi
echo "config-guard: all checks passed"
