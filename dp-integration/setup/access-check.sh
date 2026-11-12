#!/usr/bin/env bash
# Install check (scenario 9 in docs/12-verify-the-install.md): the consumer group can query a
# published object, a group that is not in the access file cannot. Safe to re-run.
#
# One Databricks-managed test service principal per group (dp-test-consumer in
# dp-sales-consumers, dp-test-other-analyst in dp-other-analysts). Both groups can use the
# warehouse, so the only difference is the Unity Catalog grant from the publish job. Each check
# uses a short-lived OAuth secret that is deleted straight after the query.
#
# Usage:
#   PROFILE=<workspace-profile> ACCOUNT_PROFILE=<account-profile> [ENVIRONMENT=dev] \
#   [TABLE=<catalog>.orders.orders] [ALLOWED_GROUP=<first Data Consumer group>] \
#   [OTHER_GROUP=dp-other-analysts] ./access-check.sh
set -euo pipefail

: "${PROFILE:?set PROFILE to the workspace CLI profile}"
: "${ACCOUNT_PROFILE:?set ACCOUNT_PROFILE to the account CLI profile}"
. "$(dirname "$0")/lib.sh"
WAREHOUSE_ID=$(cfg env.warehouseId)
WORKSPACE_ID=$(cfg env.workspaceId)
TABLE="${TABLE:-$(cfg env.demoCatalog).orders.orders}"
ALLOWED_GROUP="${ALLOWED_GROUP:-$(cfg "ontos.role_groups.Data Consumer" | head -1)}"
OTHER_GROUP="${OTHER_GROUP:-dp-other-analysts}"
HOST=$(cfg env.workspaceUrl)

check() {  # check <sp display name> <group>
  local name=$1 group=$2 sp_id app_id group_id secret_id secret result
  sp_id=$(databricks account service-principals list --filter "displayName eq \"$name\"" \
          --profile "$ACCOUNT_PROFILE" -o json | json 'd[0]["id"] if d else ""')
  [ -n "$sp_id" ] || sp_id=$(databricks account service-principals create --display-name "$name" \
                             --profile "$ACCOUNT_PROFILE" -o json | json 'd["id"]')
  app_id=$(databricks account service-principals get "$sp_id" --profile "$ACCOUNT_PROFILE" -o json | json 'd["applicationId"]')
  group_id=$(databricks account groups list --filter "displayName eq \"$group\"" \
             --profile "$ACCOUNT_PROFILE" -o json | json 'd[0]["id"]')
  databricks account groups patch "$group_id" --profile "$ACCOUNT_PROFILE" --json \
    "{\"schemas\":[\"urn:ietf:params:scim:api:messages:2.0:PatchOp\"],\"Operations\":[{\"op\":\"add\",\"path\":\"members\",\"value\":[{\"value\":\"$sp_id\"}]}]}"
  databricks account workspace-assignment update "$WORKSPACE_ID" "$sp_id" \
    --json '{"permissions":["USER"]}' --profile "$ACCOUNT_PROFILE" >/dev/null
  databricks service-principals patch "$sp_id" --profile "$PROFILE" --json \
    '{"schemas":["urn:ietf:params:scim:api:messages:2.0:PatchOp"],"Operations":[{"op":"add","path":"entitlements","value":[{"value":"databricks-sql-access"}]}]}' >/dev/null
  databricks permissions update warehouses "$WAREHOUSE_ID" --profile "$PROFILE" \
    --json "{\"access_control_list\":[{\"group_name\":\"$group\",\"permission_level\":\"CAN_USE\"}]}" >/dev/null

  read -r secret_id secret < <(databricks account service-principal-secrets create "$sp_id" \
    --profile "$ACCOUNT_PROFILE" -o json | json 'd["id"] + " " + d["secret"]')
  result=$( (
    token=$(curl -sf -u "$app_id:$secret" -d 'grant_type=client_credentials&scope=all-apis' \
            "$HOST/oidc/v1/token" | json 'd["access_token"]')
    curl -s -H "Authorization: Bearer $token" -H "Content-Type: application/json" "$HOST/api/2.0/sql/statements" \
      -d "{\"warehouse_id\":\"$WAREHOUSE_ID\",\"statement\":\"SELECT count(*) FROM $TABLE\",\"wait_timeout\":\"50s\"}"
  ) || true)
  databricks account service-principal-secrets delete "$sp_id" "$secret_id" --profile "$ACCOUNT_PROFILE"
  printf '%s' "$result" | python3 -I -c "
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    d = {}
s = d.get('status', {})
if s.get('state') == 'SUCCEEDED':
    print('$name ($group): can query, ' + d['result']['data_array'][0][0] + ' rows')
else:
    print('$name ($group): refused, ' + str((s.get('error') or d).get('message', d))[:160])"
}

log "== $TABLE"
check dp-test-consumer "$ALLOWED_GROUP"
check dp-test-other-analyst "$OTHER_GROUP"
