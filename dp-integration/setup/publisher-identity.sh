#!/usr/bin/env bash
# Create the publisher identity for the contract pipelines. Safe to re-run.
#
# A user-assigned managed identity, trusted by Azure DevOps through workload identity
# federation, so no secret is stored anywhere. The identity is added to Databricks as a
# service principal and given only what the publish and quality steps need.
#
# Usage:
#   PROFILE=<workspace-profile> ACCOUNT_PROFILE=<account-profile> [ENVIRONMENT=dev] \
#   [PIPELINE_IDS="1 2 3"] ./publisher-identity.sh
#
# Reads from environments/<ENVIRONMENT>.yml: serviceConnection, warehouseId, workspaceId,
# databricksAccountId, azureResourceGroup, adoOrganisation, adoProject, publisherIdentityName
# publisherCatalogs and publisherDeploysBundles. Put the client id it prints into publisherClientId.
# With publisherDeploysBundles 'true' the identity also deploys data product bundles
# (templates/bundle-deploy.yml): workspace access, and CREATE SCHEMA on the catalogs. That is a
# A shortcut for a trial; production gives bundle deploys their own identity.
#
# Needs: az CLI logged in to the tenant with Contributor and User Access Administrator
# on the resource group, the azure-devops extension, and a Databricks account admin.
set -euo pipefail

: "${PROFILE:?set PROFILE to the workspace CLI profile}"
: "${ACCOUNT_PROFILE:?set ACCOUNT_PROFILE to the account CLI profile}"
. "$(dirname "$0")/lib.sh"
WAREHOUSE_ID=$(cfg env.warehouseId)
WORKSPACE_ID=$(cfg env.workspaceId)
ACCOUNT_ID=$(cfg env.databricksAccountId)
RESOURCE_GROUP=$(cfg env.azureResourceGroup)
ADO_ORG=$(cfg env.adoOrganisation)
ADO_PROJECT=$(cfg env.adoProject)
IDENTITY_NAME=$(cfg env.publisherIdentityName)
SERVICE_CONNECTION=$(cfg env.serviceConnection)
CATALOGS=$(cfg env.publisherCatalogs)
DEPLOYS_BUNDLES=$(cfg env.publisherDeploysBundles 2>/dev/null || echo false)
PIPELINE_IDS="${PIPELINE_IDS:-}"
ORG_URL="https://dev.azure.com/$ADO_ORG"
# Application id of Azure DevOps in Entra ID, the same in every tenant.
ADO_RESOURCE="499b84ac-1321-427f-aa17-267ca6975798"
ado() {  # ado <method> <path> [body]: Azure DevOps REST with an Entra token
  local token
  token=$(az account get-access-token --resource "$ADO_RESOURCE" --query accessToken -o tsv)
  curl -sf -X "$1" -H "Authorization: Bearer $token" -H "Content-Type: application/json" \
    ${3:+-d "$3"} "$ORG_URL/$2"
}

SUBSCRIPTION_ID=$(az account show --query id -o tsv)
SUBSCRIPTION_NAME=$(az account show --query name -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)

# 1. Managed identity. Reader on its own resource group only, so the AzureCLI task can
#    select the subscription; it gets no other Azure rights.
log "== Managed identity $IDENTITY_NAME"
MI=$(az identity show -g "$RESOURCE_GROUP" -n "$IDENTITY_NAME" -o json 2>/dev/null) || \
  MI=$(az identity create -g "$RESOURCE_GROUP" -n "$IDENTITY_NAME" \
       --tags Purpose=dp-contracts -o json)
CLIENT_ID=$(printf '%s' "$MI" | json 'd["clientId"]')
PRINCIPAL_ID=$(printf '%s' "$MI" | json 'd["principalId"]')
RG_ID=$(az group show -n "$RESOURCE_GROUP" --query id -o tsv)
if [ -z "$(az role assignment list --assignee "$PRINCIPAL_ID" --scope "$RG_ID" --role Reader --query '[].id' -o tsv)" ]; then
  # A new identity can take a few seconds to replicate in Entra.
  for _ in 1 2 3 4 5 6; do
    az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
      --role Reader --scope "$RG_ID" >/dev/null 2>&1 && break
    sleep 10
  done
fi
log "  client id $CLIENT_ID"

# 2. ADO service connection (Azure Resource Manager, workload identity federation).
log "== Service connection $SERVICE_CONNECTION"
PROJECT_ID=$(az devops project show --organization "$ORG_URL" --project "$ADO_PROJECT" --query id -o tsv)
SC=$(az devops service-endpoint list --organization "$ORG_URL" --project "$ADO_PROJECT" \
     --query "[?name=='$SERVICE_CONNECTION'] | [0]" -o json)
if [ "$SC" = "null" ] || [ -z "$SC" ]; then
  CONFIG=$(mktemp)
  python3 -I - "$SERVICE_CONNECTION" "$TENANT_ID" "$CLIENT_ID" "$SUBSCRIPTION_ID" "$SUBSCRIPTION_NAME" \
    "$PROJECT_ID" "$ADO_PROJECT" > "$CONFIG" <<'PY'
import json, sys
name, tenant, client, sub, sub_name, proj_id, proj = sys.argv[1:]
print(json.dumps({
    "name": name, "type": "azurerm", "url": "https://management.azure.com/",
    "description": "Publisher identity for data contract pipelines (managed identity, no secret)",
    "authorization": {"scheme": "WorkloadIdentityFederation",
                      "parameters": {"tenantid": tenant, "serviceprincipalid": client}},
    "data": {"environment": "AzureCloud", "scopeLevel": "Subscription", "subscriptionId": sub,
             "subscriptionName": sub_name, "creationMode": "Manual", "identityType": "ManagedIdentity"},
    "isShared": False, "isReady": True,
    "serviceEndpointProjectReferences": [
        {"projectReference": {"id": proj_id, "name": proj}, "name": name}],
}))
PY
  SC=$(az devops service-endpoint create --organization "$ORG_URL" --project "$ADO_PROJECT" \
       --service-endpoint-configuration "$CONFIG" -o json)
  rm -f "$CONFIG"
  log "  created"
else
  log "  exists"
fi
SC_ID=$(printf '%s' "$SC" | json 'd["id"]')
ISSUER=$(printf '%s' "$SC" | json 'd["authorization"]["parameters"]["workloadIdentityFederationIssuer"]')
SUBJECT=$(printf '%s' "$SC" | json 'd["authorization"]["parameters"]["workloadIdentityFederationSubject"]')

# 3. Federated credential: the identity trusts tokens ADO issues for this connection only.
log "== Federated credential"
if [ -z "$(az identity federated-credential list -g "$RESOURCE_GROUP" --identity-name "$IDENTITY_NAME" \
          --query "[?subject=='$SUBJECT'].name" -o tsv)" ]; then
  az identity federated-credential create -g "$RESOURCE_GROUP" --identity-name "$IDENTITY_NAME" \
    --name "ado-$SERVICE_CONNECTION" --issuer "$ISSUER" --subject "$SUBJECT" \
    --audiences api://AzureADTokenExchange >/dev/null
  log "  created for $SUBJECT"
else
  log "  exists"
fi

# 4. Let the named pipelines use the connection without a manual "Permit" click.
for pid in $PIPELINE_IDS; do
  ado PATCH "$ADO_PROJECT/_apis/pipelines/pipelinePermissions/endpoint/$SC_ID?api-version=7.1-preview.1" \
    "{\"pipelines\":[{\"id\":$pid,\"authorized\":true}]}" >/dev/null
  log "  pipeline $pid authorised"
done

# 5. Databricks: account service principal, assigned to the workspace as USER.
log "== Databricks service principal"
SP_ID=$(databricks account service-principals list --filter "applicationId eq \"$CLIENT_ID\"" \
        --profile "$ACCOUNT_PROFILE" -o json | json 'd[0]["id"] if d else ""')
if [ -z "$SP_ID" ]; then
  SP_ID=$(databricks account service-principals create --application-id "$CLIENT_ID" \
          --display-name "$IDENTITY_NAME" --profile "$ACCOUNT_PROFILE" -o json | json 'd["id"]')
  log "  created ($SP_ID)"
else
  log "  exists ($SP_ID)"
fi
databricks account workspace-assignment update "$WORKSPACE_ID" "$SP_ID" \
  --json '{"permissions":["USER"]}' --profile "$ACCOUNT_PROFILE" >/dev/null
# SQL access only (no workspace UI access): enough for the warehouse and the REST APIs.
databricks service-principals patch "$SP_ID" --profile "$PROFILE" --json \
  '{"schemas":["urn:ietf:params:scim:api:messages:2.0:PatchOp"],"Operations":[{"op":"add","path":"entitlements","value":[{"value":"databricks-sql-access"}]}]}' >/dev/null

if [ "$DEPLOYS_BUNDLES" = "true" ]; then
  # A bundle deploy writes the bundle files to the workspace and creates a job.
  log "== Workspace access (bundle deploys)"
  databricks service-principals patch "$SP_ID" --profile "$PROFILE" --json \
    '{"schemas":["urn:ietf:params:scim:api:messages:2.0:PatchOp"],"Operations":[{"op":"add","path":"entitlements","value":[{"value":"workspace-access"}]}]}' >/dev/null
fi

# 6. Unity Catalog. MANAGE lets the publisher apply and revoke the grants in the access
#    file; APPLY TAG and MODIFY cover tags, comments and contract properties. A bundle deploy
#    also creates the product schema (the identity then owns it, so it can create tables in it).
PRIVILEGES="USE CATALOG, USE SCHEMA, SELECT, MODIFY, APPLY TAG, MANAGE"
[ "$DEPLOYS_BUNDLES" = "true" ] && PRIVILEGES="$PRIVILEGES, CREATE SCHEMA"
for catalog in $CATALOGS; do
  log "== Unity Catalog privileges on $catalog"
  databricks experimental aitools tools query \
    "GRANT $PRIVILEGES ON CATALOG \`$catalog\` TO \`$CLIENT_ID\`" \
    --warehouse "$WAREHOUSE_ID" --profile "$PROFILE" >/dev/null
done
databricks permissions update warehouses "$WAREHOUSE_ID" --profile "$PROFILE" \
  --json "{\"access_control_list\":[{\"service_principal_name\":\"$CLIENT_ID\",\"permission_level\":\"CAN_USE\"}]}" >/dev/null

# 7. ASSIGN on this setup's tag policies (roles/tagPolicy.assigner on each policy's rule set).
log "== Tag policy ASSIGN"
for key in tier domain status sensitivity; do
  tag=$("$DPI" config tag-key "$key")
  policy_id=$(databricks tag-policies get-tag-policy "$tag" --profile "$PROFILE" -o json | json 'd["id"]')
  name="accounts/$ACCOUNT_ID/tagPolicies/$policy_id/ruleSets/default"
  current=$(databricks account access-control get-rule-set "$name" "" --profile "$ACCOUNT_PROFILE" -o json)
  body=$(printf '%s' "$current" | python3 -I -c '
import json, sys
cur, name, principal = json.load(sys.stdin), sys.argv[1], sys.argv[2]
rules = cur.get("grant_rules", [])
rule = next((r for r in rules if r["role"] == "roles/tagPolicy.assigner"), None)
if rule and principal in rule.get("principals", []):
    sys.exit(0)
if rule:
    rule["principals"].append(principal)
else:
    rules.append({"role": "roles/tagPolicy.assigner", "principals": [principal]})
print(json.dumps({"name": name, "rule_set": {"name": name, "etag": cur["etag"], "grant_rules": rules}}))
' "$name" "servicePrincipals/$CLIENT_ID")
  if [ -n "$body" ]; then
    databricks account access-control update-rule-set --json "$body" --profile "$ACCOUNT_PROFILE" >/dev/null
    log "  $tag: granted"
  else
    log "  $tag: already granted"
  fi
done

log "== Done. Service connection '$SERVICE_CONNECTION', application id $CLIENT_ID"
printf '%s\n' "$CLIENT_ID"
