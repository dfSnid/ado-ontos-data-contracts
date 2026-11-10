#!/usr/bin/env bash
# Deploy Ontos as a Databricks App and let the publisher identity use it. Safe to re-run.
#
# Ontos is the registration and display copy for Tier 2 contracts. The publish pipeline writes
# to it as the publisher identity, which reaches Ontos through the dp-publishers group mapped
# to its own "Contract Publisher" role.
#
# Usage:
#   PROFILE=<workspace-profile> OAUTH_PROFILE=<workspace-oauth-profile> ACCOUNT_PROFILE=<account-profile> \
#   ONTOS_SRC=<ontos checkout>/src [ENVIRONMENT=dev] [FORCE_DEPLOY=1] ./ontos.sh
#
# Reads from environments/<ENVIRONMENT>.yml: warehouseId, workspaceId, publisherClientId,
# ontosAppName, ontosLakebaseProject and ontosAppCatalog; the publisher group and the role
# mapping come from platform.yaml `ontos.roles`. Put the URL it prints into ontosUrl.
#
# ONTOS_SRC is the src/ folder of an Ontos v1.0.2 checkout with the frontend already built
# (src/scripts/build_static.sh, which fills src/backend/static). OAUTH_PROFILE must be a
# U2M OAuth login for an Ontos admin (workspace "admins" group): the app proxy rejects Entra
# tokens, and only an admin can change Ontos roles.
set -euo pipefail

: "${PROFILE:?set PROFILE to the workspace CLI profile}"
: "${OAUTH_PROFILE:?set OAUTH_PROFILE to a workspace OAuth (U2M) profile}"
: "${ACCOUNT_PROFILE:?set ACCOUNT_PROFILE to the account CLI profile}"
: "${ONTOS_SRC:?set ONTOS_SRC to the src folder of an Ontos v1.0.2 checkout}"
. "$(dirname "$0")/lib.sh"
WAREHOUSE_ID=$(cfg env.warehouseId)
WORKSPACE_ID=$(cfg env.workspaceId)
PUBLISHER_APP_ID=$(cfg env.publisherClientId)
APP_NAME=$(cfg env.ontosAppName)
LAKEBASE_PROJECT=$(cfg env.ontosLakebaseProject)
APP_CATALOG=$(cfg env.ontosAppCatalog)
PUBLISHERS_GROUP=$(cfg ontos.publisher_role.group)
BRANCH="projects/$LAKEBASE_PROJECT/branches/production"
STAGE="$(cd "$SETUP_DIR/../.." && pwd)/ontos-app"
sql() { databricks experimental aitools tools query "$1" --warehouse "$WAREHOUSE_ID" --profile "$PROFILE" >/dev/null; }

[ -f "$ONTOS_SRC/app.yaml" ] && [ -d "$ONTOS_SRC/backend/static" ] || {
  log "ONTOS_SRC needs app.yaml and a built backend/static (run src/scripts/build_static.sh)"; exit 1; }

# 1. Lakebase Autoscaling project for the Ontos metadata store.
log "== Lakebase project $LAKEBASE_PROJECT"
if databricks postgres get-project "projects/$LAKEBASE_PROJECT" --profile "$PROFILE" >/dev/null 2>&1; then
  log "  exists"
else
  databricks postgres create-project "$LAKEBASE_PROJECT" --profile "$PROFILE" \
    --json '{"spec": {"display_name": "Ontos metadata (dp-contracts)"}}' >/dev/null
  log "  created"
fi
DATABASE=$(databricks postgres list-databases "$BRANCH" --profile "$PROFILE" -o json | json '
(d if isinstance(d, list) else d.get("databases", []))[0]["name"]')

# 2. Unity Catalog volume for uploaded files.
log "== Volume $APP_CATALOG.app_ontos.app_files"
sql "CREATE CATALOG IF NOT EXISTS $APP_CATALOG COMMENT 'Ontos app files (dp-contracts)'"
sql "CREATE SCHEMA IF NOT EXISTS $APP_CATALOG.app_ontos"
sql "CREATE VOLUME IF NOT EXISTS $APP_CATALOG.app_ontos.app_files"

# 3. Stage the app source. Drop package.json, otherwise the Apps build runs npm against a
#    frontend we do not ship. Ask Ontos (LLM) is off, so no serving endpoint is needed.
log "== Stage $STAGE"
rm -rf "$STAGE" && mkdir -p "$STAGE"
rsync -a --exclude frontend --exclude node_modules --exclude e2e --exclude 'backend/tests' \
  --exclude '__pycache__' --exclude '*.map' --exclude databricks.yaml --exclude package.json \
  "$ONTOS_SRC/" "$STAGE/"
python3 -I - "$STAGE/app.yaml" "$BRANCH" <<'PY'
import sys
path, branch = sys.argv[1:]
s = open(path).read()
s = s.replace('  - name: "LLM_ENABLED"\n    value: "True"\n  - name: "LLM_ENDPOINT"\n    valueFrom: "serving-endpoint"',
              '  # Ask Ontos (LLM) off, so no serving endpoint resource is needed.\n'
              '  - name: "LLM_ENABLED"\n    value: "False"')
s = s.replace('value: "projects/ontos-db/branches/production"', f'value: "{branch}"')
open(path, "w").write(s)
PY

# 4. The app, with its Lakebase, warehouse and volume resources.
log "== App $APP_NAME"
if databricks apps get "$APP_NAME" --profile "$PROFILE" >/dev/null 2>&1; then
  log "  exists"
else
  SPEC=$(python3 -I - "$APP_NAME" "$BRANCH" "$DATABASE" "$WAREHOUSE_ID" "$APP_CATALOG" <<'PY'
import json, sys
name, branch, database, warehouse, catalog = sys.argv[1:]
print(json.dumps({
    "name": name, "description": "Ontos v1.0.2: data contract registry (dp-contracts)",
    "resources": [
        {"name": "postgres", "postgres": {"branch": branch, "database": database,
                                          "permission": "CAN_CONNECT_AND_CREATE"}},
        {"name": "sql-warehouse", "sql_warehouse": {"id": warehouse, "permission": "CAN_USE"}},
        {"name": "volume", "uc_securable": {"securable_full_name": f"{catalog}.app_ontos.app_files",
                                            "securable_type": "VOLUME", "permission": "WRITE_VOLUME"}}],
    "user_api_scopes": ["sql", "files.files", "dashboards.genie", "catalog.connections",
                        "catalog.catalogs:read", "catalog.schemas:read", "catalog.tables:read"],
}))
PY
)
  databricks apps create --json "$SPEC" --profile "$PROFILE" >/dev/null
  log "  created"
fi

# 5. Deploy when there is no successful deployment yet (or FORCE_DEPLOY=1).
STATE=$(databricks apps get "$APP_NAME" --profile "$PROFILE" -o json | json 'd.get("active_deployment", {}).get("status", {}).get("state", "")')
if [ "$STATE" != "SUCCEEDED" ] || [ "${FORCE_DEPLOY:-}" = "1" ]; then
  ME=$(databricks current-user me --profile "$PROFILE" -o json | json 'd["userName"]')
  WS="/Workspace/Users/$ME/apps/$APP_NAME"
  log "== Deploy from $WS"
  databricks sync "$STAGE" "$WS" --full --profile "$PROFILE" >/dev/null
  databricks apps deploy "$APP_NAME" --source-code-path "$WS" --profile "$PROFILE" >/dev/null
else
  log "== Deploy: already running"
fi
APP_URL=$(databricks apps get "$APP_NAME" --profile "$PROFILE" -o json | json 'd["url"]')

# 6. Publisher identity: CAN USE on the app, and membership of the dp-publishers group.
log "== Publisher access"
databricks permissions update apps "$APP_NAME" --profile "$PROFILE" --json \
  "{\"access_control_list\":[{\"service_principal_name\":\"$PUBLISHER_APP_ID\",\"permission_level\":\"CAN_USE\"}]}" >/dev/null
SP_ID=$(databricks account service-principals list --filter "applicationId eq \"$PUBLISHER_APP_ID\"" \
        --profile "$ACCOUNT_PROFILE" -o json | json 'd[0]["id"]')
GROUP_ID=$(databricks account groups list --filter "displayName eq \"$PUBLISHERS_GROUP\"" \
           --profile "$ACCOUNT_PROFILE" -o json | json 'd[0]["id"] if d else ""')
[ -n "$GROUP_ID" ] || GROUP_ID=$(databricks account groups create --display-name "$PUBLISHERS_GROUP" \
                                 --profile "$ACCOUNT_PROFILE" -o json | json 'd["id"]')
databricks account groups patch "$GROUP_ID" --profile "$ACCOUNT_PROFILE" --json \
  "{\"schemas\":[\"urn:ietf:params:scim:api:messages:2.0:PatchOp\"],\"Operations\":[{\"op\":\"add\",\"path\":\"members\",\"value\":[{\"value\":\"$SP_ID\"}]}]}"
databricks account workspace-assignment update "$WORKSPACE_ID" "$GROUP_ID" \
  --json '{"permissions":["USER"]}' --profile "$ACCOUNT_PROFILE" >/dev/null

# 7. Ontos roles, from platform.yaml `ontos.roles` (dpi ontos-roles). Needs an Ontos admin,
#    hence the OAuth profile.
log "== Ontos roles"
DP_ONTOS_URL="$APP_URL" "$DPI" ontos-roles --profile "$OAUTH_PROFILE"

log "== Done. Ontos at $APP_URL"
printf '%s\n' "$APP_URL"
