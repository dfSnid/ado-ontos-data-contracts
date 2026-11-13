# Prerequisites and decisions

## Platforms

| Need | Detail |
|---|---|
| Azure subscription and resource group | For the publisher identity. The engineer running `setup/publisher-identity.sh` needs Contributor and User Access Administrator on the resource group. |
| Entra ID tenant | The Azure DevOps organisation must be connected to the same tenant. |
| Azure Databricks workspace | Premium, with a Unity Catalog metastore, a serverless SQL warehouse, Databricks Apps and Lakebase Autoscaling. |
| Azure DevOps | One organisation and one project for the three repositories. Microsoft-hosted agents, or a self-hosted pool that can reach the workspace, the Ontos app URL, PyPI (or your package mirror) and GitHub releases (the bundle deploy downloads the Databricks CLI). |
| Catalogs | One Unity Catalog catalog per environment for each domain, for example `sales_dev` (dev) and `sales_prod` (prod). The platform team creates them. |

## People and rights

| Role | Needed for |
|---|---|
| Databricks account admin | Account groups, the publisher service principal, tag-policy ASSIGN rights |
| Databricks workspace admin | Tag policies, catalogs, the Ontos app; must be in the workspace `admins` group to map Ontos roles |
| Azure resource group owner | The managed identity, its Reader role and its federated credential |
| Azure DevOps project admin | Repositories, pipelines, service connection, Environments, branch policies, groups |
| Platform owner | Reviews changes to the domain registry (`domains/`) and approves prod bundle deploys |
| Data Steward (per product) | Approves contract and product file pull requests |

## Tools on the engineer's machine

| Tool | Version used | Notes |
|---|---|---|
| Azure CLI | 2.x | With the `azure-devops` extension: `az extension add --name azure-devops` |
| Databricks CLI | 1.18 (0.294 or later) | Needs the `postgres`, `tag-policies` and `apps` command groups |
| Python | 3.12 (3.11 or later) | For the `dpi` command |
| Node.js and npm | Current LTS | Only to build the Ontos frontend once |
| git, rsync, zip, curl | Any |  |

## Databricks CLI profiles

Every setup script takes the profiles as environment variables. It never picks one for you.

| Variable | Profile | How to create it |
|---|---|---|
| `PROFILE` | The workspace, any auth (Azure CLI auth is fine) | `databricks auth login --host https://<workspace-host> --profile <name>` |
| `ACCOUNT_PROFILE` | The Databricks account (account admin) | `databricks auth login --host https://accounts.azuredatabricks.net --account-id <account-id> --profile <name>` |
| `OAUTH_PROFILE` | The workspace, Databricks OAuth (U2M) login of a workspace admin | As for `PROFILE`. It must be OAuth: the Apps proxy in front of Ontos rejects Entra tokens |

## Decisions to make first

Each decision maps to one setting, so you can change your mind later without changing code.

| # | Decision | Setting | Reference setup | Recommendation |
|---|---|---|---|---|
| 1 | Tag key names | `tags.prefix` in `platform.yaml` | `dp_`, so the keys never clash with tags the account already has | Empty prefix in an account you own: `tier`, `domain`, `status`, `sensitivity` |
| 2 | Who grants USE CATALOG and USE SCHEMA | `grants.parent_use` | `true`: the publisher adds them, so it needs MANAGE on the catalog | Either works. `false` keeps the publisher narrower if the platform team grants USE when it creates each domain catalog |
| 3 | Declarative table grants | `grants.revoke_unlisted` | `true` | Keep `true`: the access file is the only source of grants. Nobody grants by hand |
| 4 | Where the groups come from | `setup.groups` | Created by `bootstrap.sh` | Sync them from Entra ID and leave `setup.groups` empty |
| 5 | Environments | `config/environments/<env>.yml`, `environments` and `ontosEnvironment` in `pipelines/publish.yml` | `dev` and `prod`: two catalogs in one workspace, one publisher identity; Ontos synced in prod | One file per environment, one publisher identity and service connection per environment |
| 6 | Build agents | `agentPool`, `vmImage` | Microsoft-hosted | Self-hosted if your network rules require it |
| 7 | Self-approval | Branch policy | Allowed, so one person can try the whole flow | Off: the Producer and the Steward are different people |
| 8 | Domains | `domains/domains.yaml`, `tags.domain_values` and the mandatory-field schema | `sales`, registered and allowed | Your domain list |
| 9 | Who deploys product bundles | `publisherDeploysBundles` | `'true'`: the publisher identity also deploys | A separate deploy identity, so the identity that sets grants and tags does not also run product code |
| 10 | Certification levels | `certificationLevel` in each product file | Silver on the example product | Agree what Bronze, Silver and Gold mean before the first product goes active |
| 11 | Governance tiers | `tags.tier_values` | `2: domain` only: the Stewards' approval is the sign-off | Add `1: enterprise` only once you have a Business Data Owner sign-off for enterprise products |
