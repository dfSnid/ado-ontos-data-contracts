# Sourced by the setup scripts. Settings come from config/ through the dpi command, so the
# scripts and the pipelines read the same files. Only the CLI profiles are passed in.
#   ENVIRONMENT  environments/<name>.yml to use (default dev)
#   DPI          the dpi command (default: dpi on PATH; install with pip install <dp-integration>)
SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENVIRONMENT="${ENVIRONMENT:-dev}"
DPI="${DPI:-dpi}"
export DP_CONFIG_DIR="${DP_CONFIG_DIR:-$(cd "$SETUP_DIR/../config" && pwd)}"
export DP_ENVIRONMENT="$ENVIRONMENT"

log() { printf '%s\n' "$*" >&2; }
json() { python3 -I -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
# cfg env.warehouseId, cfg setup.groups (one per line). An env value that is empty or still a
# <placeholder> exits 2 with the name to fill in, so a script stops before it changes anything.
cfg() { "$DPI" config get "$1"; }

command -v "$DPI" >/dev/null 2>&1 || { log "dpi not found: pip install the dp-integration repository, or set DPI"; exit 1; }
"$DPI" config get env.databricksHost >/dev/null || exit 2   # stop early on a missing or invalid setting
