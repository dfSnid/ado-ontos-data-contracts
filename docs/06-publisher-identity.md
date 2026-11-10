# Publisher identity

## What this step does

`setup/publisher-identity.sh` creates the identity the pipelines run as. It is a user-assigned managed identity. Azure DevOps reaches it through a service connection with workload identity federation, so no secret exists anywhere. Databricks knows it as a service principal with only the rights publishing needs. The script is safe to re-run.

## Before you start

- `az login` to the right tenant and `az account set --subscription <subscription>`.
- You hold Contributor and User Access Administrator on `env.azureResourceGroup`.
- You are an Azure DevOps project admin, and a Databricks account and workspace admin.
- Step 4 is done: the tag policies exist.
- These keys are set: `serviceConnection`, `warehouseId`, `workspaceId`, `databricksAccountId`, `azureResourceGroup`, `adoOrganisation`, `adoProject`, `publisherIdentityName`, `publisherCatalogs`.

## Run it

```bash
cd dp-integration/setup
PROFILE=<workspace-profile> ACCOUNT_PROFILE=<account-profile> ENVIRONMENT=<env> ./publisher-identity.sh
```

The last line it prints is the client id. Put it in `publisherClientId` in your environment file and commit it. It is an identifier, not a secret.

Run it once per environment file (`ENVIRONMENT=prod` after the prod catalogs exist). With one identity for both, as in the reference setup, the second run only adds the prod catalogs' rights. Copy the same `publisherClientId` into `prod.yml`.

The pipelines do not exist yet, so leave `PIPELINE_IDS` unset now. [Pipelines in Azure DevOps](08-pipelines-in-azure-devops.md) re-runs this script with the pipeline ids to authorise them.

> [!IMPORTANT]
> With `publisherDeploysBundles: 'true'` the identity also deploys product bundles: it gets the `workspace-access` entitlement and CREATE SCHEMA on its catalogs. That is a shortcut for a trial. In production give bundle deploys their own identity and service connection, so the identity that sets grants and tags does not also run product code.

## What each part does and why

| Part | What | Why |
|---|---|---|
| 1. Managed identity | Created in the resource group, with Reader on that resource group only | The AzureCLI task signs in to a subscription, so the identity needs some role. Reader on one resource group is the smallest |
| 2. Service connection | Azure Resource Manager, workload identity federation, scheme `WorkloadIdentityFederation`, identity type `ManagedIdentity` | Azure DevOps issues a short-lived token for each job; nothing to rotate |
| 3. Federated credential | On the identity, trusting the issuer and subject Azure DevOps reports for this one connection | Only pipelines using this connection can act as the identity |
| 4. Pipeline authorisation | Optional. Pre-authorises the pipeline ids in `PIPELINE_IDS` | Saves the one-off Permit click on the first run |
| 5. Databricks service principal | Account service principal with the identity's client id, assigned to the workspace as USER, with the `databricks-sql-access` entitlement. `workspace-access` too when it deploys bundles | The Entra token works directly against Databricks; no workspace UI access is given unless it deploys |
| 6. Unity Catalog | On every catalog in `publisherCatalogs`: USE CATALOG, USE SCHEMA, SELECT, MODIFY, APPLY TAG and MANAGE, plus CREATE SCHEMA when it deploys bundles. CAN USE on the warehouse | SELECT for the quality and rule checks; MODIFY and APPLY TAG for comments, properties, tags and badges; MANAGE to apply and revoke the access-file grants |
| 7. Tag policy ASSIGN | The tag policy assigner role on each of the four policies | Without it Unity Catalog refuses to set a governed tag |

> [!IMPORTANT]
> If you set `grants.parent_use: false` and your platform team grants USE CATALOG and USE SCHEMA when it creates each domain catalog, you can drop MANAGE to the schema level. Keep it on anything whose table grants the access files control.

## Check it worked

- Azure DevOps: Project settings, Service connections shows `serviceConnection` as ready.
- Azure: the identity has one federated credential whose subject names the connection.
- Databricks: `databricks account service-principals list --filter 'applicationId eq "<client-id>"' --profile <account-profile>` returns one entry.
- The end-to-end check is the smoke pipeline on [Pipelines in Azure DevOps](08-pipelines-in-azure-devops.md).

## The script

Full listing: [`dp-integration/setup/publisher-identity.sh`](../dp-integration/setup/publisher-identity.sh)
