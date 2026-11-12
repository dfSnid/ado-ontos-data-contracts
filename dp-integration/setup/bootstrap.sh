#!/usr/bin/env bash
# Build the Unity Catalog baseline from scratch. Safe to re-run.
#
# Usage:
#   PROFILE=<workspace-profile> ACCOUNT_PROFILE=<account-profile> [ENVIRONMENT=dev] \
#   [OWNER_EMAIL=<user>] [SKIP_DEMO_CATALOG=1] ./bootstrap.sh
#
# Creates: the example catalog (bootstrap.sql, env.demoCatalog), the governed tag
# policies (keys and values from platform.yaml `tags`), and the account groups in
# platform.yaml `setup.groups`, assigned to the workspace. The catalog is created with SQL
# because a default-storage metastore rejects the catalogs REST API. OWNER_EMAIL, if set, is
# added to the groups mapped to the Data Producer and Data Steward roles.
#
# The default tag keys have a prefix (dp_tier, dp_domain, ...) to avoid a clash in an account that
# already has policies named domain, status and sensitivity. In an account you own, set tags.prefix to "".
set -euo pipefail

: "${PROFILE:?set PROFILE to the workspace CLI profile}"
: "${ACCOUNT_PROFILE:?set ACCOUNT_PROFILE to the account CLI profile}"
. "$(dirname "$0")/lib.sh"
WAREHOUSE_ID=$(cfg env.warehouseId)
WORKSPACE_ID=$(cfg env.workspaceId)
CATALOG=$(cfg env.demoCatalog)
OWNER_EMAIL="${OWNER_EMAIL:-}"
MARKER="dp-contracts"
HERE="$SETUP_DIR"

run_sql() {
  databricks experimental aitools tools query "$1" --warehouse "$WAREHOUSE_ID" --profile "$PROFILE" >/dev/null
}

# 1. The example catalog, one statement at a time (order matters).
if [ "${SKIP_DEMO_CATALOG:-}" = "1" ]; then
  log "== SQL bootstrap: skipped"
else
log "== SQL bootstrap"
python3 -I - "$HERE/bootstrap.sql" "$CATALOG" <<'PY' > "$HERE/.statements.tmp"
import re, sys
sql = open(sys.argv[1], encoding="utf-8").read().replace("sales_dev", sys.argv[2])
body = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))
for stmt in re.split(r";\s*\n", body):
    if stmt.strip():
        sys.stdout.write(stmt.strip() + "\0")
PY
while IFS= read -r -d '' stmt; do
  log "  $(printf '%s' "$stmt" | head -1 | cut -c1-80)"
  run_sql "$stmt"
done < "$HERE/.statements.tmp"
rm -f "$HERE/.statements.tmp"
fi

# 2. Governed tag policies. Only create or update policies this setup owns.
log "== Tag policies"
tag_policy() {  # tag_policy <logical tag> <description>
  local key desc="$2" values body existing
  key=$("$DPI" config tag-key "$1")
  values=$("$DPI" config tag-values "$1" | paste -sd, -)
  body=$(python3 -I -c 'import json,sys; print(json.dumps({"tag_key": sys.argv[1], "description": sys.argv[2], "values": [{"name": v} for v in sys.argv[3].split(",")]}))' "$key" "$desc [$MARKER]" "$values")
  if existing=$(databricks tag-policies get-tag-policy "$key" --profile "$PROFILE" -o json 2>/dev/null); then
    if printf '%s' "$existing" | grep -q "$MARKER"; then
      databricks tag-policies update-tag-policy "$key" "description,values" --json "$body" --profile "$PROFILE" >/dev/null
      log "  updated $key"
    else
      log "  SKIP $key: exists and is owned by someone else"
    fi
  else
    databricks tag-policies create-tag-policy --json "$body" --profile "$PROFILE" >/dev/null
    log "  created $key"
  fi
}
tag_policy tier        "Data product governance tier, from governanceTier in the contract"
tag_policy domain      "Owning business domain of the data product"
tag_policy status      "Data product lifecycle status, set by the publish job"
tag_policy sensitivity "Security classification from the data contract"

# 3. Account groups, assigned to the workspace with USER permission.
log "== Groups"
group_id() {
  databricks account groups list --filter "displayName eq \"$1\"" --profile "$ACCOUNT_PROFILE" -o json | python3 -I -c 'import json,sys; d=json.load(sys.stdin); print(d[0]["id"] if d else "")'
}
ensure_group() {
  local name="$1" gid
  gid=$(group_id "$name")
  if [ -z "$gid" ]; then
    gid=$(databricks account groups create --display-name "$name" --profile "$ACCOUNT_PROFILE" -o json | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["id"])')
    log "  created $name ($gid)"
  else
    log "  exists  $name ($gid)"
  fi
  databricks account workspace-assignment update "$WORKSPACE_ID" "$gid" --json '{"permissions":["USER"]}' --profile "$ACCOUNT_PROFILE" >/dev/null
  printf '%s' "$gid"
}
add_member() {
  local gid="$1" email="$2" uid
  uid=$(databricks account users list --filter "userName eq \"$email\"" --profile "$ACCOUNT_PROFILE" -o json | python3 -I -c 'import json,sys; d=json.load(sys.stdin); print(d[0]["id"] if d else "")')
  [ -n "$uid" ] || { log "  WARN user $email not found in account"; return; }
  databricks account groups patch "$gid" --profile "$ACCOUNT_PROFILE" --json "{\"schemas\":[\"urn:ietf:params:scim:api:messages:2.0:PatchOp\"],\"Operations\":[{\"op\":\"add\",\"path\":\"members\",\"value\":[{\"value\":\"$uid\"}]}]}" >/dev/null
  log "  added $email"
}

cfg setup.groups > "$HERE/.groups.tmp"
while IFS= read -r group; do
  [ -n "$group" ] && ensure_group "$group" >/dev/null
done < "$HERE/.groups.tmp"
rm -f "$HERE/.groups.tmp"
if [ -n "$OWNER_EMAIL" ]; then
  { cfg "ontos.role_groups.Data Producer"; cfg "ontos.role_groups.Data Steward"; } > "$HERE/.groups.tmp"
  while IFS= read -r group; do
    [ -n "$group" ] || continue
    gid=$(group_id "$group")
    if [ -n "$gid" ]; then add_member "$gid" "$OWNER_EMAIL"; else log "  WARN group $group not found: add it to setup.groups"; fi
  done < "$HERE/.groups.tmp"
  rm -f "$HERE/.groups.tmp"
fi

log "== Done"
